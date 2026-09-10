"""Moteurs de transcription interchangeables."""
from __future__ import annotations

from .base import Segment, TranscriptionEngine, TranscriptionError
from .local import LocalWhisperEngine
from .runpod import RunPodEngine

_ENGINES: dict[str, type[TranscriptionEngine]] = {
    "local": LocalWhisperEngine,
    "runpod": RunPodEngine,
}


def get_engine(name: str) -> TranscriptionEngine:
    try:
        return _ENGINES[name]()
    except KeyError:
        raise TranscriptionError(
            f"Moteur de transcription inconnu : « {name} ». "
            f"Choix possibles : {', '.join(_ENGINES)}."
        ) from None


def availability() -> dict[str, dict]:
    """État de chaque moteur, pour l'écran d'accueil."""
    report = {}
    for name, factory in _ENGINES.items():
        engine = factory()
        available, detail = engine.is_available()
        report[name] = {"available": available, "detail": detail, "label": engine.label}
    return report


__all__ = [
    "Segment",
    "TranscriptionEngine",
    "TranscriptionError",
    "get_engine",
    "availability",
]
