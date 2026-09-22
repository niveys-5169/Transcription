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

Périmètre de la vérification web : le repérage (passe A) reste large — les
six catégories sont toujours repérées, le repérage a de la valeur même sans
vérification. C'est la vérification (passe B) qui est restreinte : seules
les catégories coûteuses en cas d'erreur (``factcheck_priority_types``, par
défaut le juridique, les dates et les organismes liés à une mesure de
protection) déclenchent une recherche. Voir ``is_in_scope``.

Coûteux même trié : la même citation répétée plusieurs fois dans un texte
n'est vérifiée qu'une fois (déduplication intra-document, voir
``cache_key``), et un verdict déjà établi pour cette citation ailleurs — dans
ce document ou un autre — est réutilisé depuis ``db.factcheck_cache_*``
tant qu'il n'est pas périmé. Jamais au prix du garde-fou : seul un verdict
issu d'une recherche web effective (``origine == "web"``) est mémorisé, voir
``verify_claim``.
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace

from .. import config as config_module
from .. import db
from ..lexicon import Term
from ..lexicon import load_lexicon as lexicon_load_all
from ..lexicon import lookup as lexicon_lookup
from . import legalref
from . import prompts
from .backends import get_backend
from .backends.cli import QuotaExhausted
from .base import ProofreadError
from .structure import parse_json_array, parse_json_object
from .textloc import locate as _locate
from .verify import Finding

logger = logging.getLogger(__name__)

# Sortie de l'extraction : un court tableau JSON (citations + catégories),
# pas un texte à produire — le modèle rapide y suffit (voir extract_claims).
MAX_TOKENS_CLAIMS = 2_000
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

# Catégories où une correction seule coûte cher si elle se trompe : même à
# confiance haute, elles passent par une validation manuelle plutôt que
# d'être appliquées seules (voir apply_verdicts).
SENSITIVE_CLAIM_TYPES = frozenset({"reference_juridique", "statistique", "date"})

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
    block_id: str | None = None
    block_start: float | None = None


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
    # lexique MJPM vérifié), "cache" (verdict "web" mémorisé réutilisé sans
    # nouvel appel, voir cache_key/factcheck), "coerce" (garde-fou : aucune
    # preuve trouvée), "quota" (limite d'usage de l'abonnement atteinte).
    origine: str = "web"


@dataclass
class PendingCorrection:
    """Correction proposée par le fact-check, en attente d'une validation
    humaine explicite — soit trop incertaine (confiance moyenne ou basse),
    soit trop sensible (catégorie de ``SENSITIVE_CLAIM_TYPES``) pour être
    appliquée seule. Voir ``accept_pending``/``reject_pending``.
    """

    id: str
    claim_type: str
    citation: str
    proposition: str
    marker: str
    explication: str = ""
    sources: list[Source] = field(default_factory=list)
    confiance: str = "basse"
    start: float = 0.0
    block_id: str | None = None
    status: str = "attente"  # attente | validee | rejetee

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FactCheckReport:
    claims_checked: int = 0
    corrections: int = 0
    findings: list[Finding] = field(default_factory=list)
    pending: list[PendingCorrection] = field(default_factory=list)
    # Affirmations repérées mais hors périmètre de la vérification web (voir
    # is_in_scope) : ni appel, ni note, ni Finding — seulement listées ici,
    # pour que l'interface puisse les montrer sans laisser croire qu'elles
    # ont été vérifiées.
    skipped: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "claims_checked": self.claims_checked,
            "corrections": self.corrections,
            "findings": [f.to_dict() for f in self.findings],
            "pending": [p.to_dict() for p in self.pending],
            "skipped": self.skipped,
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
    review_blocks: list[dict] | None = None,
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
            # Repérage mécanique (une citation dans le texte, pas un
            # jugement) : le modèle rapide suffit. Jamais pour les verdicts
            # de vérification (verify_claim), qui exigent le jugement du
            # modèle principal — voir la docstring de proofread_model_fast.
            fast=True,
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
            matching = [block for block in (review_blocks or []) if citation and str(block.get("text") or "").find(citation) != -1]
            anchor = matching[0] if len(matching) == 1 else None
            claims.append(
                Claim(
                    type=claim_type if claim_type in CLAIM_TYPES else "autre",
                    citation=citation,
                    question=str(raw.get("question") or "").strip(),
                    start=approx_pos / total_len,
                    block_id=anchor.get("id") if anchor else None,
                    block_start=float(anchor.get("start") or 0) if anchor else None,
                )
            )

    return claims


