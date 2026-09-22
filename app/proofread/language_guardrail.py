"""Heuristique locale étroite contre les raisonnements NIM en anglais."""
from __future__ import annotations

from collections import Counter

from .textloc import tokenize_words

MIN_CANDIDATE_WORDS = 8
MIN_RAW_FRENCH_MARKERS = 2
MIN_ENGLISH_MARKERS = 4
MIN_ENGLISH_MARKER_RATIO = 0.30
LOCAL_WINDOW_WORDS = 12

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


def _is_english_reasoning(words: list[str]) -> bool:
    english = sum(word in _ENGLISH_MARKERS for word in words)
    french = sum(word in _FRENCH_MARKERS for word in words)
    ratio = english / len(words) if words else 0.0
    return english >= MIN_ENGLISH_MARKERS and ratio >= MIN_ENGLISH_MARKER_RATIO and english > french


def check_language_consistency(raw: str, candidate: str) -> dict:
    """Rejette un bloc nettement anglais, global ou local, absent du RAW français."""
    raw_words = _words(raw)
    candidate_words = _words(candidate)
    raw_french = sum(word in _FRENCH_MARKERS for word in raw_words)
    applicable = len(candidate_words) >= MIN_CANDIDATE_WORDS and raw_french >= MIN_RAW_FRENCH_MARKERS
    if not applicable:
        return {"applicable": False, "accepted": True, "reason": None}

    raw_english = Counter(word for word in raw_words if word in _ENGLISH_MARKERS)
    candidate_english = Counter(word for word in candidate_words if word in _ENGLISH_MARKERS)
    introduced_english = sum((candidate_english - raw_english).values())
    if introduced_english < MIN_ENGLISH_MARKERS:
        return {"applicable": True, "accepted": True, "reason": None}

    global_mismatch = _is_english_reasoning(candidate_words)
    local_mismatch = any(
        _is_english_reasoning(candidate_words[index:index + LOCAL_WINDOW_WORDS])
        for index in range(len(candidate_words) - LOCAL_WINDOW_WORDS + 1)
    )
    mismatch = global_mismatch or local_mismatch
    return {
        "applicable": True,
        "accepted": not mismatch,
        "reason": (
            "embedded_english_reasoning" if local_mismatch and not global_mismatch
            else "english_reasoning_block" if mismatch
            else None
        ),
    }
