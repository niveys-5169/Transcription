"""Contrat de sortie strict pour une relecture NVIDIA NIM.

Le bug corrigé ici est précis : un modèle NIM répond parfois par son
raisonnement interne (« We need to apply the rules... ») au lieu du texte
relu, et ce texte était jusqu'ici accepté tel quel — tout ``message.content``
valait transcription. Ce module remplace cette confiance aveugle par un
contrat explicite : la réponse n'est exploitable que si elle respecte
l'enveloppe attendue ; sinon elle est ``None`` (invalide), jamais devinée.

Deux enveloppes possibles :

- structurée : ``{"text": "..."}``, quand le modèle et le point de
  terminaison prennent en charge une sortie JSON fiable ;
- délimitée : ``<transcription>...</transcription>``, le protocole par
  défaut, sans dépendance à une fonctionnalité de structured output.

Toute donnée en dehors de l'enveloppe est ignorée sans être analysée : on
n'essaie jamais de deviner quelle portion d'une réponse mêlant raisonnement
et résultat correspond au texte relu.
"""
from __future__ import annotations

import json
import re

from .nim_profiles import NimModelProfile
from .structure import parse_json_object

_ENVELOPE_RE = re.compile(r"<transcription>(.*?)</transcription>", re.DOTALL | re.IGNORECASE)


def extract_delimited(response: str) -> str | None:
    """Contenu entre ``<transcription>`` et ``</transcription>``, ou ``None``.

    Seule la DERNIÈRE occurrence est retenue : si le modèle a malgré tout
    laissé du raisonnement suivi d'une enveloppe correcte, c'est cette
    dernière enveloppe qui contient la réponse finale. Une enveloppe vide
    compte comme absente.
    """
    matches = list(_ENVELOPE_RE.finditer(response or ""))
    if not matches:
        return None
    text = matches[-1].group(1).strip()
    return text or None


def extract_structured(response: str) -> str | None:
    """Contenu de ``{"text": "..."}``, ou ``None`` si absent/mal formé."""
    if not response:
        return None
    text = response.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = parse_json_object(text)
    if not isinstance(data, dict):
        return None
    value = data.get("text")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def extract_candidate(response: str, *, profile: NimModelProfile) -> str | None:
    """Extrait le candidat de relecture d'une réponse NIM brute.

    Ne se rabat JAMAIS sur « la réponse entière » quand l'enveloppe attendue
    est absente : une réponse sans enveloppe reconnaissable est INVALID, pas
    approximativement valide.
    """
    if profile.supports_structured_output:
        structured = extract_structured(response)
        if structured is not None:
            return structured
    return extract_delimited(response)