# ------------------------------------------------------- périmètre et clé


def is_in_scope(claim: Claim, settings) -> bool:
    """Cette affirmation déclenche-t-elle une recherche web ?

    Restreint la vérification (jamais le repérage, voir extract_claims) aux
    catégories coûteuses en cas d'erreur (``factcheck_priority_types``). Cas
    particulier de ``organisme`` : même prioritaire, il n'est retenu que s'il
    est lié à une mesure de protection — sinon un simple organisme cité en
    passant (une caisse, un employeur...) déclencherait une recherche sans
    justification. Deux vérifications mécaniques, sans appel réseau : le
    lexique MJPM (s'il est activé) puis, à défaut, les sigles/expressions
    reconnus par ``legalref``.
    """
    priority_types = {t.strip() for t in settings.factcheck_priority_types.split(",") if t.strip()}
    if claim.type not in priority_types:
        return False

    if claim.type == "organisme":
        if settings.lexicon_enabled:
            term = lexicon_lookup(claim.citation, verified_only=False)
            if term is not None and term.categorie in {"mesure", "acteur", "prestation", "texte"}:
                return True
        return legalref.is_organisme_de_mesure(claim.citation)

    return True


# Ponctuation de bordure (guillemets, tirets, points...) : \W couvre tout
# caractère non alphanumérique, accents compris parmi les caractères gardés
# (un caractère accentué est un caractère de mot pour re, pas un \W).
_BORDER_PUNCT_RE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def _normalize_for_match(text: str) -> str:
    """Minuscule, espaces compactés, ponctuation de bordure retirée.

    Les accents sont conservés à dessein — contrairement à ``legalref``, qui
    les supprime pour tolérer une transcription automatique dégradée : ici,
    on compare des citations à des entrées du lexique, toutes deux déjà en
    français correctement accentué, et un accent compte dans une référence
    légale.
    """
    normalized = " ".join(text.split()).lower()
    return _BORDER_PUNCT_RE.sub("", normalized)


def cache_key(claim_type: str, citation: str) -> str:
    """Clé de déduplication et de cache pour une affirmation.

    Deux occurrences de la même citation (à la casse et aux espaces près)
    partagent la même clé, qu'elles proviennent du même document ou non —
    voir factcheck() pour la déduplication intra-document et verify_claim()
    pour le cache inter-documents (``db.factcheck_cache_*``).
    """
    return f"{claim_type}:{_normalize_for_match(citation)}"


# En deçà, une référence est trop courte pour identifier quoi que ce soit.
_MIN_REFERENCE_MATCH_CHARS = 10


def _lexicon_reference_match(citation: str) -> Term | None:
    """Entrée vérifiée du lexique dont le champ ``reference`` correspond à
    ``citation``, par inclusion tolérante (après la même normalisation que
    ``cache_key``) — ou ``None``.

    Les entrées livrées sont actuellement toutes ``verifie: false`` (voir
    ``app/lexicon/__init__.py``) : ce court-circuit ne se déclenche donc pas
    tant qu'un mainteneur n'a pas lancé ``python -m app.lexicon verify``.
    """
    citation_norm = _normalize_for_match(citation)
    if not citation_norm:
        return None
    for term in lexicon_load_all():
        if not term.verifie or not term.reference:
            continue
        reference_norm = _normalize_for_match(term.reference)
        # Dans ce sens uniquement : la citation doit contenir la référence
        # entière. L'inclusion inverse confirmerait « code civil » sur la foi
        # de l'entrée « Code civil, art. 433 à 439 » — une citation vague
        # validée par une référence bien plus précise qu'elle, soit la faute
        # exacte que cette étape doit empêcher. Longueur minimale pour qu'une
        # référence trop courte ne se retrouve pas partout.
        if len(reference_norm) >= _MIN_REFERENCE_MATCH_CHARS and reference_norm in citation_norm:
            return term
    return None


# ---------------------------------------------------- passe B : vérification


