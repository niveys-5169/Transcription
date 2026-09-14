"""Test de bout en bout des quatre étapes, un clic jusqu'à la publication.

Moteur de transcription, back-end Claude et coffre Obsidian sont tous
simulés : ce test vérifie l'enchaînement (chain/factcheck/publish) et l'état
de la base à chaque étape, pas les appels externes eux-mêmes (voir
test_factcheck.py, test_obsidian.py, test_cli_backend.py pour ça).
"""
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, db, engines, media, obsidian, server
from app.engines.base import Segment
from app.proofread import factcheck as factcheck_module
from app.proofread.backends.base import BackendResult

FAUX_SEGMENTS = [
    Segment(0.0, 3.0, "alors euh bonjour à tous"),
    Segment(3.2, 7.0, "le rapport Dupont est notre référence aujourd'hui ."),
]


class FauxMoteur:
    name = "local"
    label = "Moteur simulé"

    def is_available(self):
        return True, "Moteur simulé."

    def transcribe(self, wav_path, *, model, language, duration, workdir,
                   initial_prompt=None, on_progress=None, should_cancel=None):
        for segment in FAUX_SEGMENTS:
            yield segment


class _FakeFactcheckBackend:
    """Confirme tout, sans recherche réelle — on teste l'enchaînement, pas le verdict."""

    def is_available(self):
        return True, "ok"

    def complete(self, *, system, user, max_tokens, schema=None, web_search=False):
        if schema and schema.get("type") == "array":
            return BackendResult(text="[]", parsed=[])
        return BackendResult(text="{}", parsed={})


def _ecrire_wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = (np.sin(np.arange(16_000 * 8) / 40.0) * 8000).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(samples.tobytes())
    return path


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "coffre"
    v.mkdir()
    return v


@pytest.fixture
def client(monkeypatch, vault):
    monkeypatch.setitem(engines._ENGINES, "local", FauxMoteur)
    monkeypatch.setattr(media, "probe_duration", lambda path: 10.0)
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(media, "extract_wav", lambda src, dst, **kwargs: _ecrire_wav(dst))
    monkeypatch.setattr(factcheck_module, "get_backend", lambda settings=None: _FakeFactcheckBackend())
    config.save_settings({"obsidian_vault_path": str(vault)})
    with TestClient(server.app) as test_client:
        yield test_client
    config.save_settings({"obsidian_vault_path": ""})


def _attendre_statut(client, job_id, statuts, timeout=20.0):
    limite = time.monotonic() + timeout
    job = None
    while time.monotonic() < limite:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuts:
            return job
        time.sleep(0.1)
    raise AssertionError(
        f"Travail {job_id} toujours en {job['status'] if job else '?'}, attendu {statuts}"
    )


def test_un_clic_va_jusqu_a_la_publication(client, vault):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp4", b"\x00" * 2048, "video/mp4")},
        data={
            "engine": "local", "model": "tiny", "language": "fr",
            "proofread": "basic", "structure": "false", "one_click": "true",
        },
    )
    assert reponse.status_code == 200, reponse.text
    job_id = reponse.json()["id"]

    job = _attendre_statut(client, job_id, {"published", "error", "canceled"})
    assert job["status"] == "published", job.get("error")
    assert job["obsidian_path"]
    assert (vault / job["obsidian_path"]).exists()

    note = (vault / job["obsidian_path"]).read_text(encoding="utf-8")
    assert "statut_verification:" in note


def test_factcheck_desactive_publie_quand_meme_directement(client, vault):
    """chain=True, factcheck=False, publish=True : saute l'étape 3."""
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp4", b"\x00" * 2048, "video/mp4")},
        data={
            "engine": "local", "model": "tiny", "language": "fr",
            "proofread": "basic", "structure": "false",
            "chain": "true", "factcheck": "false", "publish": "true",
        },
    )
    job_id = reponse.json()["id"]
    job = _attendre_statut(client, job_id, {"published", "error", "canceled"})
    assert job["status"] == "published"

    note = (vault / job["obsidian_path"]).read_text(encoding="utf-8")
    assert "statut_verification: non_verifie" in note


def test_sans_publish_s_arrete_a_checked(client):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp4", b"\x00" * 2048, "video/mp4")},
        data={
            "engine": "local", "model": "tiny", "language": "fr",
            "proofread": "basic", "structure": "false",
            "chain": "true", "factcheck": "true", "publish": "false",
        },
    )
    job_id = reponse.json()["id"]
    job = _attendre_statut(client, job_id, {"checked", "error", "canceled"})
    assert job["status"] == "checked"


def test_sans_chain_s_arrete_a_transcribed(client):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp4", b"\x00" * 2048, "video/mp4")},
        data={
            "engine": "local", "model": "tiny", "language": "fr",
            "proofread": "basic", "structure": "false", "chain": "false",
        },
    )
    job_id = reponse.json()["id"]
    job = _attendre_statut(client, job_id, {"transcribed", "error", "canceled"})
    assert job["status"] == "transcribed"


def test_publish_refuse_un_travail_pas_encore_relu(client):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp4", b"\x00" * 2048, "video/mp4")},
        data={
            "engine": "local", "model": "tiny", "language": "fr",
            "proofread": "basic", "structure": "false", "chain": "false",
        },
    )
    job_id = reponse.json()["id"]
    _attendre_statut(client, job_id, {"transcribed"})

    r = client.post(f"/api/jobs/{job_id}/publish")
    assert r.status_code == 409


def test_route_publish_explicite_depuis_done(client, vault):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp4", b"\x00" * 2048, "video/mp4")},
        data={
            "engine": "local", "model": "tiny", "language": "fr",
            "proofread": "basic", "structure": "false",
            "chain": "true", "factcheck": "false", "publish": "false",
        },
    )
    job_id = reponse.json()["id"]
    _attendre_statut(client, job_id, {"done"})

    r = client.post(f"/api/jobs/{job_id}/publish")
    assert r.status_code == 200
    job = _attendre_statut(client, job_id, {"error", "published"})
    assert job["status"] == "published"


def test_republier_manuellement_ne_duplique_pas_la_fiche(client, vault):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp4", b"\x00" * 2048, "video/mp4")},
        data={
            "engine": "local", "model": "tiny", "language": "fr",
            "proofread": "basic", "structure": "false", "one_click": "true",
        },
    )
    job_id = reponse.json()["id"]
    job = _attendre_statut(client, job_id, {"published"})
    first_path = job["obsidian_path"]

    r = client.post(f"/api/jobs/{job_id}/publish")
    assert r.status_code == 200
    job2 = _attendre_statut(client, job_id, {"published"})
    assert job2["obsidian_path"] == first_path
