"""Tests du moteur Modal (``app/engines/modal.py`` et ``modal_server.py``)."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from app import config
from app.engines import availability, get_engine
from app.engines.base import TranscriptionError
from app.engines.modal import ModalEngine, ModalSession, check_endpoint

PHRASE_FRANCAISE = "Stéphane répète qu'être là, ça réchauffe le cœur."


class _Settings:
    modal_endpoint_url = "https://mon-espace--verbatim-transcription-serve.modal.run"
    modal_api_token = ""
    modal_boot_timeout_seconds = 5
    modal_job_timeout_seconds = 30
    hf_token = ""
    diarization_enabled = True


def test_modal_est_enregistre_dans_les_moteurs(monkeypatch):
    assert "modal" in config.ENGINES
    engine = get_engine("modal")
    assert isinstance(engine, ModalEngine)
    etat = availability()
    assert "modal" in etat


def test_disponibilite_modal_selon_url(monkeypatch):
    s = _Settings()
    s.modal_endpoint_url = ""
    monkeypatch.setattr("app.engines.modal.load_settings", lambda: s)
    ok, detail = ModalEngine().is_available()
    assert not ok and "modal deploy" in detail

    s.modal_endpoint_url = "ftp://invalide"
    ok, detail = ModalEngine().is_available()
    assert not ok and "https://" in detail

    s.modal_endpoint_url = "https://exemple.modal.run/"
    ok, detail = ModalEngine().is_available()
    assert ok and "https://exemple.modal.run" in detail


def test_aller_retour_utf8_accents_et_oe_sans_ufffd(monkeypatch, tmp_path):
    """Vérifie l'aller-retour complet (prompt + segments + mots) avec é, è, ê, à, ç et œ."""
    s = _Settings()
    s.modal_api_token = "secret_modal_123"
    s.hf_token = "hf_test_token"
    monkeypatch.setattr("app.engines.modal.load_settings", lambda: s)

    recu = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer secret_modal_123"
        assert request.headers.get("X-HF-Token") == "hf_test_token"
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/jobs" and request.method == "POST":
            raw = request.read()
            assert PHRASE_FRANCAISE.encode("utf-8") in raw
            recu["multipart"] = raw
            return httpx.Response(202, json={"job_id": "job42", "status": "queued"})
        if request.url.path == "/jobs/job42":
            payload = {
                "job_id": "job42",
                "status": "done",
                "result": {
                    "text": PHRASE_FRANCAISE,
                    "language": "fr",
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 3.5,
                            "text": f" {PHRASE_FRANCAISE} ",
                            "confidence": 0.95,
                            "speaker": "SPEAKER_00",
                            "words": [
                                {"start": 0.0, "end": 0.8, "text": "Stéphane", "confidence": 0.98},
                                {"start": 0.8, "end": 3.5, "text": "cœur.", "confidence": 0.92},
                            ],
                        }
                    ],
                },
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            return httpx.Response(
                200,
                content=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        return httpx.Response(404, json={"error": "introuvable"})

    orig_init = ModalSession.__init__

    def fake_init(self, settings, *, http_client=None):
        orig_init(self, settings, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(ModalSession, "__init__", fake_init)

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF____WAVE")
    segments = list(
        ModalEngine().transcribe(
            wav,
            model="large-v3",
            language="fr",
            duration=3.5,
            workdir=tmp_path,
            initial_prompt=PHRASE_FRANCAISE,
        )
    )
    assert len(segments) == 1
    seg = segments[0]
    assert seg.text == PHRASE_FRANCAISE
    for ch in ("é", "è", "ê", "à", "ç", "œ"):
        assert ch in seg.text
    assert "\ufffd" not in seg.text
    assert seg.speaker == "SPEAKER_00"
    assert [w.text for w in seg.words] == ["Stéphane", "cœur."]


def test_refuse_caractere_de_remplacement_ufffd(monkeypatch, tmp_path):
    """Tout U+FFFD dans la réponse du serveur Modal déclenche une erreur immédiate."""
    s = _Settings()
    monkeypatch.setattr("app.engines.modal.load_settings", lambda: s)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/jobs":
            return httpx.Response(202, json={"job_id": "corrompu", "status": "queued"})
        if request.url.path == "/jobs/corrompu":
            payload = {
                "job_id": "corrompu",
                "status": "done",
                "result": {
                    "text": "St\ufffdphane",
                    "segments": [{"start": 0.0, "end": 1.0, "text": "St\ufffdphane"}],
                },
            }
            return httpx.Response(
                200,
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        return httpx.Response(404)

    orig_init = ModalSession.__init__
    monkeypatch.setattr(
        ModalSession,
        "__init__",
        lambda self, settings, *, http_client=None: orig_init(
            self, settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
    )

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF____WAVE")
    with pytest.raises(TranscriptionError, match="U\\+FFFD"):
        list(
            ModalEngine().transcribe(
                wav, model="large-v3", language="fr", duration=1.0, workdir=tmp_path
            )
        )


def test_refuse_octets_non_utf8(monkeypatch, tmp_path):
    """Une réponse contenant des octets Latin-1 bruts non UTF-8 est rejetée explicitement."""
    s = _Settings()
    monkeypatch.setattr("app.engines.modal.load_settings", lambda: s)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/jobs":
            return httpx.Response(200, content=b'{"text": "St\xe9phane"}')
        return httpx.Response(404)

    orig_init = ModalSession.__init__
    monkeypatch.setattr(
        ModalSession,
        "__init__",
        lambda self, settings, *, http_client=None: orig_init(
            self, settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
    )

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF____WAVE")
    with pytest.raises(TranscriptionError, match="UTF-8"):
        list(
            ModalEngine().transcribe(
                wav, model="large-v3", language="fr", duration=1.0, workdir=tmp_path
            )
        )


def test_check_endpoint_verifie_health():
    s = _Settings()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"status": "ok"}))
    )
    ok, detail = check_endpoint(s, http_client=client)
    assert ok and "opérationnel" in detail

    client_401 = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "refusé"}))
    )
    ok, detail = check_endpoint(s, http_client=client_401)
    assert not ok and "401" in detail


