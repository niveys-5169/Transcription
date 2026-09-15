"""reset_interrupted() doit aussi débloquer une synchro NotebookLM coupée
par un redémarrage — sinon notebooklm_status reste bloqué à « en_cours »
pour toujours (le job.status général ne change pas pendant cette étape,
donc pending_tasks() ne la reprend jamais) et le bouton reste grisé.
"""
from __future__ import annotations

import pytest

from app import config, db


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.sqlite3")
    db.init_db()


def _make_job() -> str:
    return db.create_job(
        filename="cours.wav",
        media_path="/tmp/cours.wav",
        size_bytes=1,
        engine="local",
        model="base",
        language="fr",
        proofread="claude",
        structure=True,
    )


def test_reset_interrupted_debloque_une_synchro_notebooklm_coupee():
    job_id = _make_job()
    db.update_job(
        job_id,
        status="published",
        obsidian_path="cours/vault/cours.md",
        notebooklm_status="en_cours",
        notebooklm_error=None,
    )

    db.reset_interrupted()

    job = db.get_job(job_id, with_content=False)
    assert job["status"] == "published"
    assert job["notebooklm_status"] == "erreur"
    assert job["notebooklm_error"]


def test_reset_interrupted_laisse_les_autres_statuts_notebooklm_intacts():
    job_id = _make_job()
    db.update_job(
        job_id,
        status="published",
        obsidian_path="cours/vault/cours.md",
        notebooklm_status="synchronise",
    )

    db.reset_interrupted()

    job = db.get_job(job_id, with_content=False)
    assert job["notebooklm_status"] == "synchronise"