def verify_claim(claim: Claim, *, settings=None) -> Verdict:
    """Vérifie une affirmation : lexique d'abord, cache ensuite, recherche web sinon."""
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

        # Extension à la référence elle-même (pas seulement au terme) pour
        # les affirmations de type "reference_juridique" : « article 440 du
        # code civil » cité tel quel dans le lexique résout sans recherche.
        if claim.type == "reference_juridique":
            term = _lexicon_reference_match(claim.citation)
            if term is not None:
                return Verdict(
                    claim=claim,
                    verdict="confirme",
                    forme_correcte=term.terme,
                    explication=f"Référence légale du lexique MJPM, vérifiée ({term.reference}).",
                    sources=[
                        Source(titre=str(s.get("titre") or ""), url=str(s.get("url") or ""))
                        for s in term.sources
                        if isinstance(s, dict)
                    ],
                    confiance="haute",
                    origine="lexique",
                )

    key = cache_key(claim.type, claim.citation)
    cached = db.factcheck_cache_get(key, max_age_days=settings.factcheck_cache_days)
    if cached is not None:
        # Un verdict relu du cache n'a jamais contourné _coerce_verdict : il
        # n'a été écrit (voir plus bas) qu'après une recherche web effective
        # ("web"). Le garde-fou reste donc entier, même relu depuis le cache.
        return Verdict(
            claim=claim,
            verdict=str(cached.get("verdict") or "introuvable"),
            forme_correcte=str(cached.get("forme_correcte") or ""),
            explication=str(cached.get("explication") or ""),
            sources=[
                Source(titre=str(s.get("titre") or ""), url=str(s.get("url") or ""))
                for s in cached.get("sources") or []
                if isinstance(s, dict)
            ],
            confiance=str(cached.get("confiance") or "basse"),
            origine="cache",
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
            # Jamais fast=True ici : un verdict, contrairement au repérage,
            # engage une correction potentielle du texte — notamment sur les
            # références juridiques, où une erreur coûte plus cher que le
            # temps économisé (voir proofread_model_fast dans config.py).
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
    verdict = _coerce_verdict(claim, data or {}, result)

    # On ne mémorise que les vrais résultats d'une recherche effective
    # ("web") assortis d'au moins une source : "coerce" (garde-fou déclenché,
    # aucune recherche constatée) et "quota" (limite d'abonnement atteinte)
    # sont des non-réponses, pas des verdicts — les mettre en cache figerait
    # une absence de preuve en preuve d'absence pour 90 jours.
    if verdict.origine == "web" and verdict.sources:
        db.factcheck_cache_put(
            key,
            {
                "claim_type": claim.type,
                "citation": claim.citation,
                "verdict": verdict.verdict,
                "forme_correcte": verdict.forme_correcte,
                "explication": verdict.explication,
                "sources": [{"titre": s.titre, "url": s.url} for s in verdict.sources],
                "confiance": verdict.confiance,
            },
        )

    return verdict


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

_PENDING_LABEL = "correction proposée : en attente de validation manuelle"


def _verdict_label(verdict: Verdict) -> str:
    if verdict.origine == "quota":
        return "non vérifié (limite d'usage atteinte)"
    return _VERDICT_LABELS.get(verdict.verdict, verdict.verdict)


def _sources_line(sources: list[Source]) -> str:
    links = [f"[{s.titre or s.url}]({s.url})" for s in sources if s.url]
    return f"\n    Sources : {' · '.join(links)}" if links else ""


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
) -> tuple[str, list[Finding], list[dict], list[PendingCorrection]]:
    """Applique les verdicts au texte relu.

    Une correction n'est appliquée seule QUE si verdict == "corrige",
    confiance == "haute", au moins une source, et que la catégorie n'est pas
    sensible (``SENSITIVE_CLAIM_TYPES``) : une substitution exacte, faite
    ici, jamais par le modèle. Une correction qui a une proposition et des
    sources mais ne remplit pas ces conditions (confiance moindre, ou
    catégorie sensible même à confiance haute) n'est pas non plus perdue :
    elle attend une validation humaine explicite (voir
    ``accept_pending``/``reject_pending``). Tout le reste laisse le texte
    transcrit intact et ajoute un appel de note — l'incertitude reste
    visible dans le texte, plutôt que lissée en une version fluide et
    faussement définitive.

    Renvoie (texte annoté, points à vérifier pour le rapport de
    vérification, entités confirmées pour le second brain, corrections en
    attente de validation manuelle).
    """
    text = clean_text
    findings: list[Finding] = []
    footnotes: list[str] = []
    entities: list[dict] = []
    pending: list[PendingCorrection] = []
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

        proposable = (
            verdict.verdict == "corrige"
            and bool(verdict.sources)
            and bool(verdict.forme_correcte.strip())
        )
        sensible = verdict.claim.type in SENSITIVE_CLAIM_TYPES
        applied_correction = proposable and verdict.confiance == "haute" and not sensible
        awaiting_validation = proposable and not applied_correction

        note_index += 1
        marker = f"[^v{note_index}]"

        if applied_correction:
            replacement = f"{verdict.forme_correcte}{marker}"
            text = text[:start_pos] + replacement + text[end_pos:]
            label = f"corrigé en **{verdict.forme_correcte}**"
        else:
            text = text[:end_pos] + marker + text[end_pos:]
            label = _PENDING_LABEL if awaiting_validation else _verdict_label(verdict)

        footnote = f"{marker}: **« {citation} »** — {label}."
        if verdict.explication:
            footnote += f" {verdict.explication}"
        footnote += _sources_line(verdict.sources)
        footnotes.append(footnote)

        if awaiting_validation:
            pending.append(
                PendingCorrection(
                    id=uuid.uuid4().hex[:12],
                    claim_type=verdict.claim.type,
                    citation=citation,
                    proposition=verdict.forme_correcte,
                    marker=marker,
                    explication=verdict.explication,
                    sources=list(verdict.sources),
                    confiance=verdict.confiance,
                    start=verdict.claim.block_start if verdict.claim.block_start is not None else verdict.claim.start * duration,
                    block_id=verdict.claim.block_id,
                )
            )

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
                    start=verdict.claim.block_start if verdict.claim.block_start is not None else verdict.claim.start * duration,
                    block_id=verdict.claim.block_id,
                    raw_excerpt=citation,
                    clean_excerpt=verdict.forme_correcte or citation,
                    source="web",
                )
            )

    if footnotes:
        text = text.rstrip() + "\n\n" + "\n".join(footnotes) + "\n"

    return text, findings, entities, pending


