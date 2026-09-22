"""Repérage mécanique du juridique et des organismes de mesure de protection.

Aucun appel réseau ni au modèle ici : seulement des expressions régulières,
pour que les étapes coûteuses (recherche web, verdict Claude) se concentrent
sur ce qui l'exige vraiment. Le domaine visé est celui du lexique MJPM (voir
``app/lexicon/mjpm.json``, champ ``reference``) : droit civil et social
français relatif à la protection juridique des majeurs.

Choix de normalisation : plutôt que de doubler chaque motif en version
accentuée et non accentuée pour tolérer les accents que la transcription
automatique perd souvent (« préfecture » devenu « prefecture »), on
normalise le texte d'entrée — suppression des diacritiques, minuscule — et
on écrit les motifs sans accents. Plus simple à maintenir, plus robuste
face à l'ASR, et sans effet sur l'exactitude : la reconnaissance porte sur
la forme, pas sur l'orthographe des accents.
"""
from __future__ import annotations

import re
import unicodedata

# --- Références légales : articles, codes, textes ---------------------------

# Un article, isolé ou en borne de plage : « article 440 », « art. 433 a
# 439 », « articles l. 471-1 a l. 471-9 », « art. r. 471-5 », « article d.
# 472-5 », « art. a. 271-1 ». La lettre (L/R/D/A) est optionnelle, avec ou
# sans point ni espace ; les numéros composés (« 213-4-1 ») sont acceptés.
_ARTICLE = (
    r"\bart(?:icles?)?\.?\s+"
    r"(?:[lrda]\.?\s*)?\d+(?:[-\s]\d+)*"
    # borne haute d'une plage, introduite par « a » (« à » sans accent) —
    # uniquement à la suite d'un numéro d'article, jamais ailleurs, pour ne
    # pas confondre avec la lettre « a » ordinaire.
    r"(?:\s*a\s*(?:[lrda]\.?\s*)?\d+(?:[-\s]\d+)*)?"
)

# Les codes cités dans le lexique MJPM, en toutes lettres ou en sigle.
_CODES = (
    r"\bcode (?:civil|penal"
    r"|de l['’]action sociale et des familles"
    r"|de la sante publique"
    r"|de procedure civile"
    r"|de l['’]organisation judiciaire"
    r"|de la securite sociale"
    r"|du travail"
    r"|monetaire et financier"
    r"|general des impots)\b"
    r"|\bcasf\b|\bcsp\b"
)

# « n° » et ses variantes fréquentes : n°, n °, no, nº.
_NUM_MARK = r"n\s?[o°º]\.?\s*"

_TEXTES = (
    rf"\bloi\s+{_NUM_MARK}\d"
    r"|\bloi\s+du\s+\d"
    rf"|\bdecret\s+{_NUM_MARK}\d"
    r"|\barrete\s+du\s+\d"
    rf"|\bordonnance\s+{_NUM_MARK}\d"
    r"|\bcirculaire\b"
    r"|\breglement\s*\(ue\)\s*\d"
    r"|\bdirective\s+\d"
    r"|\brgpd\b"
)

LEGAL_REF_RE = re.compile(
    "|".join(f"(?:{p})" for p in (_ARTICLE, _CODES, _TEXTES)),
    re.IGNORECASE,
)

# --- Organismes liés à la mesure de protection -------------------------------

# Sigles bornés (\b) pour ne jamais matcher au milieu d'un autre mot.
_ORGANISMES = (
    r"\bjuge des tutelles\b"
    r"|\bjuge des contentieux de la protection\b"
    r"|\bjcp\b"
    r"|\btribunal judiciaire\b"
    r"|\btribunal d['’]instance\b"
    r"|\bgreffe\b"
    r"|\bprocureur\b"
    r"|\bparquet\b"
    r"|\bprefecture\b"
    r"|\bprefet\b"
    r"|\bconseil departemental\b"
    r"|\bddets\b"
    r"|\bddcs\b"
    r"|\bars\b"
    r"|\bmdph\b"
    r"|\bcaf\b"
    r"|\bcpam\b"
    r"|\bcarsat\b"
    r"|\bmsa\b"
    r"|\budaf\b"
    r"|\bati\b"
    r"|\bcnc\b"
    r"|\banesm\b"
    r"|\bhas\b"
    r"|\bdefenseur des droits\b"
    r"|\bcnape\b"
    r"|\bfnat\b"
    r"|\bunaf\b"
)

ORGANISME_MESURE_RE = re.compile(_ORGANISMES, re.IGNORECASE)


def _normalize(text: str) -> str:
    """Minuscule et sans diacritiques — voir le choix expliqué en tête de module."""
    decomposed = unicodedata.normalize("NFKD", text)
    sans_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return sans_accents.lower()


def has_legal_reference(text: str) -> bool:
    """Le texte contient-il une référence à un article, un code ou un texte juridique ?"""
    return bool(LEGAL_REF_RE.search(_normalize(text)))


def is_organisme_de_mesure(text: str) -> bool:
    """Le texte cite-t-il un organisme lié à la mesure de protection (juge, greffe, MDPH...) ?"""
    return bool(ORGANISME_MESURE_RE.search(_normalize(text)))
