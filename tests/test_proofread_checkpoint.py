"""Points de reprise durables de la relecture IA."""
from app import db
from app.proofread.base import TextPair
from app.proofread.nim import NimCompletion, NimProofreader
from app.config import Settings
from conftest import segment


def test_checkpoint_relecture_est_persiste_et_recharge(tmp_path, monkeypatch):
    database = tmp_path / "verbatim.sqlite3"
    monkeypatch.setattr(db.config, "DB_PATH", database)
    db.init_db()
    job_id = db.create_job(
        filename="cours.mp3", media_path="cours.mp3", size_bytes=1,
        engine="local", model="tiny", language="fr", proofread="claude",
        structure=True,
    )
    pair = TextPair(
        start=0, end=5, raw="Texte brut.", clean="Texte relu.",
        block_id="block-0-0", source_segment_ids=["segment-0"],
    )

    db.update_job(job_id, review_checkpoint={"pairs": [pair.to_dict()]})

    checkpoint = db.get_job(job_id)["review_checkpoint"]
    assert checkpoint["pairs"] == [pair.to_dict()]


def test_checkpoint_est_efface_apres_finalisation(tmp_path, monkeypatch):
    database = tmp_path / "verbatim.sqlite3"
    monkeypatch.setattr(db.config, "DB_PATH", database)
    db.init_db()
    job_id = db.create_job(
        filename="cours.mp3", media_path="cours.mp3", size_bytes=1,
        engine="local", model="tiny", language="fr", proofread="claude",
        structure=True,
    )
    db.update_job(job_id, review_checkpoint={"pairs": [{"block_id": "block-0-0"}]})
    db.update_job(job_id, review_checkpoint=None)

    assert db.get_job(job_id)["review_checkpoint"] == {}


def test_nim_reutilise_un_bloc_archive_par_claude(monkeypatch):
    raw = "Une phrase source suffisamment longue pour devenir un bloc stable. " * 12
    segments = [segment(0, 10, raw), segment(10, 20, raw)]
    checkpoint = TextPair(
        start=0, end=10, raw=raw.strip(), clean="Bloc relu par Claude.",
        block_id="block-1-1", source_segment_ids=["segment-1"],
    )
    proofreader = NimProofreader(Settings(
        nim_fallback_enabled=True, nim_api_key="nim-test", nim_base_url="https://nim.example",
        proofread_chunk_chars=600,
    ))
    calls = []
    monkeypatch.setattr(
        proofreader, "complete",
        lambda **kwargs: calls.append(kwargs) or NimCompletion(
            text="<transcription>" + "Texte NIM suffisamment long. " * 20 + "</transcription>",
            model="nvidia/test",
        ),
    )

    result = proofreader.proofread(
        segments, structure=False, completed_pairs=[checkpoint],
    )

    assert result.pairs[0] == checkpoint
    assert len(calls) == 1
