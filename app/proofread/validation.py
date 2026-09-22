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
from .contracts import FAITHFUL_PROOFREAD_CONTRACT, ProofreadAcceptanceContract
from .entity_grounding import check_grounded_lexicon_entities
from .language_guardrail import check_language_consistency
from .prefix_alignment import check_prefix_alignment

# ----------------------------------------------------------------- seuils
#
# Chaque seuil est documenté avec son intention : ce qu'il doit laisser
# passer (une relecture légitime) et ce qu'il doit bloquer (l'incident
# constaté). Ce sont des points de départ raisonnables, pas des constantes
# gravées dans le marbre — à ajuster si l'expérience montre un biais.

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
    contract: ProofreadAcceptanceContract = FAITHFUL_PROOFREAD_CONTRACT,
    model: str = "",
    prefix_options: dict | None = None,
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
    elif raw.strip() and len(candidate) < len(raw) * contract.min_length_ratio:
        reasons.append("too_short")

    # B. Réponse anormalement longue (le cœur du bug observé : le
    # raisonnement du modèle est généralement bien plus long que le passage
    # source, jamais plus court).
    expansion_ceiling = max(
        words_raw * contract.max_expansion_ratio,
        words_raw + contract.max_expansion_absolute_margin,
    )
    if words_raw and words_candidate > expansion_ceiling:
        reasons.append("too_long")

    # C. Ratio de mots ajoutés.
    if words_raw and added_ratio > contract.max_added_word_ratio:
        reasons.append("added_word_ratio")

    # D. Longue séquence continue de mots absents du brut.
    if longest_added_span > contract.max_added_span_words:
        reasons.append("long_added_span")

    # E. Changement important des nombres prononcés.
    raw_numbers, candidate_numbers = _numbers(raw), _numbers(candidate)
    metrics["altered_number_ratio"] = round(_missing_ratio(raw_numbers, candidate_numbers), 4)
    numbers_altered = (
        contract.preserve_numbers
        and bool(raw_numbers)
        and metrics["altered_number_ratio"] > MAX_ALTERED_NUMBER_RATIO
    )

    # F. Changement important des sigles.
    raw_acronyms, candidate_acronyms = _acronyms(raw), _acronyms(candidate)
    metrics["altered_acronym_ratio"] = round(_missing_ratio(raw_acronyms, candidate_acronyms), 4)
    acronyms_altered = (
        contract.preserve_acronyms
        and bool(raw_acronyms)
        and metrics["altered_acronym_ratio"] > MAX_ALTERED_ACRONYM_RATIO
    )

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

    if contract.check_prefix_alignment:
        prefix = check_prefix_alignment(raw, candidate, **(prefix_options or {}))
        metrics["prefix_alignment"] = prefix
        if prefix["applicable"] and not prefix["accepted"] and "prefix_misaligned" not in reasons:
            reasons.append("prefix_misaligned")

    if contract.check_language:
        language = check_language_consistency(raw, candidate)
        metrics["language"] = language
        if language["applicable"] and not language["accepted"] and "language_mismatch" not in reasons:
            reasons.append("language_mismatch")

    if contract.check_entity_grounding:
        entity_grounding = check_grounded_lexicon_entities(raw, candidate)
        metrics["entity_grounding"] = entity_grounding
        if not entity_grounding["accepted"] and "ungrounded_entity" not in reasons:
            reasons.append("ungrounded_entity")

    if model:
        metrics["model"] = model

    return ValidationResult(valid=not reasons, reasons=reasons, metrics=metrics)
