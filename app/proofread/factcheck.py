"""Fact-check : recherche web ciblée sur les noms propres, titres de rapport,
statistiques et références juridiques cités dans le texte relu.

Deux passes, chacune un appel par bloc ou par affirmation :

    A. EXTRACTION    texte relu → affirmations vérifiables (citation exacte)
    B. VÉRIFICATION  affirmation → verdict, recherche web obligatoire

La vérification de fidélité (``verify.py``) compare le texte relu au texte
brut : elle voit un chiffre disparu, pas un chiffre faux. Cette étape prend
l'autre moitié du problème — un nom propre, un titre de rapport, une
statistique plausibles mais faux — par une recherche web ciblée, jamais par
une simple relecture de plausibilité.

Principe directeur, hérité de ``structure.insert_headings`` : le modèle
propose, le programme dispose. Une correction n'est appliquée que par
substitution exacte faite ici, en Python, et seulement si une recherche web
l'a étayée (voir ``apply_verdicts``). Le garde-fou est mécanique, pas
seulement prompté : un verdict sans trace de recherche web effective est
requalifié de force en « introuvable », quoi qu'ait écrit le modèle — voir
``_coerce_verdict``. L'incertitude résiduelle reste visible dans le texte —
un appel de note — plutôt que lissée en une version fluide et faussement
définitive.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .. import config as config_module
from ..lexicon import lookup as lexicon_lookup
from . import prompts
from .backends import get_backend
from .backends.cli import QuotaExhausted
from .base import ProofreadError
from .structure import _fingerprint, parse_json_array, parse_json_object
from .verify import Finding

logger = logging.getLogger(__name__)

MAX_TOKENS_CLAIMS = 4_000
MAX_TOKENS_VERDICT = 2_000

VERDICTS = ("confirme", "corrige", "infirme", "introuvable", "ambigu")
CONFIDENCES = ("haute", "moyenne", "basse")
CLAIM_TYPES = (
    "nom_propre",
    "organisme",
    "rapport",
    "statistique",
    "reference_juridique",
    "date",
)

CLAIMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(CLAIM_TYPES)},
            "citation": {"type": "string"},
            "question": {"type": "string"},
        },
        "required": ["citation"],
    },
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "forme_correcte": {"type": "string"},
        "explication": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"titre": {"type": "string"}, "url": {"type": "string"}},
            },
        },
        "confiance": {"type": "string", "enum": list(CONFIDENCES)},
    },
    "required": ["verdict", "confiance"],
}


@dataclass
class Claim:
    """Une affirmation à vérifier, repérée dans le texte relu."""

    type: str
    citation: str
    question: str = ""
    # Fraction (0..1) de la position dans le texte relu — convertie en
    # secondes par apply_verdicts, qui seul connaît la durée du média.
    start: float = 0.0


@dataclass
class Source:
    titre: str = ""
    url: str = ""


@dataclass
class Verdict:
    """Résultat de la vérification d'une affirmation."""

    claim: Claim
    verdict: str = "introuvable"
    forme_correcte: str = ""
    explication: str = ""
    sources: list[Source] = field(default_factory=list)
    confiance: str = "basse"
    # "web" (recherche effective), "lexique" (résolu sans recherche par le
    # lexique MJPM vérifié), "coerce" (garde-fou : aucune preuve trouvée),
    # "quota" (limite d'usage de l'abonnement atteinte).
    origine: str = "web"


@dataclass
class FactCheckReport:
    claims_checked: int = 0
    corrections: int = 0
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "claims_checked": self.claims_checked,
            "corrections": self.corrections,
            "findings": [f.to_dict() for f in self.findings],
        }


def _scaled(on_progress, base: float, share: float, label: str):
    """Sous-progression, ramenée à l'échelle globale (même principe que
    pipeline._Progress.scaled)."""
    if on_progress is None:
        return None

    def report(fraction: float, stage: str = label) -> None:
        on_progress(base + fraction * share, stage)

    return report


# ------------------------------------------------------------- découpage


