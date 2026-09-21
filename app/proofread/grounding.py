"""Ancrage terminologique léger, avant la relecture — pas un fact-check.

Entre RAW et REVIEW, on peut aider le modèle de relecture avec les graphies
déjà connues du domaine pour CE passage précis, sans lui demander de
réécrire quoi que ce soit et sans recherche web systématique. La différence
avec ``lexicon.glossary_block`` (déjà utilisé pour tout le lexique) est
l'étendue : ici, seulement les termes dont une graphie proche apparaît
réellement dans le passage brut envoyé — un indice court et ciblé, pas un
rappel de tout le glossaire.

Une recherche web n'est utile que pour une entité réellement ambiguë ; ce
n'est pas le rôle de ce module, qui reste purement local (lexique MJPM déjà
en mémoire).
"""
from __future__ import annotations

from ..lexicon import near_misses

MAX_HINTS = 8


def grounding_hints(raw_chunk_text: str, settings=None) -> str:
    """Courte liste de graphies plausibles pour ``raw_chunk_text``, ou "".

    Ne réécrit jamais le passage : fournit seulement des graphies autorisées
    que le modèle peut choisir d'utiliser si l'audio le suggère — jamais à
    imposer, exactement comme ``glossary_block``.
    """
    if settings is not None and not getattr(settings, "lexicon_enabled", True):
        return ""
    if not raw_chunk_text or not raw_chunk_text.strip():
        return ""

    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for word, term in near_misses(raw_chunk_text):
        key = (word.lower(), term.terme)
        if key in seen:
            continue
        seen.add(key)
        graphies = ", ".join(dict.fromkeys([term.terme, *term.sigles]))
        lines.append(f"- « {word} » entendu dans ce passage : graphie probable « {graphies} »")
        if len(lines) >= MAX_HINTS:
            break

    if not lines:
        return ""
    return (
        "Repères terminologiques pour CE passage précis (à utiliser seulement "
        "si l'audio le confirme ; ne remplace jamais un mot sur cette seule "
        "base) :\n" + "\n".join(lines)
    )