def accept_pending(clean_text: str, pending: PendingCorrection) -> str | None:
    """Applique une correction en attente à ``clean_text``.

    ``None`` si la citation ne s'y retrouve plus (le texte a changé depuis
    la proposition) : à l'appelant de refuser l'action plutôt que de
    deviner où l'appliquer.
    """
    span = _locate(clean_text, pending.citation)
    if span is None:
        return None
    start_pos, end_pos = span
    text = clean_text[:start_pos] + pending.proposition + clean_text[end_pos:]

    ancienne = f"{pending.marker}: **« {pending.citation} »** — {_PENDING_LABEL}."
    nouvelle = (
        f"{pending.marker}: **« {pending.citation} »** — "
        f"corrigé en **{pending.proposition}** (validé manuellement)."
    )
    return text.replace(ancienne, nouvelle, 1) if ancienne in text else text


def reject_pending(clean_text: str, pending: PendingCorrection) -> str:
    """Met à jour la note de bas de page d'une correction rejetée.

    Le texte transcrit lui-même n'est pas modifié : seule la note change de
    libellé, pour ne pas laisser croire qu'une validation reste en attente.
    """
    ancienne = f"{pending.marker}: **« {pending.citation} »** — {_PENDING_LABEL}."
    nouvelle = f"{pending.marker}: **« {pending.citation} »** — proposition rejetée après relecture."
    return clean_text.replace(ancienne, nouvelle, 1) if ancienne in clean_text else clean_text


# --------------------------------------------------------- orchestration


