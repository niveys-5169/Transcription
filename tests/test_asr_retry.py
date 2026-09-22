from pathlib import Path

from app.engines.base import Segment, Word
from app.proofread import asr_quality
from app.proofread.asr_retry import (
    apply_asr_retries,
    build_retry_clip_bounds,
    choose_asr_candidate,
    effective_asr_segments,
    retry_suspect_segment,
)


def test_segment_normal_ne_declenche_pas_de_retry(tmp_path):
    class Engine:
        name = "fake"

        def transcribe(self, *args, **kwargs):
            raise AssertionError("aucun appel ASR attendu")

    segments = [{"start": 0.0, "end": 3.0, "text": "Bonjour à tous."}]
    apply_asr_retries(
        segments, asr_quality.check_segments(segments),
        audio_path=tmp_path / "audio.wav", media_duration=3.0,
        engine=Engine(), model="large-v3", language="fr", initial_prompt=None,
        workdir=tmp_path,
    )
    assert "asr_retry" not in segments[0]


def test_segment_seulement_long_ne_declenche_pas_de_retry():
    assert asr_quality.should_retry_asr("segment_anormalement_long") is False


def test_repetition_corrigee_utilise_retry():
    decision = choose_asr_candidate(
        "la tutelle la tutelle la tutelle la tutelle la tutelle la tutelle",
        "La tutelle est prononcée lorsque l'altération des facultés le justifie.",
        "boucle_de_tokens",
    )
    assert decision.action == "use_retry"


def test_repetition_non_corrigee_demande_revue():
    decision = choose_asr_candidate(
        "la tutelle la tutelle la tutelle la tutelle la tutelle la tutelle",
        "la mesure la mesure la mesure la mesure la mesure la mesure",
        "boucle_de_tokens",
    )
    assert decision.action == "needs_review"


def test_unk_reduit_utilise_retry():
    decision = choose_asr_candidate(
        "le juge <unk> <unk> la mesure <unk>",
        "Le juge peut renouveler la mesure.",
        "unk_ratio_eleve",
    )
    assert decision.action == "use_retry"


def test_divergence_juridique_demande_revue():
    decision = choose_asr_candidate(
        "article 472 du Code civil", "article 473 du Code civil",
        "unk_ratio_eleve",
    )
    assert decision.action == "needs_review"
    assert decision.reasons == ["sensitive_reference_divergence"]


def test_bornes_du_clip_au_debut_et_a_la_fin():
    assert build_retry_clip_bounds(1.0, 4.0, media_duration=100.0) == (0.0, 7.0)
    assert build_retry_clip_bounds(97.0, 100.0, media_duration=100.0) == (94.0, 100.0)


def test_timestamps_recalés_et_contexte_exclu(monkeypatch, tmp_path):
    def fake_extract(_src, dst, **_kwargs):
        Path(dst).write_bytes(b"RIFF" + b"\0" * 44)

    class Engine:
        name = "fake"

        def transcribe(self, *args, **kwargs):
            yield Segment(0.1, 2.0, "contexte", words=[Word(0.1, 1.0, "contexte")])
            yield Segment(4.2, 11.8, "passage corrigé", words=[
                Word(4.2, 5.0, "passage"), Word(5.1, 6.0, "corrigé"),
            ])
            yield Segment(14.0, 15.0, "après", words=[Word(14.0, 15.0, "après")])

    monkeypatch.setattr("app.proofread.asr_retry.media.extract_wav_clip", fake_extract)
    candidate = retry_suspect_segment(
        tmp_path / "audio.wav",
        {"start": 120.0, "end": 130.0, "text": "ancien"},
        ["unk_ratio_eleve"], media_duration=200.0, engine=Engine(),
        model="large-v3", language="fr", initial_prompt=None, workdir=tmp_path,
    )
    assert candidate.clip_start == 117.0
    assert candidate.retry_text == "passage corrigé"
    assert candidate.retry_segments[0]["start"] == 121.2
    assert candidate.retry_words[0]["start"] == 121.2


def test_deux_anomalies_du_meme_segment_ne_font_qu_un_appel(monkeypatch, tmp_path):
    calls = []

    def fake_extract(_src, dst, **_kwargs):
        Path(dst).write_bytes(b"RIFF" + b"\0" * 44)

    class Engine:
        name = "fake"

        def transcribe(self, *args, **kwargs):
            calls.append(1)
            yield Segment(3.0, 6.0, "texte corrigé sans boucle")

    monkeypatch.setattr("app.proofread.asr_retry.media.extract_wav_clip", fake_extract)
    original = "<unk> <unk> mot mot mot mot mot mot"
    segments = [{"start": 3.0, "end": 6.0, "text": original}]
    issues = [
        {"segment_index": 1, "issue": "boucle_de_tokens"},
        {"segment_index": 1, "issue": "unk_ratio_eleve"},
    ]
    apply_asr_retries(
        segments, issues, audio_path=tmp_path / "audio.wav", media_duration=10.0,
        engine=Engine(), model="large-v3", language="fr", initial_prompt=None,
        workdir=tmp_path,
    )
    assert len(calls) == 1
    assert segments[0]["text"] == original
    assert segments[0]["asr_retry"]["issues"] == ["boucle_de_tokens", "unk_ratio_eleve"]
    assert effective_asr_segments(segments)[0]["text"] == "texte corrigé sans boucle"


def test_erreur_technique_du_retry_conserve_original(monkeypatch, tmp_path):
    def fake_extract(_src, dst, **_kwargs):
        Path(dst).write_bytes(b"RIFF" + b"\0" * 44)

    class Engine:
        name = "fake"

        def transcribe(self, *args, **kwargs):
            raise RuntimeError("panne simulée")
            yield  # pragma: no cover

    monkeypatch.setattr("app.proofread.asr_retry.media.extract_wav_clip", fake_extract)
    segments = [{
        "start": 0.0, "end": 5.0,
        "text": "mot mot mot mot mot mot",
    }]
    apply_asr_retries(
        segments, [{"segment_index": 1, "issue": "boucle_de_tokens"}],
        audio_path=tmp_path / "audio.wav", media_duration=5.0,
        engine=Engine(), model="large-v3", language="fr", initial_prompt=None,
        workdir=tmp_path,
    )
    assert segments[0]["text"] == "mot mot mot mot mot mot"
    assert segments[0]["asr_retry"]["retry_failed"] is True
    assert segments[0]["asr_retry"]["decision"] == "keep_original"
