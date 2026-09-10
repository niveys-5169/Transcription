"""Test de bout en bout de l'API, avec un moteur de transcription simulé."""
import json
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, db, engines, media, server
from app.engines.base import Segment

FAUX_SEGMENTS = [
    Segment(0.0, 3.0, "alors euh bonjour à tous"),
    Segment(3.2, 7.0, "on commence le cours d'aujourd'hui ."),
    Segment(9.0, 12.0, "le le premier point est simple ."),
]


class FauxMoteur:
    """Moteur qui rend des segments connus, sans rien calculer."""

    name = "local"
    label = "Moteur simulé"

    def is_available(self):
        return True, "Moteur simulé."

    def transcribe(self, wav_path, *, model, language, duration, workdir,
                   on_progress=None, should_cancel=None):
        for index, segment in enumerate(FAUX_SEGMENTS, start=1):
            if should_cancel is not None and should_cancel():
                return
            if on_progress:
                on_progress(index / len(FAUX_SEGMENTS), "Transcription…")
            yield segment


class MoteurEnEchec:
    name = "local"
    label = "Moteur en échec"

    def is_available(self):
        return True, "ok"

    def transcribe(self, *args, **kwargs):
        raise engines.TranscriptionError("La carte graphique a pris feu.")
        yield  # pragma: no cover - rend la fonction génératrice


def _ecrire_wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = (np.sin(np.arange(16_000 * 12) / 40.0) * 8000).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(samples.tobytes())
    return path


@pytest.fixture
def client(monkeypatch):
    """Client HTTP avec extraction ffmpeg et moteur Whisper simulés."""
    monkeypatch.setitem(engines._ENGINES, "local", FauxMoteur)
    monkeypatch.setattr(media, "probe_duration", lambda path: 12.0)
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        media,
        "extract_wav",
        lambda src, dst, **kwargs: _ecrire_wav(dst),
    )
    with TestClient(server.app) as test_client:
        yield test_client


def _deposer(client, nom="cours.mp4", **champs):
    donnees = {"engine": "local", "model": "tiny", "language": "fr",
               "proofread": "basic", "structure": "false"}
    donnees.update(champs)
    reponse = client.post(
        "/api/jobs",
        files={"file": (nom, b"\x00" * 2048, "video/mp4")},
        data=donnees,
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["id"]


def _attendre(client, job_id, timeout=20.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"done", "error", "canceled"}:
            return job
        time.sleep(0.15)
    raise AssertionError(f"Travail {job_id} toujours en {job['status']}")


# --------------------------------------------------------------------- état


def test_status_expose_les_capacites(client):
    body = client.get("/api/status").json()
    assert body["engines"]["local"]["available"] is True
    assert "large-v3" in body["models"]
    assert "settings" in body


def test_status_ne_divulgue_jamais_les_cles(client):
    settings = client.get("/api/status").json()["settings"]
    assert settings["anthropic_api_key"] == ""
    assert "anthropic_api_key_set" in settings


def test_reglages_conservent_une_cle_existante(client):
    original = config.load_settings().public_dict()
    try:
        client.post("/api/settings", json={"anthropic_api_key": "sk-ant-secret"})
        apres = client.post("/api/settings", json={"proofread_effort": "high"}).json()
        assert apres["anthropic_api_key_set"] is True
        assert apres["anthropic_api_key"] == ""
        assert apres["proofread_effort"] == "high"

        efface = client.post(
            "/api/settings", json={"anthropic_api_key": "__clear__"}
        ).json()
        assert efface["anthropic_api_key_set"] is False
    finally:
        config.save_settings(
            {"anthropic_api_key": "__clear__",
             "proofread_effort": original["proofread_effort"]}
        )


# ------------------------------------------------------------------ travaux


def test_cycle_complet_depot_transcription_relecture(client):
    job_id = _deposer(client)
    job = _attendre(client, job_id)

    assert job["status"] == "done"
    assert job["progress"] == 1.0
    assert job["duration"] == 12.0
    # Le texte brut garde les scories de l'oral…
    assert "euh" in job["raw_text"]
    # …que la relecture simple supprime.
    assert "euh" not in job["clean_text"]
    assert "le le" not in job["clean_text"]
    assert len(job["segments"]) == len(FAUX_SEGMENTS)
    assert job["segments"][0]["start"] == 0.0


def test_fichier_vide_refuse(client):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("vide.mp3", b"", "audio/mpeg")},
        data={"engine": "local", "model": "tiny", "proofread": "none"},
    )
    assert reponse.status_code == 400
    assert "vide" in reponse.json()["detail"]