def _verify_unique_claims_parallel(
    unique_claims: list[Claim],
    *,
    settings,
    on_progress,
    should_cancel,
) -> list[Verdict]:
    """Vérifie ``unique_claims`` (une entrée par clé de cache) en parallèle.

    Ordre déterministe en sortie (même position que l'entrée), quel que soit
    l'ordre réel d'achèvement des threads : la numérotation des notes de
    ``apply_verdicts`` doit rester stable d'une exécution à l'autre.
    """
    total = len(unique_claims)
    results: list[Verdict | None] = [None] * total
    # Dès qu'un worker rencontre un quota épuisé, on arrête d'en soumettre
    # d'autres : marteler un quota épuisé avec plusieurs threads est pire
    # qu'avec un seul.
    quota_hit = threading.Event()
    progress_lock = threading.Lock()
    completed = 0

    def _report_progress():
        nonlocal completed
        if on_progress is None:
            return
        # Appel sous verrou : on_progress écrit en base (voir pipeline._Progress),
        # et plusieurs threads y arrivent de front.
        with progress_lock:
            completed += 1
            on_progress(0.3 + 0.7 * completed / total, f"Vérification {completed}/{total}…")

    def _run(claim: Claim) -> Verdict:
        if quota_hit.is_set():
            return Verdict(
                claim=claim,
                verdict="introuvable",
                confiance="basse",
                explication="Limite d'usage de l'abonnement Claude atteinte au moment de la vérification.",
                origine="quota",
            )
        result = verify_claim(claim, settings=settings)
        if result.origine == "quota":
            quota_hit.set()
        return result

    max_workers = max(1, int(settings.factcheck_workers))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures: dict = {}
        for index, claim in enumerate(unique_claims):
            if should_cancel is not None and should_cancel():
                raise ProofreadError("Fact-check annulé.")
            futures[executor.submit(_run, claim)] = index
        for future in as_completed(futures):
            if should_cancel is not None and should_cancel():
                raise ProofreadError("Fact-check annulé.")
            results[futures[future]] = future.result()
            _report_progress()
    except BaseException:
        # Pas seulement ProofreadError : une erreur de back-end remontée par
        # future.result() laisserait sinon l'executor et ses threads en vie.
        executor.shutdown(cancel_futures=True)
        raise
    else:
        executor.shutdown()
    return results  # type: ignore[return-value]


def factcheck(
    clean_text: str,
    *,
    review_blocks: list[dict] | None = None,
    duration: float = 0.0,
    settings=None,
    on_progress=None,
    should_cancel=None,
) -> tuple[str, FactCheckReport, list[dict]]:
    """Fait tout : repère les affirmations, les vérifie, applique les verdicts."""
    settings = settings or config_module.load_settings()

    claims = extract_claims(
        clean_text,
        review_blocks=review_blocks,
        settings=settings,
        on_progress=_scaled(on_progress, 0.0, 0.3, "Repérage des affirmations…"),
        should_cancel=should_cancel,
    )
    if not claims:
        if on_progress:
            on_progress(1.0, "Aucune affirmation à vérifier.")
        return clean_text, FactCheckReport(claims_checked=0), []

    # Périmètre : seules les catégories coûteuses en cas d'erreur (voir
    # is_in_scope) déclenchent une vérification. Le reste est repéré mais
    # n'entraîne ni appel, ni note, ni Finding.
    in_scope: list[Claim] = []
    skipped: list[dict] = []
    for claim in claims:
        if is_in_scope(claim, settings):
            in_scope.append(claim)
        else:
            skipped.append({"type": claim.type, "citation": claim.citation})

    # Déduplication intra-document : une seule vérification par citation
    # (même clé de cache), réappliquée à chaque occurrence plus bas.
    unique_claims: list[Claim] = []
    position_by_key: dict[str, int] = {}
    for claim in in_scope:
        key = cache_key(claim.type, claim.citation)
        if key not in position_by_key:
            position_by_key[key] = len(unique_claims)
            unique_claims.append(claim)

    resolved = (
        _verify_unique_claims_parallel(
            unique_claims, settings=settings, on_progress=on_progress, should_cancel=should_cancel
        )
        if unique_claims
        else []
    )

    # Un Verdict par occurrence, copié depuis le verdict résolu pour sa clé
    # mais portant son propre Claim (sa propre position) — apply_verdicts en
    # a besoin pour poser une note par occurrence, pas une seule pour toutes.
    verdicts: list[Verdict] = [
        replace(resolved[position_by_key[cache_key(claim.type, claim.citation)]], claim=claim)
        for claim in in_scope
    ]

    text, findings, entities, pending = apply_verdicts(clean_text, verdicts, duration=duration)
    corrections = sum(
        1
        for v in verdicts
        if v.verdict == "corrige"
        and v.confiance == "haute"
        and v.sources
        and v.claim.type not in SENSITIVE_CLAIM_TYPES
    )
    report = FactCheckReport(
        claims_checked=len(verdicts),
        corrections=corrections,
        findings=findings,
        pending=pending,
        skipped=skipped,
    )

    if on_progress:
        on_progress(1.0, "Fact-check terminé.")
    return text, report, entities
