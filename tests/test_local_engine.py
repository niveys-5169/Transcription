"""Tests du moteur local (``app/engines/local.py``), sans GPU."""
from __future__ import annotations

import sys
import types

import pytest

from app.engines.local import LocalWhisperEngine, _confidence_from_logprob


class _FauxSegment:
    def __init__(self, start, end, text, avg_logprob=None):
        self.start, self.end, self.text = start, end, text
        self.avg_logprob = avg_logprob


class _FauxInfo:
    duration = 4.5


@pytest.fixture
def moteur(monkeypatch):
    class FauxWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, chemin, **kwargs):
            return (
                iter(
                    [
                        _FauxSegment(0.0, 2.0, "Bonjour à tous.", avg_logprob=-0.2),
                        _FauxSegment(2.0, 4.5, "Segment incertain.", avg_logprob=None),
                    ]
                ),
                _FauxInfo(),
            )

    faux_fw = types.ModuleType("faster_whisper")
    faux_fw.WhisperModel = FauxWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", faux_fw)

    engine = LocalWhisperEngine()
    monkeypatch.setattr(engine, "is_available", lambda: (True, "ok"))
    monkeypatch.setattr(engine, "_load", lambda model: FauxWhisperModel())
    return engine


def test_la_confiance_est_deduite_de_avg_logprob(moteur, tmp_path):
    segments = list(
        moteur.transcribe(
            tmp_path / "audio.wav", model="tiny", language="fr",
            duration=4.5, workdir=tmp_path,
        )
    )
    assert segments[0].confidence is not None
    assert 0.0 <= segments[0].confidence <= 1.0


def test_confidence_none_quand_avg_logprob_absent(moteur, tmp_path):
    segments = list(
        moteur.transcribe(
            tmp_path / "audio.wav", model="tiny", language="fr",
            duration=4.5, workdir=tmp_path,
        )
    )
    assert segments[1].confidence is None


def test_confidence_from_logprob_mappe_vers_0_1():
    assert _confidence_from_logprob(None) is None
    assert _confidence_from_logprob(0.0) == 1.0
    assert _confidence_from_logprob(-1.5) == 0.0
    assert 0.0 <= _confidence_from_logprob(-0.75) <= 1.0
