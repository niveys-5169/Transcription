"""Contrat API de l'éditeur synchronisé (phase 1)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, pipeline, server


@pytest.fixture
def client():
    with TestClient(server.app) as test_client:
        yield test_client


def _job(tmp_path: Path) -> str:
    source = tmp_path / "cours.mp4"
    source.write_bytes(b"source-media")
    job_id = db.create_job(
        filename="cours.mp4",
        media_path=str(source),
        size_bytes=source.stat().st_size,
        engine="local",
        model="tiny",
        language="fr",
        proofread="basic",
        structure=False,
    )
    db.update_job(
        job_id,
        status="transcribed",
        segments=[
            {"start": 1.0, "end": 3.5, "text": "Bonjour à tous."},
            {"start": 4.0, "end": 7.0, "text": "Le second passage."},
        ],
        raw_text="Bonjour à tous.\n\nLe second passage.",
    )
    return job_id


def test_migration_ajoute_les_primitives_editeur_sans_effacer_les_jobs(tmp_path):
    legacy = tmp_path / "legacy.sqlite"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_path TEXT,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO jobs VALUES ('ancien', 'cours.mp3', NULL, 'done', '2026', '2026');
            """
        )

    db.init_db(legacy)

    with sqlite3.connect(legacy) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert conn.execute("SELECT id FROM jobs").fetchone()[0] == "ancien"
    assert "review_blocks" in columns
    assert "annotations" in tables


def test_blocs_de_revision_sont_crees_a_la_demande_et_editables(client, tmp_path):
    job_id = _job(tmp_path)

    body = client.get(f"/api/jobs/{job_id}/review-blocks").json()
    assert body["blocks"] == [
        {"id": "segment-1", "start": 1.0, "end": 3.5, "text": "Bonjour à tous.", "raw_text": "Bonjour à tous.", "source_segment_ids": ["segment-1"], "confidence": None, "speaker": None, "role": None},
        {"id": "segment-2", "start": 4.0, "end": 7.0, "text": "Le second passage.", "raw_text": "Le second passage.", "source_segment_ids": ["segment-2"], "confidence": None, "speaker": None, "role": None},
    ]

    response = client.put(
        f"/api/jobs/{job_id}/review-blocks/segment-1",
        json={"text": "Bonjour tout le monde."},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Bonjour tout le monde."

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["segments"][0]["text"] == "Bonjour à tous."
    assert job["review_blocks"][0]["text"] == "Bonjour tout le monde."


def test_correction_conserve_les_temps_des_mots_inchanges(tmp_path):
    job_id = db.create_job(filename="cours.wav", media_path="", size_bytes=0, engine="local", model="tiny", language="fr", proofread="none", structure=False)
    db.update_job(job_id, segments=[{"start": 0, "end": 3, "text": "Bonjour monde", "words": [
        {"start": 0, "end": 1, "text": "Bonjour", "confidence": 0.9},
        {"start": 1, "end": 3, "text": "monde", "confidence": 0.8},
    ]}])
    db.update_review_block(job_id, "segment-1", text="Bonjour nouveau monde")
    words = db.ensure_review_blocks(job_id)[0]["words"]
    assert words[0]["start"] == 0 and words[0]["end"] == 1
    assert words[2]["start"] == 1 and words[2]["end"] == 3
    assert words[1]["text"] == "nouveau"


def test_annotations_sont_persistantes_et_validees(client, tmp_path):
    job_id = _job(tmp_path)
    annotation = client.post(
        f"/api/jobs/{job_id}/annotations",
        json={
            "block_id": "segment-1",
            "type": "note",
            "content": "Vérifier le nom de l'intervenant.",
            "status": "a_verifier",
        },
    )
    assert annotation.status_code == 200
    annotation_id = annotation.json()["id"]

    updated = client.patch(
        f"/api/jobs/{job_id}/annotations/{annotation_id}",
        json={"status": "valide"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "valide"
    assert client.get(f"/api/jobs/{job_id}/annotations").json()["annotations"][0]["content"]

    invalid = client.post(
        f"/api/jobs/{job_id}/annotations",
        json={"block_id": "inconnu", "type": "note"},
    )
    assert invalid.status_code == 400

    assert client.delete(f"/api/jobs/{job_id}/annotations/{annotation_id}").json() == {
        "deleted": annotation_id
    }


def test_surlignage_cible_et_historique_restaurable(client, tmp_path):
    job_id = _job(tmp_path)
    client.get(f"/api/jobs/{job_id}/review-blocks")
    annotation = client.post(f"/api/jobs/{job_id}/annotations", json={
        "block_id": "segment-1", "type": "highlight", "color": "yellow",
        "range_start": 0, "range_end": 7,
    })
    assert annotation.status_code == 200
    db.update_job(job_id, clean_text="Bonjour à tous.")
    snapshot = db.archive_review_version(job_id, reason="Test")
    assert snapshot
    assert client.get(f"/api/jobs/{job_id}/review-versions").json()["versions"][0]["id"] == snapshot
    client.put(f"/api/jobs/{job_id}/review-blocks/segment-1", json={"text": "Modifié."})
    restored = client.post(f"/api/jobs/{job_id}/review-versions/{snapshot}/restore")
    assert restored.status_code == 200
    assert db.ensure_review_blocks(job_id)[0]["text"] == "Bonjour à tous."
    assert client.get(f"/api/jobs/{job_id}/annotations").json()["annotations"][0]["range_end"] == 7


def test_recherche_globale_retourne_un_horodatage_de_bloc(client, tmp_path):
    job_id = _job(tmp_path)
    client.get(f"/api/jobs/{job_id}/review-blocks")
    client.put(
        f"/api/jobs/{job_id}/review-blocks/segment-2",
        json={"text": "Une expression très spécifique."},
    )

    response = client.get("/api/search", params={"q": "très spécifique"})
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["job_id"] == job_id
    assert result["start"] == 4.0
    assert "spécifique" in result["match_text"]


def test_liste_des_travaux_expose_compteur_et_statut_de_publication(client, tmp_path):
    job_id = _job(tmp_path)
    client.get(f"/api/jobs/{job_id}/review-blocks")
    client.post(
        f"/api/jobs/{job_id}/annotations",
        json={"block_id": "segment-1", "type": "review", "status": "a_verifier"},
    )
    client.post(
        f"/api/jobs/{job_id}/annotations",
        json={"block_id": "segment-2", "type": "note", "status": "valide"},
    )

    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == job_id)
    assert job["pending_review_count"] == 1
    assert job["publication_status"] == "non_disponible"

    db.update_job(job_id, status="done", clean_text="Bonjour tout le monde.")
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == job_id)
    assert job["publication_status"] == "pret"

    db.update_job(job_id, obsidian_path="Formation/cours.md")
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == job_id)
    assert job["publication_status"] == "publie"