def _text_blocks(text: str, max_chars: int) -> list[tuple[str, int]]:
    """Découpe ``text`` en blocs sur des frontières de paragraphes.

    Renvoie, pour chaque bloc, son texte et la position (en caractères) où
    il commence dans ``text`` — sert à situer approximativement chaque
    affirmation dans le temps (voir extract_claims).
    """
    max_chars = max(500, int(max_chars))
    blocks: list[tuple[str, int]] = []
    buffer: list[str] = []
    length = 0
    block_start = 0
    cursor = 0

    paragraphs = text.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        sep_len = 2 if index < len(paragraphs) - 1 else 0
        if not paragraph.strip():
            cursor += len(paragraph) + sep_len
            continue
        if not buffer:
            block_start = cursor
        buffer.append(paragraph)
        length += len(paragraph) + sep_len
        cursor += len(paragraph) + sep_len
        if length >= max_chars:
            blocks.append(("\n\n".join(buffer), block_start))
            buffer, length = [], 0

    if buffer:
        blocks.append(("\n\n".join(buffer), block_start))
    return blocks


# ------------------------------------------------------- passe A : extraction


def extract_claims(
    text: str,
    *,
    settings=None,
    on_progress=None,
    should_cancel=None,
) -> list[Claim]:
    """Repère les affirmations vérifiables du texte relu, bloc par bloc."""
    settings = settings or config_module.load_settings()
    if not text.strip():
        return []

    backend = get_backend(settings)
    blocks = _text_blocks(text, settings.proofread_chunk_chars)
    total_len = max(len(text), 1)
    claims: list[Claim] = []

    for index, (block_text, offset) in enumerate(blocks):
        if should_cancel is not None and should_cancel():
            raise ProofreadError("Repérage des affirmations annulé.")
        if on_progress:
            on_progress(
                index / max(len(blocks), 1),
                f"Repérage des affirmations, bloc {index + 1}/{len(blocks)}…",
            )

        result = backend.complete(
            system=prompts.FACTCHECK_EXTRACTION_SYSTEM,
            user=prompts.FACTCHECK_EXTRACTION_USER.format(body=block_text),
            max_tokens=MAX_TOKENS_CLAIMS,
            schema=CLAIMS_SCHEMA,
        )
        raw_claims = result.parsed if result.parsed is not None else parse_json_array(result.text)
        for raw in raw_claims or []:
            if not isinstance(raw, dict):
                continue
            citation = str(raw.get("citation") or "").strip()
            if not citation:
                continue
            claim_type = str(raw.get("type") or "").strip()
            local_pos = block_text.find(citation)
            approx_pos = offset + local_pos if local_pos != -1 else offset
            claims.append(
                Claim(
                    type=claim_type if claim_type in CLAIM_TYPES else "autre",
                    citation=citation,
                    question=str(raw.get("question") or "").strip(),
                    start=approx_pos / total_len,
                )
            )

    return claims


# ---------------------------------------------------- passe B : vérification


def verify_claim(claim: Claim, *, settings=None) -> Verdict:
    """Vérifie une affirmation : lexique d'abord, recherche web sinon."""
    settings = settings or config_module.load_settings()

    if settings.lexicon_enabled:
        term = lexicon_lookup(claim.citation, verified_only=True)
        if term is not None:
            return Verdict(
                claim=claim,
                verdict="confirme",
                forme_correcte=term.terme,
                explication=f"Terme du lexique MJPM, catégorie « {term.categorie} ».",
                sources=[
                    Source(titre=str(s.get("titre") or ""), url=str(s.get("url") or ""))
                    for s in term.sources
                    if isinstance(s, dict)
                ],
                confiance="haute",
                origine="lexique",
            )

    backend = get_backend(settings)
    try:
        result = backend.complete(
            system=prompts.FACTCHECK_VERDICT_SYSTEM,
            user=prompts.FACTCHECK_VERDICT_USER.format(
                citation=claim.citation,
                question=claim.question or "Vérifier l'exactitude de cette information.",
            ),
            max_tokens=MAX_TOKENS_VERDICT,
            schema=VERDICT_SCHEMA,
            web_search=True,
        )
    except QuotaExhausted:
        return Verdict(
            claim=claim,
            verdict="introuvable",
            confiance="basse",
            explication="Limite d'usage de l'abonnement Claude atteinte au moment de la vérification.",
            origine="quota",
        )

    data = result.parsed if isinstance(result.parsed, dict) else parse_json_object(result.text)
    return _coerce_verdict(claim, data or {}, result)


