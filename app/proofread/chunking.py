"""Découpage du texte brut en blocs relisables.

Un cours d'une heure fait 50 000 à 70 000 caractères. Tout envoyer d'un coup
tiendrait dans la fenêtre de contexte, mais la qualité de relecture se dégrade
sur un bloc trop long et la moindre erreur fait tout recommencer. On découpe
donc en blocs, sur des frontières de segments, avec un rappel du contexte
précédent pour que les phrases à cheval restent cohérentes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SENTENCE_END = re.compile(r"[.!?…][\"»')\]]?\s*$")


@dataclass
class TextChunk:
    index: int
    start: float
    end: float
    text: str


def segments_to_text(segments) -> str:
    """Concatène des segments en un texte brut, sans mise en forme."""
    parts = []
    for segment in segments:
        text = _text_of(segment).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _text_of(segment) -> str:
    if isinstance(segment, dict):
        return segment.get("text", "") or ""
    return getattr(segment, "text", "") or ""


def _bounds_of(segment) -> tuple[float, float]:
    if isinstance(segment, dict):
        return float(segment.get("start", 0.0)), float(segment.get("end", 0.0))
    return float(getattr(segment, "start", 0.0)), float(getattr(segment, "end", 0.0))


def build_chunks(segments, max_chars: int = 6000) -> list[TextChunk]:
    """Regroupe les segments en blocs d'au plus ``max_chars`` caractères.

    Le découpage tombe de préférence en fin de phrase : dès qu'on dépasse 70 %
    de la taille cible et qu'un segment se termine par une ponctuation forte,
    on ferme le bloc.
    """
    max_chars = max(500, int(max_chars))
    soft_limit = int(max_chars * 0.7)

    chunks: list[TextChunk] = []
    buffer: list[str] = []
    length = 0
    start: float | None = None
    end = 0.0

    def flush() -> None:
        nonlocal buffer, length, start, end
        if buffer:
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    start=round(start or 0.0, 3),
                    end=round(end, 3),
                    text=" ".join(buffer).strip(),
                )
            )
        buffer, length, start = [], 0, None

    for segment in segments:
        text = _text_of(segment).strip()
        if not text:
            continue
        seg_start, seg_end = _bounds_of(segment)
        if start is None:
            start = seg_start
        end = seg_end

        buffer.append(text)
        length += len(text) + 1

        if length >= max_chars or (
            length >= soft_limit and SENTENCE_END.search(text)
        ):
            flush()

    flush()
    return chunks


def tail(text: str, max_chars: int = 400) -> str:
    """Fin d'un texte, coupée sur une frontière de mot — sert de contexte."""
    if len(text) <= max_chars:
        return text
    excerpt = text[-max_chars:]
    space = excerpt.find(" ")
    return excerpt[space + 1 :] if space != -1 else excerpt
