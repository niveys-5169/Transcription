"""Localisation tolérante d'un passage à l'intérieur d'un texte plus long.

Comparer deux transcriptions caractère pour caractère échoue au moindre écart
de ponctuation, de casse ou d'espacement — un modèle ne recopie jamais une
citation à l'identique, et une relecture ne raccourcit pas toujours au même
endroit qu'elle a été découpée. Ces fonctions comparent sur une empreinte
alphanumérique et savent remonter à la position d'origine dans le texte
source. Partagé par ``verify.py`` (situer une anomalie), ``factcheck.py``
(retrouver une citation) et ``structure.py`` (insérer un intertitre).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_WORD_RE = re.compile(r"\w+", re.UNICODE)

Opcode = tuple[str, int, int, int, int]


def fingerprint(text: str) -> tuple[str, list[int]]:
    """Version alphanumérique de ``text`` + position d'origine de chaque caractère.

    Comparer sur les seuls caractères alphanumériques rend la recherche
    insensible à la ponctuation, aux espaces et à la casse.
    """
    letters: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char.isalnum():
            letters.append(char.lower())
            positions.append(index)
    return "".join(letters), positions


def locate(haystack: str, needle: str, *, min_len: int = 4) -> tuple[int, int] | None:
    """Position de ``needle`` dans ``haystack``, tolérante à la ponctuation et à la casse.

    ``None`` si ``needle`` est vide, trop court pour être un repère fiable, ou
    introuvable.
    """
    needle = needle.strip()
    if not needle:
        return None
    haystack_fp, positions = fingerprint(haystack)
    needle_fp, _ = fingerprint(needle)
    if len(needle_fp) < min_len:
        return None
    found = haystack_fp.find(needle_fp)
    if found == -1:
        return None
    start = positions[found]
    end = positions[found + len(needle_fp) - 1] + 1
    return start, end


def tokenize_words(text: str) -> list[tuple[str, int, int]]:
    """Mots de ``text`` (normalisés en minuscule) avec leurs bornes d'origine."""
    return [(match.group().lower(), match.start(), match.end()) for match in _WORD_RE.finditer(text)]


def word_opcodes(raw: str, clean: str) -> list[Opcode]:
    """Alignement mot-à-mot entre ``raw`` et ``clean``, façon suivi de modifications.

    Chaque opcode ``(tag, i1, i2, j1, j2)`` (voir ``difflib.SequenceMatcher``)
    porte des index dans les *listes de mots*, pas des positions caractère —
    à combiner avec ``tokenize_words`` pour retrouver les bornes d'origine.
    """
    raw_words = [word for word, _, _ in tokenize_words(raw)]
    clean_words = [word for word, _, _ in tokenize_words(clean)]
    matcher = SequenceMatcher(None, raw_words, clean_words, autojunk=False)
    return matcher.get_opcodes()