def _coerce_verdict(claim: Claim, data: dict, result) -> Verdict:
    """Garde-fou mécanique : sans trace de recherche, pas de verdict positif.

    Un verdict rendu de mémoire — sans qu'aucune recherche n'ait eu lieu —
    est structurellement impossible : ceci est vérifié ici, pas seulement
    demandé dans le prompt.
    """
    verdict = str(data.get("verdict") or "").strip()
    if verdict not in VERDICTS:
        verdict = "ambigu"
    confiance = str(data.get("confiance") or "").strip()
    if confiance not in CONFIDENCES:
        confiance = "basse"

    sources = [
        Source(titre=str(s.get("titre") or ""), url=str(s.get("url") or ""))
        for s in (data.get("sources") or [])
        if isinstance(s, dict) and s.get("url")
    ]
    if not sources and result.sources:
        sources = [Source(titre="", url=url) for url in result.sources]

    has_evidence = result.web_searches > 0 or bool(result.sources) or bool(sources)
    if not has_evidence:
        return Verdict(
            claim=claim,
            verdict="introuvable",
            confiance="basse",
            explication="Aucune recherche web constatée pour cette affirmation : verdict non retenu.",
            origine="coerce",
        )

    return Verdict(
        claim=claim,
        verdict=verdict,
        forme_correcte=str(data.get("forme_correcte") or "").strip(),
        explication=str(data.get("explication") or "").strip(),
        sources=sources,
        confiance=confiance,
        origine="web",
    )


# --------------------------------------------------------- application

_VERDICT_LABELS = {
    "infirme": "infirmé par la recherche web",
    "introuvable": "introuvable",
    "ambigu": "résultat ambigu",
    "corrige": "correction proposée mais non retenue (confiance insuffisante)",
}


def _verdict_label(verdict: Verdict) -> str:
    if verdict.origine == "quota":
        return "non vérifié (limite d'usage atteinte)"
    return _VERDICT_LABELS.get(verdict.verdict, verdict.verdict)


def _sources_line(sources: list[Source]) -> str:
    links = [f"[{s.titre or s.url}]({s.url})" for s in sources if s.url]
    return f"\n    Sources : {' · '.join(links)}" if links else ""


def _locate(text: str, citation: str) -> tuple[int, int] | None:
    """Position de ``citation`` dans ``text``, tolérante aux espaces et à la
    ponctuation — même technique que ``structure.insert_headings``."""
    citation = citation.strip()
    if not citation:
        return None
    haystack, positions = _fingerprint(text)
    needle, _ = _fingerprint(citation)
    if len(needle) < 4:  # trop court pour être un repère fiable
        return None
    found = haystack.find(needle)
    if found == -1:
        return None
    start = positions[found]
    end = positions[found + len(needle) - 1] + 1
    return start, end


def _entity_from_verdict(verdict: Verdict) -> dict:
    nom = (verdict.forme_correcte or verdict.claim.citation).strip()
    return {
        "nom": nom,
        "wikilink": nom,
        "categorie": verdict.claim.type,
        "definition": verdict.explication,
        "sources": [{"titre": s.titre, "url": s.url} for s in verdict.sources if s.url],
    }