def test_sync_notebooklm_exige_obsidian_puis_enfile_la_sync(client, tmp_path, monkeypatch):
    job_id = _job(tmp_path)
    db.update_job(job_id, status="published", clean_text="Bonjour.")
    config.save_settings({"notebooklm_sync_enabled": True, "notebooklm_master_doc_id": "doc-maitre"})
    calls = []
    monkeypatch.setattr(pipeline, "enqueue", lambda ident, task: calls.append((ident, task)))

    blocked = client.post(f"/api/jobs/{job_id}/notebooklm-sync")
    assert blocked.status_code == 409

    db.update_job(job_id, obsidian_path="Formation/cours.md")
    response = client.post(f"/api/jobs/{job_id}/notebooklm-sync")
    assert response.status_code == 200
    assert calls == [(job_id, pipeline.TASK_NOTEBOOKLM)]
    assert db.get_job(job_id)["notebooklm_status"] == "en_cours"


def test_initialisation_notebooklm_active_la_sync(client, monkeypatch):
    from app import notebooklm_sync

    monkeypatch.setattr(notebooklm_sync, "initialize_master_doc", lambda: "doc-maitre")

    response = client.post("/api/notebooklm/initialize")

    assert response.status_code == 200
    assert response.json()["master_doc_id"] == "doc-maitre"


def test_test_notebooklm_restitue_le_diagnostic(client, monkeypatch):
    from app import notebooklm_sync

    monkeypatch.setattr(
        notebooklm_sync,
        "sync_master_doc_with_detail",
        lambda _courses: (False, "Jeton Google expiré : initialisez NotebookLM."),
    )

    response = client.post("/api/notebooklm/sync")

    assert response.status_code == 409
    assert "Jeton Google expiré" in response.json()["detail"]


def test_media_source_est_servi_sans_divulguer_son_chemin(client, tmp_path):
    job_id = _job(tmp_path)
    response = client.get(f"/api/jobs/{job_id}/media")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.content == b"source-media"
    assert "media_path" not in client.get(f"/api/jobs/{job_id}").json()

    source = Path(db.get_job(job_id, with_content=False)["media_path"])
    source.unlink()
    assert client.get(f"/api/jobs/{job_id}/media").status_code == 404


