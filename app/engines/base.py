"""Contrat commun aux moteurs de transcription."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterator, Protocol


class TranscriptionError(RuntimeError):
    """Erreur levée par un moteur de transcription."""


@dataclass
class Segment:
    """Un fragment de texte transcrit, horodaté dans le média d'origine."""

    start: float
    end: float
    text: str
    confidence: float | None = None
    """Score indicatif [0, 1] dérivé du moteur (ex. avg_logprob) ; n'affecte
    jamais le texte ni son édition — purement informatif pour l'affichage."""

    def shifted(self, offset: float) -> "Segment":
        return Segment(
            start=round(self.start + offset, 3),
            end=round(self.end + offset, 3),
            text=self.text,
            confidence=self.confidence,
        )

    def to_dict(self) -> dict:
        return asdict(self)


ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class TranscriptionEngine(Protocol):
    """Interface implémentée par chaque moteur.

    ``transcribe`` est un générateur : les segments sont émis au fil de l'eau,
    ce qui permet d'afficher une vraie progression plutôt qu'une attente
    opaque jusqu'à la fin du fichier.
    """

    name: str
    label: str

    def is_available(self) -> tuple[bool, str]:
        """(disponible, explication lisible)."""

    def transcribe(
        self,
        wav_path: Path,
        *,
        model: str,
        language: str | None,
        duration: float,
        workdir: Path,
        initial_prompt: str | None = None,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> Iterator[Segment]:
        ...