def apply_verdicts(
    clean_text: str, verdicts: list[Verdict], *, duration: float = 0.0
) -> tuple[str, list[Finding], list[dict]]:
    """Applique les verdicts au texte relu.

    Une correction n'est appliquée QUE si verdict == "corrige", confiance
    == "haute" et au moins une source : une substitution exacte, faite ici,
    jamais par le modèle. Tout le reste laisse le texte transcrit intact et
    ajoute un appel de note — l'incertitude reste visible dans le texte,
    plutôt que lissée en une version fluide et faussement définitive.

    Renvoie (texte annoté, points à vérifier pour le rapport de
    vérification, entités confirmées pour le second brain).
    """
    text = clean_text
    findings: list[Finding] = []
    footnotes: list[str] = []
    entities: list[dict] = []
    note_index = 0

    for verdict in verdicts:
        citation = verdict.claim.citation.strip()
        if not citation:
            continue

        if verdict.verdict == "confirme":
            entities.append(_entity_from_verdict(verdict))
            continue  # fidèle : le texte reste tel quel, aucun marqueur

        span = _locate(text, citation)
        if span is None:
            # Citation introuvable dans le texte relu : ignorée en silence,
            # comme structure.insert_headings le fait pour un intertitre.
            continue
        start_pos, end_pos = span

        applied_correction = (
            verdict.verdict == "corrige"
            and verdict.confiance == "haute"
            and bool(verdict.sources)
            and bool(verdict.forme_correcte.strip())
        )

        note_index += 1
        marker = f"[^v{note_index}]"

        if applied_correction:
            replacement = f"{verdict.forme_correcte}{marker}"
            text = text[:start_pos] + replacement + text[end_pos:]
            label = f"corrigé en **{verdict.forme_correcte}**"
        else:
            text = text[:end_pos] + marker + text[end_pos:]
            label = _verdict_label(verdict)

        footnote = f"{marker}: **« {citation} »** — {label}."
        if verdict.explication:
            footnote += f" {verdict.explication}"
        footnote += _sources_line(verdict.sources)
        footnotes.append(footnote)

        if not applied_correction:
            severity = "haute" if verdict.verdict == "infirme" or verdict.confiance == "basse" else "moyenne"
            message = f"« {citation} » — {label}."
            if verdict.explication:
                message += f" {verdict.explication}"
            findings.append(
                Finding(
                    kind="fait",
                    severity=severity,
                    message=message,
                    start=verdict.claim.start * duration,
                    raw_excerpt=citation,
                    clean_excerpt=verdict.forme_correcte or citation,
                    source="web",
                )
            )

    if footnotes:
        text = text.rstrip() + "\n\n" + "\n".join(footnotes) + "\n"

    return text, findings, entities


# --------------------------------------------------------- orchestration


def factcheck(
    clean_text: str,
    *,
    duration: float = 0.0,
    settings=None,
    on_progress=None,
    should_cancel=None,
) -> tuple[str, FactCheckReport, list[dict]]:
    """Fait tout : repère les affirmations, les vérifie, applique les verdicts."""
    settings = settings or config_module.load_settings()

    claims = extract_claims(
        clean_text,
        settings=settings,
        on_progress=_scaled(on_progress, 0.0, 0.3, "Repérage des affirmations…"),
        should_cancel=should_cancel,
    )
    if not claims:
        if on_progress:
            on_progress(1.0, "Aucune affirmation à vérifier.")
        return clean_text, FactCheckReport(claims_checked=0), []

    verdicts: list[Verdict] = []
    for index, claim in enumerate(claims):
        if should_cancel is not None and should_cancel():
            raise ProofreadError("Fact-check annulé.")
        if on_progress:
            on_progress(
                0.3 + 0.7 * index / len(claims),
                f"Vérification {index + 1}/{len(claims)}…",
            )
        verdicts.append(verify_claim(claim, settings=settings))

    text, findings, entities = apply_verdicts(clean_text, verdicts, duration=duration)
    corrections = sum(
        1
        for v in verdicts
        if v.verdict == "corrige" and v.confiance == "haute" and v.sources
    )
    report = FactCheckReport(
        claims_checked=len(claims), corrections=corrections, findings=findings
    )

    if on_progress:
        on_progress(1.0, "Fact-check terminé.")
    return text, report, entities
