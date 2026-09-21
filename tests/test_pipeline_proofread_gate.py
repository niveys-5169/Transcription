"""run_proofread : la vérification sémantique s'applique à NIM aussi.

Avant : ``use_claude=... and result.mode == "claude"`` empêchait toute
comparaison Claude/brut d'une relecture NIM, même avec verify=True et Claude
disponible. Ces tests verrouillent le comportement corrigé, ainsi que la
remontée des rejets NIM dans les findings.
"""
import pytest

from app import config, db, pipeline
from app.proofread.base import ProofreadResult, TextPair


def _job_id(**overrides):
    job_id = db.create_job(
        filename="cours.mp4", media_path="/tmp/x.mp4", size_bytes=10,
        engine="local", model="tiny", language="fr",
        proofread="nim", structure=False, verify=True,
        chain=False, factcheck=False, publish=False,
    )
    segments = [{"start": 0.0, "end": 2.0, "text": "Le texte brut du cours."}]
    db.mark_finished(
        job_id, status="transcribed", stage="Transcrit", progress=1.0, task=None,
        segments=segments, review_blocks=db.review_blocks_from_segments(segments),
        raw_text="Le texte brut du cours.",
    )
    if overrides:
        db.update_job(job_id, **overrides)
    return job_id


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    db.init_db(config.DB_PATH)
    yield


def _fake_nim_result():
    pair = TextPair(start=0.0, end=2.0, raw="Le texte brut du cours.", clean="Le texte brut du cours, relu.",
                     block_id="block-1-1", source_segment_ids=["segment-1"])
    return ProofreadResult(
        text=pair.clean, mode="nim", pairs=[pair],
        rejections=[{"block_id": "block-2-2", "model": "nvidia/test", "reject_reason": "reasoning_leak",
                     "validation_status": "invalid", "raw_word_count": 5, "candidate_word_count": 40,
                     "added_word_ratio": 0.9}],
        block_log=[{"block_id": "block-1-1", "model": "nvidia/test", "validation_status": "valid"}],
    )


def test_verify_true_appelle_claude_meme_pour_une_relecture_nim(monkeypatch):
    job_id = _job_id()
    monkeypatch.setattr(pipeline, "_proofread", lambda *a, **k: _fake_nim_result())

    claude_calls = []

    class _FakeVerifier:
        def __init__(self, settings=None):
            pass

        def is_available(self):
            return True, "ok"

        def verify(self, pairs, *, on_progress=None, should_cancel=None):
            claude_calls.append(list(pairs))
            return []

    monkeypatch.setattr("app.proofread.verify.ClaudeVerifier", _FakeVerifier)

    pipeline.run_proofread(job_id)

    job = db.get_job(job_id)
    assert job["status"] == "done"
    assert job["verification"]["mode"] == "claude"
    assert claude_calls, "Claude aurait dû être appelé pour vérifier la relecture NIM"


def test_claude_indisponible_ne_fait_pas_echouer_le_travail(monkeypatch):
    job_id = _job_id()
    monkeypatch.setattr(pipeline, "_proofread", lambda *a, **k: _fake_nim_result())

    class _UnavailableVerifier:
        def __init__(self, settings=None):
            pass

        def is_available(self):
            return False, "Claude indisponible."

    monkeypatch.setattr("app.proofread.verify.ClaudeVerifier", _UnavailableVerifier)

    pipeline.run_proofread(job_id)

    job = db.get_job(job_id)
    assert job["status"] == "done"
    assert job["clean_text"]
    assert job["verification"]["mode"] == "regles"


def test_un_bloc_rejete_par_nim_devient_un_finding_visible(monkeypatch):
    job_id = _job_id()
    monkeypatch.setattr(pipeline, "_proofread", lambda *a, **k: _fake_nim_result())

    class _UnavailableVerifier:
        def __init__(self, settings=None):
            pass

        def is_available(self):
            return False, "Claude indisponible."

    monkeypatch.setattr("app.proofread.verify.ClaudeVerifier", _UnavailableVerifier)

    pipeline.run_proofread(job_id)

    job = db.get_job(job_id)
    findings = job["verification"]["findings"]
    rejets = [f for f in findings if f["kind"] == "candidat_rejete"]
    assert len(rejets) == 1
    assert rejets[0]["block_id"] == "block-2-2"
    assert "reasoning" in rejets[0]["message"] or "raisonnement" in rejets[0]["message"]

    assert job["review_engine_detail"] is not None
    import json
    detail = json.loads(job["review_engine_detail"])
    assert detail["engine"] == "nim"
    assert "nvidia/test" in detail["models_used"]
