"""Fixtures partagées.

Le dossier de données est redirigé vers un répertoire temporaire *avant*
l'import des modules de l'application : ``app.config`` fige ses chemins au
moment de l'import.
"""
from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path

import numpy as np
import pytest

_TMP_DATA = Path(tempfile.mkdtemp(prefix="transcription-tests-"))
os.environ["TRANSCRIPTION_DATA_DIR"] = str(_TMP_DATA)
# Ne pas laisser une vraie clé de l'environnement déclencher des appels réseau.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("RUNPOD_API_KEY", None)

SAMPLE_RATE = 16_000


def write_wav(path: Path, plan: list[tuple[float, float]]) -> Path:
    """Écrit un WAV 16 kHz mono à partir d'une suite (durée, amplitude).

    Une amplitude nulle produit un silence — pratique pour tester le
    découpage sur les blancs.
    """
    blocks = []
    for duration, amplitude in plan:
        count = int(SAMPLE_RATE * duration)
        if amplitude <= 0:
            blocks.append(np.zeros(count, dtype=np.int16))
        else:
            t = np.arange(count, dtype=np.float32) / SAMPLE_RATE
            wave_data = np.sin(2 * np.pi * 440 * t) * amplitude * 32767
            blocks.append(wave_data.astype(np.int16))

    payload = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(payload.tobytes())
    return path


@pytest.fixture
def wav_factory(tmp_path):
    counter = {"n": 0}

    def factory(plan, name: str | None = None) -> Path:
        counter["n"] += 1
        return write_wav(tmp_path / (name or f"sample{counter['n']}.wav"), plan)

    return factory


def segment(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text}