def test_review_blocks_propagent_la_confiance_quand_disponible():
    blocks = db.review_blocks_from_segments(
        [{"start": 0.0, "end": 1.0, "text": "x", "confidence": 0.42}]
    )
    assert blocks[0]["confidence"] == 0.42


def test_review_blocks_propagent_mots_et_locuteur():
    blocks = db.review_blocks_from_segments([{
        "start": 0.0, "end": 1.0, "text": "Bonjour", "speaker": "SPEAKER_00",
        "words": [{"start": 0.0, "end": 1.0, "text": "Bonjour", "confidence": 0.9}],
    }])
    assert blocks[0]["speaker"] == "SPEAKER_00"
    assert blocks[0]["words"][0]["text"] == "Bonjour"


def test_review_blocks_tolerent_un_segment_sans_confiance():
    """Compat des travaux transcrits avant l'ajout du champ : pas de KeyError,
    confidence retombe simplement à None (pas de coloration côté client)."""
    blocks = db.review_blocks_from_segments([{"start": 0.0, "end": 1.0, "text": "x"}])
    assert blocks[0]["confidence"] is None


def test_relecture_manuelle_et_identification_des_intervenants(client, tmp_path):
    job_id = _job(tmp_path)
    response = client.post(f"/api/jobs/{job_id}/manual-review", json={"status": "in_progress"})
    assert response.status_code == 200
    assert response.json()["manual_review_status"] == "in_progress"

    response = client.put(
        f"/api/jobs/{job_id}/review-blocks/segment-1",
        json={"role": "professeur", "speaker": "Mme Martin"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "professeur"
    assert response.json()["speaker"] == "Mme Martin"

    response = client.post(f"/api/jobs/{job_id}/manual-review", json={"status": "completed"})
    assert response.status_code == 200
    assert response.json()["manual_review_status"] == "completed"


def test_renommer_un_repere_de_locuteur_se_propage_a_tous_ses_blocs(tmp_path):
    """Whisper répète le même repère (« Speaker 1 ») sur plusieurs passages :
    le renommer sur un bloc doit renommer tous les blocs qui portent encore
    ce repère, pas seulement celui édité."""
    job_id = db.create_job(
        filename="cours.mp4",
        media_path=str(tmp_path / "cours.mp4"),
        size_bytes=0,
        engine="local",
        model="tiny",
        language="fr",
        proofread="basic",
        structure=False,
    )
    db.update_job(
        job_id,
        status="transcribed",
        segments=[
            {"start": 0.0, "end": 1.0, "text": "Un."},
            {"start": 1.0, "end": 2.0, "text": "Deux."},
            {"start": 2.0, "end": 3.0, "text": "Trois."},
        ],
    )
    blocks = db.ensure_review_blocks(job_id)
    for block in blocks:
        block["speaker"] = "Speaker 1" if block["id"] != "segment-3" else "Speaker 2"
    db.update_job(job_id, review_blocks=blocks)

    db.update_review_block(job_id, "segment-1", speaker="Prof")

    updated = db.ensure_review_blocks(job_id)
    by_id = {block["id"]: block for block in updated}
    assert by_id["segment-1"]["speaker"] == "Prof"
    assert by_id["segment-2"]["speaker"] == "Prof"
    assert by_id["segment-3"]["speaker"] == "Speaker 2"


def test_attribuer_un_role_se_propage_aux_blocs_du_meme_locuteur(tmp_path):
    job_id = db.create_job(
        filename="cours.mp4",
        media_path=str(tmp_path / "cours.mp4"),
        size_bytes=0,
        engine="local",
        model="tiny",
        language="fr",
        proofread="basic",
        structure=False,
    )
    db.update_job(
        job_id,
        status="transcribed",
        segments=[
            {"start": 0.0, "end": 1.0, "text": "Un."},
            {"start": 1.0, "end": 2.0, "text": "Deux."},
        ],
    )
    blocks = db.ensure_review_blocks(job_id)
    for block in blocks:
        block["speaker"] = "Prof"
    db.update_job(job_id, review_blocks=blocks)

    db.update_review_block(job_id, "segment-2", role="professeur")

    updated = db.ensure_review_blocks(job_id)
    assert all(block["role"] == "professeur" for block in updated)
