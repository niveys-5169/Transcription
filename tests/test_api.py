"""Test de bout en bout de l'API, avec un moteur de transcription simulé."""
import json
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, db, engines, media, pipeline, server
from app.engines.base import Segment
from app.proofread.base import ProofreadResult, TextPair

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
                   initial_prompt=None, on_progress=None, should_cancel=None):
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
    # factcheck/publish à « false » par défaut : ces étapes appellent Claude
    # et le coffre Obsidian, hors du périmètre de la plupart de ces tests
    # (voir test_pipeline_stages.py pour leur test dédié).
    donnees = {"engine": "local", "model": "tiny", "language": "fr",
               "proofread": "basic", "structure": "false",
               "factcheck": "false", "publish": "false"}
    donnees.update(champs)
    reponse = client.post(
        "/api/jobs",
        files={"file": (nom, b"\x00" * 2048, "video/mp4")},
        data=donnees,
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["id"]


def _attendre_statut(client, job_id, statuts, timeout=20.0):
    """Attend que le travail atteigne l'un des statuts donnés."""
    limite = time.monotonic() + timeout
    job = None
    while time.monotonic() < limite:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuts:
            return job
        time.sleep(0.15)
    raise AssertionError(
        f"Travail {job_id} toujours en {job['status'] if job else '?'}, "
        f"attendu {statuts}"
    )


def _attendre(client, job_id, timeout=20.0):
    """Attend la fin complète du travail — relecture comprise."""
    return _attendre_statut(client, job_id, {"done", "error", "canceled"}, timeout)


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


# --------------------------------------------------------- navigation de dossiers


def test_browse_sans_chemin_part_du_dossier_personnel(client):
    body = client.get("/api/browse").json()
    assert body["path"]
    assert isinstance(body["directories"], list)


def test_browse_liste_les_sous_dossiers(client, tmp_path):
    (tmp_path / "MonCoffre").mkdir()
    (tmp_path / "Autre").mkdir()
    (tmp_path / ".cache").mkdir()  # dossier caché : ignoré
    (tmp_path / "un_fichier.txt").write_text("x")  # pas un dossier : ignoré

    body = client.get("/api/browse", params={"path": str(tmp_path)}).json()
    noms = sorted(d["name"] for d in body["directories"])
    assert noms == ["Autre", "MonCoffre"]
    assert body["path"] == str(tmp_path.resolve())


def test_browse_expose_le_dossier_parent_pour_remonter(client, tmp_path):
    sous_dossier = tmp_path / "MonCoffre"
    sous_dossier.mkdir()

    body = client.get("/api/browse", params={"path": str(sous_dossier)}).json()
    assert body["parent"] == str(tmp_path.resolve())


def test_browse_chemin_inexistant_retombe_proprement(client):
    body = client.get("/api/browse", params={"path": "/ceci/n-existe-pas/vraiment"}).json()
    assert body["path"]  # jamais d'erreur : repli sur le dossier personnel


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
            ("srt", "00:00:00,000 -->"),
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
    assert client.get(f"/api/jobs/{job_id}/download/inconnu").status_code == 400


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


# ------------------------------------------- deux étapes : transcrire, relire


def test_sans_enchainement_le_travail_s_arrete_apres_la_transcription(client):
    job_id = _deposer(client, "cours.mp4", chain="false")
    job = _attendre_statut(client, job_id, {"transcribed"})

    assert job["status"] == "transcribed"
    assert job["raw_text"]
    assert len(job["segments"]) == len(FAUX_SEGMENTS)
    # La relecture n'a pas eu lieu : pas de texte relu, pas de vérification.
    assert not job["clean_text"]
    assert job["verification"] == []


def test_un_travail_transcrit_est_deja_telechargeable(client):
    job_id = _deposer(client, "brut.mp4", chain="false")
    _attendre_statut(client, job_id, {"transcribed"})

    for fmt in ("txt", "srt", "vtt", "json"):
        reponse = client.get(f"/api/jobs/{job_id}/download/{fmt}")
        assert reponse.status_code == 200, fmt
    # Le .txt retombe sur le texte brut, faute de texte relu.
    assert "euh" in client.get(f"/api/jobs/{job_id}/download/txt").text


def test_la_relecture_se_lance_separement_plus_tard(client):
    job_id = _deposer(client, "cours.mp4", chain="false")
    _attendre_statut(client, job_id, {"transcribed"})

    reponse = client.post(f"/api/jobs/{job_id}/proofread", json={"proofread": "basic"})
    assert reponse.status_code == 200

    job = _attendre(client, job_id)
    assert job["status"] == "done"
    assert "euh" not in job["clean_text"]
    # La transcription n'a pas été refaite : mêmes segments.
    assert len(job["segments"]) == len(FAUX_SEGMENTS)


def test_la_relecture_peut_etre_relancee_avec_d_autres_reglages(client):
    job_id = _deposer(client, "cours.mp4", proofread="basic")
    premier = _attendre(client, job_id)
    assert "euh" not in premier["clean_text"]

    # Rejouer en mode « aucune relecture » : le texte redevient brut.
    assert client.post(
        f"/api/jobs/{job_id}/proofread", json={"proofread": "none"}
    ).status_code == 200
    second = _attendre(client, job_id)
    assert second["proofread"] == "none"
    assert "euh" in second["clean_text"]


def test_la_relecture_peut_etre_relancee_explicitement_avec_nim(client, monkeypatch):
    job_id = _deposer(client, chain="false")
    _attendre_statut(client, job_id, {"transcribed"})
    calls = []
    monkeypatch.setattr(pipeline, "enqueue", lambda ident, task: calls.append((ident, task)))

    reponse = client.post(f"/api/jobs/{job_id}/proofread", json={"proofread": "nim"})

    assert reponse.status_code == 200
    assert reponse.json()["proofread"] == "nim"
    assert calls == [(job_id, pipeline.TASK_PROOFREAD)]


def test_relecture_refusee_sans_transcription(client, monkeypatch):
    monkeypatch.setitem(engines._ENGINES, "local", MoteurEnEchec)
    job_id = _deposer(client)
    _attendre(client, job_id)

    reponse = client.post(f"/api/jobs/{job_id}/proofread")
    assert reponse.status_code == 409
    assert "transcription" in reponse.json()["detail"].lower()


def test_relecture_d_un_travail_inexistant(client):
    assert client.post("/api/jobs/inconnu/proofread").status_code == 404


def test_mode_de_relecture_invalide_refuse(client):
    job_id = _deposer(client, chain="false")
    _attendre_statut(client, job_id, {"transcribed"})
    reponse = client.post(f"/api/jobs/{job_id}/proofread", json={"proofread": "zen"})
    assert reponse.status_code == 400


def test_la_verification_par_regles_tourne_meme_en_relecture_simple(client):
    job_id = _deposer(client, proofread="basic")
    job = _attendre(client, job_id)

    rapport = job["verification"]
    assert rapport["mode"] == "regles"
    assert rapport["checked_pairs"] > 0
    assert "counts" in rapport


def test_la_verification_signale_un_chiffre_perdu(client, monkeypatch):
    class MoteurAvecChiffre:
        name, label = "local", "chiffre"

        def is_available(self):
            return True, "ok"

        def transcribe(self, *args, **kwargs):
            # « 42 » disparaîtra : le nettoyage mécanique retire « euh »,
            # mais on force ici une relecture qui perd le nombre.
            yield Segment(0.0, 5.0, "le seuil est fixé à 42 degrés exactement")

    # Relecture qui perd le nombre : c'est précisément ce que la
    # vérification doit rattraper.
    relu = "Le seuil est fixé à quelques degrés exactement."
    monkeypatch.setitem(engines._ENGINES, "local", MoteurAvecChiffre)
    monkeypatch.setattr(
        "app.pipeline.basic_proofread",
        lambda segments: ProofreadResult(
            text=relu,
            mode="basic",
            pairs=[
                TextPair(
                    start=0.0,
                    end=5.0,
                    raw="le seuil est fixé à 42 degrés exactement",
                    clean=relu,
                )
            ],
        ),
    )

    job = _attendre(client, _deposer(client, proofread="basic"))
    points = job["verification"]["findings"]
    assert any(p["kind"] == "chiffre" and "42" in p["message"] for p in points)


def test_les_points_a_verifier_apparaissent_dans_le_markdown(client, monkeypatch):
    job_id = _deposer(client, "chiffres.mp4", proofread="basic")
    _attendre(client, job_id)
    db.update_job(
        job_id,
        verification={
            "mode": "regles",
            "checked_pairs": 1,
            "counts": {"haute": 1, "moyenne": 0, "basse": 0},
            "findings": [
                {
                    "kind": "chiffre",
                    "severity": "haute",
                    "message": "Le nombre « 42 » est absent du texte relu.",
                    "start": 12.0,
                    "raw_excerpt": "",
                    "clean_excerpt": "",
                    "source": "regles",
                }
            ],
        },
    )
    markdown = client.get(f"/api/jobs/{job_id}/download/md").text
    assert "## Points à vérifier" in markdown
    assert "00:00:12" in markdown
    assert "42" in markdown