@pytest.mark.parametrize(
    "champ,valeur",
    [("engine", "quantique"), ("model", "gigantesque"), ("proofread", "poetique")],
)
def test_parametres_invalides_refuses(client, champ, valeur):
    reponse = client.post(
        "/api/jobs",
        files={"file": ("cours.mp3", b"\x00" * 64, "audio/mpeg")},
        data={"engine": "local", "model": "tiny", "proofread": "none", champ: valeur},
    )
    assert reponse.status_code == 400


def test_echec_du_moteur_remonte_dans_le_travail(client, monkeypatch):
    monkeypatch.setitem(engines._ENGINES, "local", MoteurEnEchec)
    job = _attendre(client, _deposer(client, "casse.mp4"))
    assert job["status"] == "error"
    assert "carte graphique" in job["error"]


def test_travail_inexistant(client):
    assert client.get("/api/jobs/inconnu").status_code == 404
    assert client.delete("/api/jobs/inconnu").status_code == 404


# ---------------------------------------------------------- téléchargements


def test_tous_les_formats_de_telechargement(client):
    job_id = _deposer(client, "thermo.mp4")
    _attendre(client, job_id)

    for fmt, attendu in [
        ("txt", "bonjour à tous"),
        ("md", "# "),
        ("srt", "00:00:00,000 --> 00:00:03,000"),
        ("vtt", "WEBVTT"),
    ]:
        reponse = client.get(f"/api/jobs/{job_id}/download/{fmt}")
        assert reponse.status_code == 200, fmt
        assert attendu in reponse.text
        assert f'filename="thermo.{fmt}"' in reponse.headers["content-disposition"]

    payload = json.loads(client.get(f"/api/jobs/{job_id}/download/json").text)
    assert payload["fichier"] == "thermo.mp4"
    assert len(payload["segments"]) == len(FAUX_SEGMENTS)


def test_format_inconnu_refuse(client):
    job_id = _deposer(client)
    _attendre(client, job_id)
    assert client.get(f"/api/jobs/{job_id}/download/docx").status_code == 400


def test_telechargement_avant_la_fin_refuse(client):
    job_id = _deposer(client)
    db.update_job(job_id, status="queued")
    assert client.get(f"/api/jobs/{job_id}/download/txt").status_code == 409
    _attendre(client, job_id)


def test_audio_extrait_telechargeable(client):
    job_id = _deposer(client)
    _attendre(client, job_id)
    reponse = client.get(f"/api/jobs/{job_id}/audio")
    assert reponse.status_code == 200
    assert reponse.content[:4] == b"RIFF"


# --------------------------------------------------------- liste et gestion


def test_liste_et_recherche(client):
    job_id = _deposer(client, "cours-de-latin.mp4")
    _attendre(client, job_id)

    ids = [job["id"] for job in client.get("/api/jobs").json()["jobs"]]
    assert job_id in ids

    trouve = client.get("/api/jobs", params={"q": "latin"}).json()["jobs"]
    assert job_id in [job["id"] for job in trouve]

    absent = client.get("/api/jobs", params={"q": "zzzintrouvable"}).json()["jobs"]
    assert absent == []


def test_la_liste_ne_transporte_pas_les_gros_champs(client):
    _attendre(client, _deposer(client))
    premier = client.get("/api/jobs").json()["jobs"][0]
    assert "raw_text" not in premier
    assert "clean_text" not in premier


def test_suppression_efface_aussi_les_fichiers(client):
    job_id = _deposer(client)
    _attendre(client, job_id)
    dossier = config.MEDIA_DIR / job_id
    assert dossier.exists()

    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert not dossier.exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_relance_d_un_travail_en_erreur(client, monkeypatch):
    monkeypatch.setitem(engines._ENGINES, "local", MoteurEnEchec)
    job_id = _deposer(client)
    assert _attendre(client, job_id)["status"] == "error"

    monkeypatch.setitem(engines._ENGINES, "local", FauxMoteur)
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    relance = _attendre(client, job_id)
    assert relance["status"] == "done"
    assert relance["error"] is None


def test_mode_sans_relecture_ne_touche_pas_aux_mots(client):
    job_id = _deposer(client, "brut.mp4", proofread="none")
    job = _attendre(client, job_id)
    assert job["status"] == "done"
    assert job["proofread"] == "none"
    # Les scories de l'oral sont conservées telles quelles.
    assert "euh" in job["clean_text"]
    assert "le le" in job["clean_text"]
