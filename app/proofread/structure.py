"""Insertion des intertitres dans un texte relu, sans le réécrire.

Faire réécrire tout le texte au modèle pour qu'il y glisse des intertitres
coûterait le double et risquerait de raccourcir le contenu au passage. On lui
demande donc seulement *où* couper — sous forme d'une citation exacte du début
de chaque section — et on insère les titres nous-mêmes. Le texte relu n'est
jamais modifié, seulement complété.
"""
from __future__ import annotations

import json
import re

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_json_object(raw: str) -> dict:
    """Extrait un objet JSON d'une réponse de modèle, tolérant aux enrobages."""
    if not raw:
        return {}
    text = raw.strip()

    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    # Dernier recours : la portion entre la première accolade ouvrante et la
    # dernière fermante.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def parse_json_array(raw: str) -> list:
    """Extrait un tableau JSON d'une réponse de modèle, tolérant aux enrobages."""
    if not raw:
        return []
    text = raw.strip()

    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        pass

    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _fingerprint(text: str) -> tuple[str, list[int]]:
    """Version alphanumérique du texte + position d'origine de chaque caractère.

    Comparer sur les seuls caractères alphanumériques rend la recherche
    insensible à la ponctuation, aux espaces et à la casse — le modèle recopie
    rarement une citation au caractère près.
    """
    letters: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char.isalnum():
            letters.append(char.lower())
            positions.append(index)
    return "".join(letters), positions


def _paragraph_start(text: str, position: int) -> int:
    """Début du paragraphe contenant ``position``."""
    boundary = text.rfind("\n\n", 0, position)
    return 0 if boundary == -1 else boundary + 2


def insert_headings(text: str, sections: list[dict]) -> str:
    """Insère les intertitres aux endroits désignés par les citations.

    Une section dont la citation est introuvable, ou qui remonterait avant une
    section déjà placée, est simplement ignorée : mieux vaut un intertitre
    manquant qu'un titre au mauvais endroit.
    """
    if not text or not sections:
        return text

    haystack, positions = _fingerprint(text)
    if not haystack:
        return text

    anchors: list[tuple[int, str]] = []
    cursor = 0
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        quote = str(section.get("quote") or "").strip()
        if not heading or not quote:
            continue

        needle, _ = _fingerprint(quote)
        if len(needle) < 12:  # trop court pour être un repère fiable
            continue

        found = haystack.find(needle, cursor)
        if found == -1:
            continue

        insert_at = _paragraph_start(text, positions[found])
        if anchors and insert_at <= anchors[-1][0]:
            continue
        if insert_at == 0:  # pas d'intertitre collé au tout début
            cursor = found + len(needle)
            continue

        anchors.append((insert_at, heading))
        cursor = found + len(needle)

    if not anchors:
        return text

    pieces: list[str] = []
    previous = 0
    for position, heading in anchors:
        pieces.append(text[previous:position])
        pieces.append(f"## {heading}\n\n")
        previous = position
    pieces.append(text[previous:])
    return "".join(pieces)
