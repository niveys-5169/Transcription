"""Relecture déterministe, sans appel réseau ni clé API.

C'est le mode par défaut quand aucune clé Anthropic n'est configurée, et le
filet de sécurité quand la relecture par Claude échoue. Elle ne « comprend »
rien au contenu : elle se contente de ce qu'on peut corriger mécaniquement —
hésitations, bégaiements, ponctuation, majuscules, paragraphes.
"""
from __future__ import annotations

import re

from dataclasses import dataclass

from .base import ProofreadResult, TextPair
from .chunking import _bounds_of, _text_of

# Interjections d'oral, retirées seulement quand elles forment un mot entier.
FILLERS = (
    "euh",
    "euhh",
    "heu",
    "heuh",
    "heum",
    "hum",
    "humm",
    "hmm",
    "mmh",
    "mmm",
    "bah",
)
_FILLER_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(FILLERS) + r")(?![\w-])\s*[,]?\s*",
    re.IGNORECASE,
)

# Mots dont le doublement est correct en français : on ne les dédouble pas.
LEGIT_DOUBLES = {"nous", "vous", "très", "si", "cha"}
_REPEAT_RE = re.compile(r"(?<![\w-])(\w+)(?:\s+\1)+(?![\w-])", re.IGNORECASE)

_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_SIMPLE = re.compile(r"\s+([,.])")
_SPACE_BEFORE_DOUBLE = re.compile(r"\s*([;:!?»])")
_SPACE_AFTER_OPEN = re.compile(r"([«])\s*")
_SPACE_BEFORE_OPEN = re.compile(r"(\w)([«])")
_SENTENCE_START = re.compile(r"(^|[.!?…]\s+)([a-zà-öø-ÿ])")
_ORPHAN_PUNCT = re.compile(r"^\s*[,;:.]\s*")

# Une pause plus longue que ça, dans un cours, marque un changement d'idée.
PARAGRAPH_PAUSE = 1.2
MIN_PARAGRAPH_CHARS = 260
MAX_PARAGRAPH_CHARS = 1100


def strip_fillers(text: str) -> str:
    return _FILLER_RE.sub("", text)


def collapse_repeats(text: str) -> str:
    """« je je pense » → « je pense » (bégaiement typique de l'oral)."""

    def replace(match: re.Match) -> str:
        word = match.group(1)
        if word.lower() in LEGIT_DOUBLES:
            return match.group(0)
        return word

    return _REPEAT_RE.sub(replace, text)


def fix_typography(text: str) -> str:
    """Espaces et majuscules conformes à l'usage français."""
    text = _MULTISPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_SIMPLE.sub(r"\1", text)
    text = _SPACE_BEFORE_DOUBLE.sub(r" \1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1 ", text)
    text = _SPACE_BEFORE_OPEN.sub(r"\1 \2", text)
    text = _ORPHAN_PUNCT.sub("", text)
    text = _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text.strip()


def clean_line(text: str) -> str:
    """Nettoyage mécanique complet d'un fragment de texte."""
    return fix_typography(collapse_repeats(strip_fillers(text)))


@dataclass
class Paragraph:
    """Un paragraphe brut, avec sa position dans l'enregistrement."""

    text: str
    start: float
    end: float


def split_paragraph_spans(segments) -> list[Paragraph]:
    """Regroupe les segments en paragraphes, en s'appuyant sur les pauses."""
    paragraphs: list[Paragraph] = []
    current: list[str] = []
    length = 0
    start_at: float | None = None
    previous_end: float | None = None

    for segment in segments:
        text = _text_of(segment).strip()
        if not text:
            continue
        start, end = _bounds_of(segment)

        pause = start - previous_end if previous_end is not None else 0.0
        should_break = current and (
            (pause >= PARAGRAPH_PAUSE and length >= MIN_PARAGRAPH_CHARS)
            or length >= MAX_PARAGRAPH_CHARS
        )
        if should_break:
            paragraphs.append(
                Paragraph(" ".join(current), start_at or 0.0, previous_end or 0.0)
            )
            current, length, start_at = [], 0, None

        if start_at is None:
            start_at = start
        current.append(text)
        length += len(text) + 1
        previous_end = end

    if current:
        paragraphs.append(
            Paragraph(" ".join(current), start_at or 0.0, previous_end or 0.0)
        )
    return paragraphs


def split_paragraphs(segments) -> list[str]:
    """Paragraphes bruts, texte seul."""
    return [paragraph.text for paragraph in split_paragraph_spans(segments)]


def basic_proofread(segments) -> ProofreadResult:
    """Relecture mécanique d'une liste de segments."""
    pairs: list[TextPair] = []
    for paragraph in split_paragraph_spans(segments):
        nettoye = clean_line(paragraph.text)
        if nettoye:
            pairs.append(
                TextPair(
                    start=paragraph.start,
                    end=paragraph.end,
                    raw=paragraph.text,
                    clean=nettoye,
                )
            )

    body = "\n\n".join(pair.clean for pair in pairs)
    return ProofreadResult(text=body, mode="basic", pairs=pairs)
