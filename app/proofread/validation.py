"""Validation locale, mécanique, d'un candidat de relecture NIM.

C'est le garde-fou principal de tout ce module : une réponse de modèle,
NVIDIA NIM en particulier, peut contenir n'importe quoi — y compris son
propre raisonnement interne (« We need to apply the rules... ») au lieu du
texte relu. Cette fonction ne fait AUCUN appel réseau ni LLM : elle compare
mécaniquement le brut et le candidat, et rend un verdict reproductible.

La sécurité ne repose PAS sur une liste de phrases interdites : une liste de
ce type est contournable dans n'importe quelle langue. Le contrôle principal
est structurel — nombre de mots, mots ajoutés, longueur du plus grand passage
inventé — et les marqueurs de méta-discours ne sont qu'un signal
complémentaire, jamais suffisant à eux seuls (sauf artefact univoque comme
``<think>``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import textloc

# ----------------------------------------------------------------- seuils
#
# Chaque seuil est documenté avec son intention : ce qu'il doit laisser
# passer (une relecture légitime) et ce qu'il doit bloquer (l'incident
# constaté). Ce sont des points de départ raisonnables, pas des constantes
# gravées dans le marbre — à ajuster si l'expérience montre un biais.

# En dessous de ce ratio de longueur, le modèle a résumé au lieu de relire.
# Même seuil que ``claude.MIN_LENGTH_RATIO`` : une relecture légitime retire
# hésitations et répétitions, ce qui fait déjà fondre le texte de 10-20 %,
# mais pas plus de 45 %.
MIN_LENGTH_RATIO = 0.55

# Une relecture ne fait que ponctuer, corriger l'orthographe et retirer le
# bruit de l'oral : le nombre de mots ne devrait pas grossir de plus de 60 %.
# La marge absolue protège les blocs courts, où un ratio seul serait trop
# sévère (un brut de 8 mots devenant 14 mots reste une relecture plausible).
MAX_EXPANSION_RATIO = 1.6
MAX_EXPANSION_ABSOLUTE_MARGIN = 40

# Proportion de mots du candidat qui n'existent pas (au sens de l'alignement
# mot à mot) dans le brut. Une relecture corrige, elle n'ajoute pas près de
# la moitié du texte.
MAX_ADDED_WORD_RATIO = 0.40

# Longueur, en mots, du plus grand passage continu ajouté/remplacé. C'est le
# contrôle qui détecte un bloc de raisonnement inséré d'un bloc, quelle que
# soit la langue : « 150 mots ajoutés dont une séquence continue de 80 mots
# absente du brut » est rejeté par CE seuil, indépendamment des marqueurs de
# méta-discours.
MAX_ADDED_SPAN_WORDS = 25

# Marqueurs de méta-discours caractéristiques d'un raisonnement de modèle,
# français et anglais. Signal complémentaire seulement — voir plus bas les
# conditions de corroboration.
_METADISCOURSE_MARKERS = (
    r"\bwe need to\b",
    r"\bwe must\b",
    r"\blet'?s examine\b",
    r"\bthe passage is\b",
    r"\bthis passage is\b",
    r"\bi need to\b",
    r"\bi will\b",
    r"\bparagraph\s*\d+\s*:",
    r"\bhere is the corrected\b",
    r"\bhere'?s the corrected\b",
    r"\bil faut appliquer les règles\b",
    r"\ble passage est\b",
    r"\bexaminons\b",
    r"\banalysons\b",
    r"\bvoici le texte corrigé\b",
    r"\bje dois\b",
    r"\bparagraphe\s*\d+\s*:",
)
_METADISCOURSE_RE = re.compile("|".join(_METADISCOURSE_MARKERS), re.IGNORECASE)

# Artefacts de modèle : une seule occurrence suffit, aucune corroboration
# n'est nécessaire (contrairement au méta-discours ci-dessus).
_MODEL_ARTIFACT_MARKERS = (
    r"<\s*think\s*>",
    r"<\s*/\s*think\s*>",
    r"<\s*system\s*>",
    r"<\s*/\s*system\s*>",
    r"^\s*system\s*:",
    r"^\s*assistant\s*:",
    r"^\s*user\s*:",
    r'"role"\s*:\s*"(system|user|assistant)"',
)
_MODEL_ARTIFACT_RE = re.compile("|".join(_MODEL_ARTIFACT_MARKERS), re.IGNORECASE | re.MULTILINE)

_NUMBER_RE = re.compile(r"\d+(?:[   ]\d{3})*(?:[.,]\d+)?")
_ACRONYM_RE = re.compile(r"(?<![\w-])[A-ZÀ-ÖØ-Þ]{2,}(?![\w-])")

# Au-delà de ce taux de nombres/sigles du brut disparus ou changés, on ajoute
# un motif de rejet — jamais seul : toujours combiné à un signal structurel
# ou de méta-discours pour éviter de bloquer une correction ASR légitime
# (« 20 » corrigé en toutes lettres, un sigle mieux capitalisé...).
MAX_ALTERED_NUMBER_RATIO = 0.5
MAX_ALTERED_ACRONYM_RATIO = 0.5


@dataclass
class ValidationResult:
    """Verdict mécanique sur un candidat de relecture."""

    valid: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"valid": self.valid, "reasons": list(self.reasons), "metrics": dict(self.metrics)}


def _normalise_number(raw: str) -> str:
    return re.sub(r"[   ]", "", raw).replace(",", ".").rstrip(".")


def _numbers(text: str) -> list[str]:
    return [_normalise_number(m.group()) for m in _NUMBER_RE.finditer(text)]


def _acronyms(text: str) -> list[str]:
    return _ACRONYM_RE.findall(text)


def _missing_ratio(raw_items: list[str], candidate_items: list[str]) -> float:
    if not raw_items:
        return 0.0
    remaining = list(candidate_items)
    missing = 0
    for item in raw_items:
        if item in remaining:
            remaining.remove(item)
        else:
            missing += 1
    return missing / len(raw_items)


def detect_metadiscourse_markers(text: str) -> list[str]:
    """Marqueurs de méta-discours trouvés (dédupliqués), signal seul, non gating."""
    return sorted({match.group().strip().lower() for match in _METADISCOURSE_RE.finditer(text or "")})


def detect_model_artifacts(text: str) -> list[str]:
    """Artefacts de modèle univoques (``<think>``, préfixes de rôle...)."""
    return sorted({match.group().strip().lower() for match in _MODEL_ARTIFACT_RE.finditer(text or "")})


def validate_proofread_candidate(
    raw: str,
    candidate: str,
    *,
    model: str = "",
) -> ValidationResult:
    """Verdict mécanique, reproductible, sans appel réseau ni LLM.

    ``raw`` est le passage brut envoyé au modèle ; ``candidate`` est déjà le
    texte extrait de l'enveloppe de sortie (voir ``envelope.py``) — cette
    fonction ne s'occupe pas du protocole de délimitation, seulement du
    contenu.
    """
    reasons: list[str] = []
    raw = raw or ""
    candidate = candidate or ""

    raw_words = [w for w, _s, _e in textloc.tokenize_words(raw)]
    candidate_words = [w for w, _s, _e in textloc.tokenize_words(candidate)]
    words_raw = len(raw_words)
    words_candidate = len(candidate_words)

    opcodes = textloc.word_opcodes(raw, candidate) if raw_words or candidate_words else []
    added_words = sum(j2 - j1 for tag, _i1, _i2, j1, j2 in opcodes if tag in ("insert", "replace"))
    longest_added_span = max(
        (j2 - j1 for tag, _i1, _i2, j1, j2 in opcodes if tag in ("insert", "replace")), default=0
    )
    added_ratio = added_words / words_raw if words_raw else (1.0 if words_candidate else 0.0)

    metrics = {
        "words_raw": words_raw,
        "words_candidate": words_candidate,
        "added_words": added_words,
        "longest_added_span": longest_added_span,
        "added_word_ratio": round(added_ratio, 4),
        "ratio": round(words_candidate / words_raw, 4) if words_raw else (0.0 if not words_candidate else float("inf")),
    }

    # A. Réponse vide ou trop courte.
    if not candidate.strip():
        reasons.append("empty")
    elif raw.strip() and len(candidate) < len(raw) * MIN_LENGTH_RATIO:
        reasons.append("too_short")

    # B. Réponse anormalement longue (le cœur du bug observé : le
    # raisonnement du modèle est généralement bien plus long que le passage
    # source, jamais plus court).
    expansion_ceiling = max(words_raw * MAX_EXPANSION_RATIO, words_raw + MAX_EXPANSION_ABSOLUTE_MARGIN)
    if words_raw and words_candidate > expansion_ceiling:
        reasons.append("too_long")

    # C. Ratio de mots ajoutés.
    if words_raw and added_ratio > MAX_ADDED_WORD_RATIO:
        reasons.append("added_word_ratio")

    # D. Longue séquence continue de mots absents du brut.
    if longest_added_span > MAX_ADDED_SPAN_WORDS:
        reasons.append("long_added_span")

    # E. Changement important des nombres prononcés.
    raw_numbers, candidate_numbers = _numbers(raw), _numbers(candidate)
    metrics["altered_number_ratio"] = round(_missing_ratio(raw_numbers, candidate_numbers), 4)
    numbers_altered = bool(raw_numbers) and metrics["altered_number_ratio"] > MAX_ALTERED_NUMBER_RATIO

    # F. Changement important des sigles.
    raw_acronyms, candidate_acronyms = _acronyms(raw), _acronyms(candidate)
    metrics["altered_acronym_ratio"] = round(_missing_ratio(raw_acronyms, candidate_acronyms), 4)
    acronyms_altered = bool(raw_acronyms) and metrics["altered_acronym_ratio"] > MAX_ALTERED_ACRONYM_RATIO

    # G. Artefacts de modèle : signal fort, jamais besoin de corroboration.
    artifacts = detect_model_artifacts(candidate)
    metrics["model_artifacts"] = artifacts
    if artifacts:
        reasons.append("model_artifact")

    # H. Méta-discours : signal complémentaire, jamais suffisant seul (sauf
    # déjà couvert par G). Corroboré par une expansion, un ajout massif, ou
    # au moins deux marqueurs distincts — reproduit la structure du bug
    # observé (reasoning long ET détecté par plusieurs motifs à la fois).
    markers = detect_metadiscourse_markers(candidate)
    metrics["markers_found"] = markers
    reasoning_leak = bool(artifacts) or (
        bool(markers)
        and (
            len(markers) >= 2
            or added_ratio > 0.30
            or longest_added_span > 15
            or "too_long" in reasons
        )
    )
    if reasoning_leak and "reasoning_leak" not in reasons:
        reasons.append("reasoning_leak")

    # E/F ne bloquent que combinés à un autre signal structurel ou de fuite —
    # une correction ASR légitime d'un chiffre ou d'un sigle isolé ne doit
    # pas, à elle seule, faire échouer la relecture.
    if numbers_altered and (reasoning_leak or "too_long" in reasons or "long_added_span" in reasons):
        reasons.append("numbers_altered")
    if acronyms_altered and (reasoning_leak or "too_long" in reasons or "long_added_span" in reasons):
        reasons.append("acronyms_altered")

    if model:
        metrics["model"] = model

    return ValidationResult(valid=not reasons, reasons=reasons, metrics=metrics)
