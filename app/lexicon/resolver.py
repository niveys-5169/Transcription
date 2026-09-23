"""Résolution locale des graphies validées avant le repérage par IA.

Seules les entrées explicitement vérifiées sont protégées. Les correspondances
approximatives restent des pistes de relecture, jamais une preuve.
"""
from __future__ import annotations

import re
import unicodedata

from . import Term, load_lexicon

_ACCENTS = {
    "a": "aàâä", "c": "cç", "e": "eéèêë", "i": "iîï",
    "o": "oôö", "u": "uùûü", "y": "yÿ",
}


def _letter_pattern(char: str) -> str:
    base = normalize(char)
    return f"[{_ACCENTS[base]}]" if base in _ACCENTS else re.escape(char)


def _alias_pattern(name: str) -> str:
    compact = name.replace(".", "")
    if compact.isupper() and len(compact) <= 8 and compact.isalpha() and " " not in name:
        return r"(?<!\w)" + r"\.?\s*".join(map(re.escape, compact)) + r"\.?(?!\w)"
    parts = []
    for part in re.findall(r"\w+|[^\w]+", name, flags=re.UNICODE):
        if part[0].isalnum():
            parts.append("".join(_letter_pattern(char) for char in part))
        elif part.isspace():
            parts.append(r"\s+")
        elif part.strip() in ("'", "’"):
            parts.append(r"['’]")
        else:
            parts.append(re.escape(part))
    return r"(?<!\w)" + "".join(parts) + r"(?!\w)"


def normalize(value: str) -> str:
    """Compare casse, accents, espaces et points des sigles sans fuzzy match."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return "".join(char for char in value if char.isalnum())


def resolve(value: str) -> Term | None:
    """Résout une graphie entière, uniquement si elle désigne un seul terme."""
    key = normalize(value)
    if not key:
        return None
    matches = [
        term for term in load_lexicon() if term.verifie
        and any(normalize(name) == key for name in term.noms())
    ]
    return matches[0] if len(matches) == 1 else None


def shield_trusted(text: str) -> tuple[str, list[dict]]:
    """Masque les graphies fiables sans déplacer les autres citations.

Le masque garde les sauts de ligne et la longueur du texte pour préserver les
positions approximatives des affirmations extraites ensuite. Un sigle doit
être borné par des caractères non alphanumériques : « ARS » dans « mars »
ne constitue jamais une correspondance.
    """
    aliases: dict[str, list[Term]] = {}
    for term in load_lexicon():
        if term.verifie:
            for name in term.noms():
                if name.strip():
                    aliases.setdefault(normalize(name), []).append(term)
    spans: list[tuple[int, int, Term]] = []
    for key, terms in aliases.items():
        if len({term.terme for term in terms}) != 1:
            continue
        term = terms[0]
        for name in term.noms():
            if normalize(name) != key:
                continue
            pattern = _alias_pattern(name)
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                if normalize(match.group()) == key:
                    spans.append((match.start(), match.end(), term))
    # Préférer l'expression la plus longue lorsqu'un alias est inclus dedans.
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[tuple[int, int, Term]] = []
    for start, end, term in spans:
        if chosen and start < chosen[-1][1]:
            continue
        chosen.append((start, end, term))
    chars = list(text)
    recognized: list[dict] = []
    for start, end, term in chosen:
        recognized.append({"citation": text[start:end], "terme": term.terme})
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars), recognized