def test_check_endpoint_patiente_pendant_le_demarrage_a_froid(monkeypatch):
    monkeypatch.setattr("app.engines.modal.time.sleep", lambda s: None)
    appels = {"n": 0}

    def handler(req):
        appels["n"] += 1
        if appels["n"] < 3:
            raise httpx.ReadTimeout("timeout", request=req)
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok, detail = check_endpoint(_Settings(), http_client=client)
    assert ok and appels["n"] == 3


def test_check_endpoint_abandonne_apres_le_delai(monkeypatch):
    monkeypatch.setattr("app.engines.modal.time.sleep", lambda s: None)
    s = _Settings()
    s.modal_check_timeout_seconds = 0

    def handler(req):
        raise httpx.ReadTimeout("timeout", request=req)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok, detail = check_endpoint(s, http_client=client)
    assert not ok and "injoignable" in detail


def test_modal_server_gpu_de_repli(monkeypatch):
    monkeypatch.delenv("MODAL_GPU", raising=False)
    racine = Path(__file__).resolve().parent.parent
    source = (racine / "modal_server.py").read_text(encoding="utf-8")
    namespace: dict = {}
    # _parse_gpus est pur : on l'extrait sans importer le SDK modal.
    debut = source.index("def _parse_gpus")
    fin = source.index("# Tous consomment")
    exec(source[debut:fin], namespace)
    assert namespace["_parse_gpus"]("L4, T4,,A10") == ["L4", "T4", "A10"]
    assert namespace["_parse_gpus"]("") == ["L4"]


def test_modal_server_se_charge_avec_sdk_modal_simule(monkeypatch):
    """Vérifie que modal_server.py déclare l'application, le volume et la fonction web."""
    enregistre = {}

    class FakeImage:
        def env(self, d):
            enregistre["env"] = d
            return self

        def apt_install(self, *pkgs):
            enregistre["apt"] = pkgs
            return self

        def add_local_file(self, local, remote_path, copy=False):
            enregistre.setdefault("files", []).append((Path(local).name, remote_path))
            return self

        def run_commands(self, *cmds):
            enregistre["cmds"] = cmds
            return self

    class FakeApp:
        def __init__(self, name):
            enregistre["app_name"] = name

        def function(self, **kwargs):
            enregistre["function_kwargs"] = kwargs
            return lambda fn: fn

    fake_modal = types.ModuleType("modal")
    fake_modal.Image = types.SimpleNamespace(
        from_registry=lambda tag, add_python=None: (
            enregistre.update({"tag": tag, "add_python": add_python}) or FakeImage()
        )
    )
    fake_modal.Volume = types.SimpleNamespace(from_name=lambda name, create_if_missing=False: name)
    fake_modal.App = FakeApp
    fake_modal.concurrent = lambda **kwargs: (lambda fn: fn)
    fake_modal.web_server = lambda port, **kwargs: (lambda fn: fn)

    monkeypatch.delenv("MODAL_GPU", raising=False)
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    racine = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("modal_server", racine / "modal_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert enregistre["app_name"] == "verbatim-transcription"
    assert enregistre["add_python"] == "3.10"
    assert enregistre["function_kwargs"]["gpu"] == ["L4", "T4", "A10"]
    assert enregistre["function_kwargs"]["volumes"] == {"/runpod-volume": "verbatim-model-cache"}
    assert ("pod_server.py", "/pod_server.py") in enregistre["files"]
