"""Contrôle local de l'alignement du début et du contenu d'un candidat."""
from __future__ import annotations

import unicodedata

from .textloc import tokenize_words

WINDOW_WORDS = 8
WINDOW_SUPPORT_FLOOR = 0.70
OPENING_LINE_SUPPORT_FLOOR = 0.50
MIN_WORDS = 8
MIN_OPENING_WORDS = 4


def _words(text: str) -> list[str]:
    """Mots comparables, sans accents, casse ni ponctuation."""
    return [
        "".join(
            char for char in unicodedata.normalize("NFKD", word)
            if not unicodedata.combining(char)
        )
        for word, _start, _end in tokenize_words(text or "")
    ]


def check_prefix_alignment(
    raw: str,
    candidate: str,
    *,
    window_words: int = WINDOW_WORDS,
    support_floor: float = WINDOW_SUPPORT_FLOOR,
    opening_line_support_floor: float = OPENING_LINE_SUPPORT_FLOOR,
) -> dict:
    """Vérifie qu'une sortie longue est ancrée et sans première ligne parasite."""
    raw_words = _words(raw)
    candidate_words = _words(candidate)
    required_words = max(MIN_WORDS, window_words)
    result = {
        "applicable": len(raw_words) >= required_words and len(candidate_words) >= required_words,
        "accepted": True,
        "opening_support_ratio": None,
        "best_window_support_ratio": None,
    }
    if not result["applicable"]:
        return result

    raw_words_set = set(raw_words)
    window_ratios = [
        sum(word in raw_words_set for word in candidate_words[index:index + window_words]) / window_words
        for index in range(len(candidate_words) - window_words + 1)
    ]
    best_ratio = max(window_ratios, default=0.0)
    result["best_window_support_ratio"] = round(best_ratio, 4)
    if best_ratio < support_floor:
        result["accepted"] = False

    non_empty_lines = [line for line in (candidate or "").splitlines() if line.strip()]
    if len(non_empty_lines) > 1:
        opening_words = _words(non_empty_lines[0])
        if len(opening_words) >= MIN_OPENING_WORDS:
            opening_ratio = sum(word in raw_words_set for word in opening_words) / len(opening_words)
            result["opening_support_ratio"] = round(opening_ratio, 4)
            if opening_ratio < opening_line_support_floor:
                result["accepted"] = False

    return result
