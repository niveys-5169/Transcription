"""Tests du serveur HTTP du pod de secours (``pod_server.py``), sans GPU.

Même contrat que ``handler.py`` (voir ``tests/test_handler.py``) mais exposé
en HTTP continu plutôt qu'en job serverless — ce fichier vérifie surtout que
le contrat est bien respecté par-dessus HTTP : ``/health`` pour le
health-check attendu par ``PodFallbackSession._wait_ready``, ``/transcribe``
pour le calcul, avec le même comportement « pas de filtre de voix » que les
deux autres moteurs.
"""
from __future__ import annotations

import base64
import importlib.util
import sys
import threading
import types
from pathlib import Path

import httpx
import pytest

RACINE = Path(__file__).resolve().parent.parent
POD_SERVER = RACINE / "pod_server.py"


class _FauxSegment:
    def __init__(self, start, end, text, avg_logprob=None):
        self.start, self.end, self.text = start, end, text
        self.avg_logprob = avg_logprob


class _FauxInfo:
    language = "fr"


@pytest.fixture
def worker(monkeypatch):
    """Importe pod_server.py avec faster-whisper simulé."""
    appels = {"modele": [], "transcribe": None}

    class FauxWhisperModel:
        def __init__(self, taille, **kwargs):
            appels["modele"].append({"taille": taille, **kwargs})

        def transcribe(self, chemin, **kwargs):
            appels["transcribe"] = {"chemin": chemin, **kwargs}
            return (
                iter(
                    [
                        _FauxSegment(0.0, 2.0, " Bonjour à tous."),
                        _FauxSegment(2.0, 4.5, " On commence le cours."),
                    ]
                ),
                _FauxInfo(),
            )

    faux_fw = types.ModuleType("faster_whisper")
    faux_fw.WhisperModel = FauxWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", faux_fw)
    monkeypatch.delitem(sys.modules, "pod_server", raising=False)

    spec = importlib.util.spec_from_file_location("pod_server", POD_SERVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pod_server"] = module
    spec.loader.exec_module(module)

    module._appels = appels
    module._model_cache.clear()
    yield module
    sys.modules.pop("pod_server", None)


# ------------------------------------------------------------ contrat d'API


def test_transcription_rend_le_contrat_attendu(worker):
    sortie = worker.transcribe(
        base64.b64encode(b"RIFF____WAVE").decode(), "large-v3", "fr"
    )
    assert sortie["text"] == "Bonjour à tous. On commence le cours."
    assert sortie["language"] == "fr"
    assert sortie["segments"][0] == {
        "start": 0.0, "end": 2.0, "text": " Bonjour à tous.", "confidence": None,
    }
    assert "error" not in sortie


def test_la_confiance_est_deduite_de_avg_logprob(worker, monkeypatch):
    def transcribe(self, chemin, **kwargs):
        return (
            iter([_FauxSegment(0.0, 2.0, "Bonjour.", avg_logprob=-0.2)]),
            _FauxInfo(),
        )

    monkeypatch.setattr(
        sys.modules["faster_whisper"].WhisperModel, "transcribe", transcribe
    )
    sortie = worker.transcribe(
        base64.b64encode(b"RIFF____WAVE").decode(), "large-v3", "fr"
    )
    confiance = sortie["segments"][0]["confidence"]
    assert confiance is not None and 0.0 <= confiance <= 1.0


def test_le_filtre_de_voix_reste_desactive(worker):
    """Même bug que sur handler.py et le moteur local : vad_filter=True a
    déjà fait disparaître un fichier entier sans la moindre erreur."""
    worker.transcribe(base64.b64encode(b"RIFF____WAVE").decode(), "large-v3", "fr")
    assert worker._appels["transcribe"]["vad_filter"] is False


def test_le_worker_tourne_sur_gpu_en_float16(worker):
    worker.transcribe(base64.b64encode(b"RIFF____WAVE").decode(), "medium", "fr")
    charge = worker._appels["modele"][-1]
    assert charge["taille"] == "medium"
    assert charge["device"] == "cuda"
    assert charge["compute_type"] == "float16"


def test_un_modele_inconnu_retombe_sur_large_v3(worker):
    worker.transcribe(base64.b64encode(b"RIFF____WAVE").decode(), "gigantesque", "fr")
    assert worker._appels["modele"][-1]["taille"] == "large-v3"


def test_le_modele_charge_depuis_le_volume_reseau_si_monte(worker, monkeypatch, tmp_path):
    """Sans volume attaché, aucun cache spécifique n'est imposé (repli sur
    le cache Hugging Face par défaut de l'image)."""
    monkeypatch.setattr(worker, "VOLUME_ROOT", str(tmp_path / "absent"))
    worker.transcribe(base64.b64encode(b"RIFF____WAVE").decode(), "small", "fr")
    assert worker._appels["modele"][-1]["download_root"] is None


def test_le_modele_utilise_le_cache_du_volume_reseau_quand_il_est_monte(
    worker, monkeypatch, tmp_path
):
    volume = tmp_path / "runpod-volume"
    volume.mkdir()
    monkeypatch.setattr(worker, "VOLUME_ROOT", str(volume))
    worker.transcribe(base64.b64encode(b"RIFF____WAVE").decode(), "small", "fr")
    charge = worker._appels["modele"][-1]
    assert charge["download_root"] == str(volume / "huggingface-cache" / "hub")


def test_le_modele_est_garde_en_cache_entre_deux_appels(worker):
    worker.transcribe(base64.b64encode(b"RIFF____WAVE").decode(), "small", "fr")
    worker.transcribe(base64.b64encode(b"RIFF____WAVE").decode(), "small", "fr")
    tailles = [charge["taille"] for charge in worker._appels["modele"]]
    assert tailles == ["small"], "le modèle a été rechargé inutilement"


def test_audio_manquant(worker):
    sortie = worker.transcribe(None, "large-v3", "fr")
    assert "error" in sortie
    assert "audio_base64" in sortie["error"]


def test_audio_base64_invalide(worker):
    sortie = worker.transcribe("pas du base64 !!", "large-v3", "fr")
    assert "error" in sortie


def test_une_erreur_de_transcription_est_renvoyee_proprement(worker, monkeypatch):
    def explose(self, chemin, **kwargs):
        raise RuntimeError("plus de mémoire GPU")

    monkeypatch.setattr(
        sys.modules["faster_whisper"].WhisperModel, "transcribe", explose
    )
    sortie = worker.transcribe(
        base64.b64encode(b"RIFF____WAVE").decode(), "large-v3", "fr"
    )
    assert sortie["error"] == "plus de mémoire GPU"


def test_whisperx_recoit_le_prompt_dans_les_options_du_modele(monkeypatch):
    """WhisperX 3.3.1 ne prend pas ``initial_prompt`` dans ``transcribe``."""
    appels = {}

    class PipelineWhisperX:
        def transcribe(self, audio, *, batch_size, language):
            appels["transcribe"] = {
                "audio": audio, "batch_size": batch_size, "language": language,
            }
            return {"language": "fr", "segments": [{"start": 0.0, "end": 1.0, "text": "Bonjour."}]}

    faux_whisperx = types.ModuleType("whisperx")

    def load_model(*args, **kwargs):
        appels["load_model"] = {"args": args, "kwargs": kwargs}
        return PipelineWhisperX()

    faux_whisperx.load_model = load_model
    faux_whisperx.load_align_model = lambda **kwargs: (object(), {})
    faux_whisperx.align = lambda segments, *args, **kwargs: {"segments": segments}
    monkeypatch.setitem(sys.modules, "whisperx", faux_whisperx)

    spec = importlib.util.spec_from_file_location("pod_server_prompt", POD_SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = module._transcribe_with_whisperx("cours.wav", "large-v3", "fr", "MJPM, APL", False)

    assert output["text"] == "Bonjour."
    assert appels["load_model"]["kwargs"]["asr_options"] == {"initial_prompt": "MJPM, APL"}
    assert appels["transcribe"] == {"audio": "cours.wav", "batch_size": 16, "language": "fr"}


# ------------------------------------------------------------- routes HTTP


@pytest.fixture
def serveur(worker):
    httpd = worker.ThreadingHTTPServer(("127.0.0.1", 0), worker.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_health_repond_200(serveur):
    reponse = httpx.get(f"{serveur}/health", timeout=5.0)
    assert reponse.status_code == 200
    assert reponse.json() == {"status": "ok"}


def test_transcribe_via_http_rend_le_meme_contrat(serveur):
    reponse = httpx.post(
        f"{serveur}/transcribe",
        json={
            "audio_base64": base64.b64encode(b"RIFF____WAVE").decode(),
            "model": "large-v3",
            "language": "fr",
        },
        timeout=5.0,
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["text"] == "Bonjour à tous. On commence le cours."


def test_transcribe_via_multipart_accepte_un_fichier_entier(serveur):
    reponse = httpx.post(
        f"{serveur}/transcribe",
        data={"model": "large-v3", "language": "auto", "diarize": "true"},
        files={"audio": ("cours.wav", b"RIFF____WAVE", "audio/wav")},
        timeout=5.0,
    )
    assert reponse.status_code == 200
    assert reponse.json()["text"] == "Bonjour à tous. On commence le cours."


def test_route_inconnue_rend_404(serveur):
    assert httpx.get(f"{serveur}/autre-chose", timeout=5.0).status_code == 404
