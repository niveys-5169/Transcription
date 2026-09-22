"""Ancrage conservateur des seules entités connues du lexique MJPM."""
from __future__ import annotations

import unicodedata

from ..lexicon import load_lexicon, near_misses
from .textloc import tokenize_words


def _normalised_words(text: str) -> list[str]:
    words = []
    for word, _start, _end in tokenize_words(text or ""):
        words.append("".join(
            char for char in unicodedata.normalize("NFKD", word)
            if not unicodedata.combining(char)
        ))
    return words


def _contains(words: list[str], name: str) -> bool:
    sought = _normalised_words(name)
    if not sought or len(sought) > len(words):
        return False
    width = len(sought)
    return any(words[index:index + width] == sought for index in range(len(words) - width + 1))


def check_grounded_lexicon_entities(raw: str, candidate: str) -> dict:
    """Signale les entités lexiquées ajoutées sans graphie reliée dans le RAW."""
    raw_words = _normalised_words(raw)
    candidate_words = _normalised_words(candidate)
    near_terms = {term.terme for _word, term in near_misses(raw or "")}
    introduced: list[str] = []

    for term in load_lexicon():
        names = [name for name in term.noms() if name and _contains(candidate_words, name)]
        if not names:
            continue
        grounded = any(_contains(raw_words, name) for name in term.noms()) or term.terme in near_terms
        if not grounded:
            introduced.append(term.terme)

    introduced = sorted(set(introduced), key=str.casefold)
    return {"accepted": not introduced, "introduced_entities": introduced}
