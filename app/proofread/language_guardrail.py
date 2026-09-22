"""Heuristique locale étroite contre les raisonnements NIM en anglais."""
from __future__ import annotations

from .textloc import tokenize_words

MIN_CANDIDATE_WORDS = 8
MIN_RAW_FRENCH_MARKERS = 2
MIN_ENGLISH_MARKERS = 4
MIN_ENGLISH_MARKER_RATIO = 0.30

# Mots fonctionnels et marqueurs de raisonnement, hors anglicismes techniques
# usuels. Le doute accepte : plusieurs signaux concordants sont nécessaires.
_FRENCH_MARKERS = {
    "alors", "au", "aux", "avec", "ce", "ces", "cette", "dans", "de",
    "des", "du", "elle", "en", "est", "et", "il", "la", "le", "les",
    "nous", "on", "pour", "que", "qui", "une", "vous",
}
_ENGLISH_MARKERS = {
    "a", "an", "and", "are", "as", "be", "best", "determine", "how", "i",
    "in", "is", "need", "of", "passage", "request", "rewrite", "rules",
    "should", "the", "this", "to", "user", "we", "will", "with",
}


def _words(text: str) -> list[str]:
    return [word for word, _start, _end in tokenize_words(text or "")]


def check_language_consistency(raw: str, candidate: str) -> dict:
    """Rejette seulement un long bloc nettement anglais face à un RAW français."""
    raw_words = _words(raw)
    candidate_words = _words(candidate)
    raw_french = sum(word in _FRENCH_MARKERS for word in raw_words)
    applicable = len(candidate_words) >= MIN_CANDIDATE_WORDS and raw_french >= MIN_RAW_FRENCH_MARKERS
    if not applicable:
        return {"applicable": False, "accepted": True, "reason": None}

    english = sum(word in _ENGLISH_MARKERS for word in candidate_words)
    french = sum(word in _FRENCH_MARKERS for word in candidate_words)
    ratio = english / len(candidate_words)
    mismatch = english >= MIN_ENGLISH_MARKERS and ratio >= MIN_ENGLISH_MARKER_RATIO and english > french
    return {
        "applicable": True,
        "accepted": not mismatch,
        "reason": "english_reasoning_block" if mismatch else None,
    }
