"""Vérification : la relecture est-elle fidèle à ce qui a été dit ?

Une relecture réussie est invisible — c'est bien le problème. Rien ne
distingue, à la lecture, un texte fidèle d'un texte où une date a changé ou
une phrase a disparu. Cette étape compare donc systématiquement les deux
versions de chaque passage et signale ce qui mérite un coup d'œil.

Deux niveaux, complémentaires :

- des **règles**, gratuites et objectives : un chiffre présent à l'oral et
  absent du texte relu est une anomalie, quel que soit le contexte ;
- une **lecture par Claude**, qui repère ce qu'aucune règle ne voit — un sens
  qui glisse, une nuance perdue, une phrase ajoutée.

Les règles tournent toujours, y compris en relecture simple et hors ligne.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from .. import config as config_module
from ..lexicon import lookup as lexicon_lookup
from ..lexicon import near_misses as lexicon_near_misses
from . import prompts
from . import textloc
from .backends import get_backend
from .base import ProofreadError, TextPair
from .structure import parse_json_array

logger = logging.getLogger(__name__)

# Un texte relu nettement plus court que l'oral a perdu quelque chose. Le
# seuil est bas : la relecture retire légitimement les hésitations et les
# répétitions, ce qui fait déjà fondre le texte de 10 à 20 %.
LENGTH_ALERT_RATIO = 0.62

MAX_TOKENS_VERIFICATION = 4_000
EXCERPT_CHARS = 120
# Nombre de mots voisins pris de part et d'autre d'un élément manquant pour
# retrouver son contexte dans le texte relu (voir _missing_context_excerpt).
_CONTEXT_WORDS = 5
_CONTEXT_MIN_LEN = 8

# Un nombre, avec séparateur de milliers et décimales éventuels :
# « 2024 », « 1 000 000 », « 10,5 ». Les séparateurs possibles sont
# l'espace ordinaire, l'espace insécable et l'espace fine insécable.
_THOUSANDS = "[\\u0020\\u00a0\\u202f]"
_NUMBER_RE = re.compile(rf"\d+(?:{_THOUSANDS}\d{{3}})*(?:[.,]\d+)?")
_SPACES_RE = re.compile(rf"{_THOUSANDS}+")
# Un sigle : au moins deux capitales d'affilée (ADN, PIB, HTML).
_ACRONYM_RE = re.compile(r"(?<![\w-])[A-ZÀ-ÖØ-Þ]{2,}(?![\w-])")

SEVERITIES = ("haute", "moyenne", "basse")
KINDS = (
    "omission",
    "ajout",
    "sens",
    "terme",
    "chiffre",
    "coupure",
    # Ajoutés par le fact-check (voir factcheck.py) et le lexique MJPM.
    "fait",
    "source",
    "lexique",
)


@dataclass
class Finding:
    """Un point à vérifier dans la transcription relue."""

    kind: str
    severity: str
    message: str
    start: float = 0.0
    raw_excerpt: str = ""
    clean_excerpt: str = ""
    source: str = "regles"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationReport:
    findings: list[Finding] = field(default_factory=list)
    checked_pairs: int = 0
    # Passages au brut ou au relu vide : ni les règles ni Claude ne peuvent
    # rien en dire. Rendu visible plutôt que silencieusement ignoré.
    skipped_pairs: int = 0
    mode: str = "regles"

    def to_dict(self) -> dict:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "checked_pairs": self.checked_pairs,
            "skipped_pairs": self.skipped_pairs,
            "mode": self.mode,
            "counts": self.counts(),
        }

    def counts(self) -> dict:
        return {
            severity: sum(1 for f in self.findings if f.severity == severity)
            for severity in SEVERITIES
        }


# ------------------------------------------------------------------ règles


def _normalise_number(raw: str) -> str:
    """« 1 000 » et « 1000 » sont le même nombre ; « 10,5 » et « 10.5 » aussi."""
    return _SPACES_RE.sub("", raw).replace(",", ".").rstrip(".")


def _numbers(text: str) -> list[str]:
    return [_normalise_number(match.group()) for match in _NUMBER_RE.finditer(text)]


def _excerpt_from_span(text: str, span: tuple[int, int] | None) -> str:
    """Extrait centré sur ``span`` (bornes caractère dans ``text``), ou vide.

    Un extrait absent est plus honnête qu'un extrait qui ne correspond à
    rien : mieux vaut ne rien montrer que montrer le mauvais endroit.
    """
    if span is None:
        return ""
    position, _end = span
    start = max(0, position - EXCERPT_CHARS // 2)
    fragment = " ".join(text[start : start + EXCERPT_CHARS].split())
    if not fragment:
        return ""
    return ("…" if start else "") + fragment + ("…" if start + EXCERPT_CHARS < len(text) else "")


def _excerpt(text: str, around: str = "") -> str:
    """Court extrait, centré sur ``around`` quand on sait où regarder."""
    text = " ".join(text.split())
    if around:
        position = text.find(around)
        if position != -1:
            return _excerpt_from_span(text, (position, position + len(around)))
    return text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")


def _neighbouring_words(text: str, span: tuple[int, int], *, words: int = _CONTEXT_WORDS) -> str:
    """Quelques mots de part et d'autre de ``span`` dans ``text``, ``span`` exclu."""
    tokens = textloc.tokenize_words(text)
    before = [word for word, _start, end in tokens if end <= span[0]][-words:]
    after = [word for word, start, _end in tokens if start >= span[1]][:words]
    return " ".join(before + after)


def _missing_context_excerpt(raw_text: str, clean_text: str, token: str) -> str:
    """``clean_excerpt`` pour un chiffre/sigle absent du texte relu.

    Puisque l'élément a disparu, impossible de l'ancrer lui-même côté relu :
    on ancre plutôt sur son voisinage immédiat dans le texte brut, retrouvé
    dans le texte relu de façon tolérante à la ponctuation (``textloc``).
    Si ce voisinage a lui aussi été reformulé, il n'y a pas de position
    fiable à montrer : l'extrait reste vide plutôt que de pointer ailleurs.

    La fenêtre de voisinage rétrécit si elle échoue : un chiffre trop proche
    d'une autre anomalie (un second nombre manquant à deux mots de là, par
    exemple) ferait échouer une fenêtre large en y incluant elle aussi de
    quoi ne plus se retrouver dans le texte relu.
    """
    raw_norm = " ".join(raw_text.split())
    clean_norm = " ".join(clean_text.split())
    position = raw_norm.find(token)
    if position == -1:
        return ""
    span = (position, position + len(token))

    for words in range(_CONTEXT_WORDS, 0, -1):
        context = _neighbouring_words(raw_norm, span, words=words)
        if len(context) < _CONTEXT_MIN_LEN:
            continue
        located = textloc.locate(clean_norm, context, min_len=_CONTEXT_MIN_LEN)
        if located is not None:
            return _excerpt_from_span(clean_norm, located)
    return ""


def _coupure_spans(
    raw_text: str, clean_text: str
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Bornes du passage réellement disparu à la relecture, brut et relu.

    Plutôt que de deviner, on aligne les deux textes mot à mot (comme un
    suivi de modifications) et on prend le plus grand bloc supprimé ou
    remplacé. Côté relu, une suppression pure n'a pas de bornes propres :
    on retient le point d'insertion (fin du mot précédent) plutôt que le
    début du passage.
    """
    raw_tokens = textloc.tokenize_words(raw_text)
    clean_tokens = textloc.tokenize_words(clean_text)
    candidats = [op for op in textloc.word_opcodes(raw_text, clean_text) if op[0] in ("delete", "replace")]
    if not candidats:
        return None, None

    _tag, i1, i2, j1, j2 = max(candidats, key=lambda op: op[2] - op[1])
    raw_span = (raw_tokens[i1][1], raw_tokens[i2 - 1][2]) if i2 > i1 else None
    if j2 > j1:
        clean_span = (clean_tokens[j1][1], clean_tokens[j2 - 1][2])
    elif j1 > 0:
        point = clean_tokens[j1 - 1][2]
        clean_span = (point, point)
    else:
        clean_span = None
    return raw_span, clean_span


def rule_findings(pairs: list[TextPair], *, lexicon_enabled: bool = True) -> list[Finding]:
    """Contrôles mécaniques, sans appel réseau."""
    findings: list[Finding] = []

    for pair in pairs:
        # 1. Un chiffre prononcé qui disparaît du texte relu. On ne regarde
        #    que ce sens-là : écrire « 20 » là où l'oral disait « vingt » est
        #    une amélioration, pas une perte.
        manquants = _multiset_difference(_numbers(pair.raw), _numbers(pair.clean))
        for nombre in manquants:
            findings.append(
                Finding(
                    kind="chiffre",
                    severity="haute",
                    message=f"Le nombre « {nombre} » est prononcé mais absent du texte relu.",
                    start=pair.start,
                    raw_excerpt=_excerpt(pair.raw, nombre),
                    clean_excerpt=_missing_context_excerpt(pair.raw, pair.clean, nombre),
                )
            )

        # 2. Un sigle qui disparaît : souvent le cœur d'un cours technique.
        #    Gravité relevée à « haute » quand le sigle figure au lexique
        #    MJPM — ce n'est alors pas un détail, mais un terme du métier.
        sigles = _multiset_difference(
            _ACRONYM_RE.findall(pair.raw), _ACRONYM_RE.findall(pair.clean)
        )
        for sigle in sigles:
            connu = lexicon_enabled and lexicon_lookup(sigle, verified_only=False) is not None
            findings.append(
                Finding(
                    kind="terme",
                    severity="haute" if connu else "moyenne",
                    message=f"Le sigle « {sigle} » est prononcé mais absent du texte relu.",
                    start=pair.start,
                    raw_excerpt=_excerpt(pair.raw, sigle),
                    clean_excerpt=_missing_context_excerpt(pair.raw, pair.clean, sigle),
                )
            )

        # 3. Un passage qui a fondu.
        if pair.raw and len(pair.clean) < len(pair.raw) * LENGTH_ALERT_RATIO:
            perte = round((1 - len(pair.clean) / len(pair.raw)) * 100)
            raw_span, clean_span = _coupure_spans(pair.raw, pair.clean)
            findings.append(
                Finding(
                    kind="coupure",
                    severity="haute",
                    message=(
                        f"Ce passage a perdu {perte} % de sa longueur à la "
                        "relecture : à comparer avec le texte brut."
                    ),
                    start=pair.start,
                    raw_excerpt=_excerpt_from_span(pair.raw, raw_span) if raw_span else _excerpt(pair.raw),
                    clean_excerpt=_excerpt_from_span(pair.clean, clean_span) if clean_span else "",
                )
            )

        # 4. Une graphie proche d'un terme du lexique, sans lui être
        #    identique : signe possible d'une déformation de reconnaissance
        #    vocale que la relecture aurait laissée passer (« DIPEM » pour
        #    « DIPM »). Purement mécanique, aucun appel réseau.
        if lexicon_enabled:
            for mot, terme in lexicon_near_misses(pair.clean):
                findings.append(
                    Finding(
                        kind="lexique",
                        severity="basse",
                        message=(
                            f"« {mot} » ressemble à « {terme.terme} »"
                            + (f" ({'/'.join(terme.sigles)})" if terme.sigles else "")
                            + " sans lui être identique : graphie à vérifier."
                        ),
                        start=pair.start,
                        raw_excerpt=_excerpt(pair.raw, mot),
                        clean_excerpt=_excerpt(pair.clean, mot),
                        source="lexique",
                    )
                )

    return findings


def _multiset_difference(left: list[str], right: list[str]) -> list[str]:
    """Éléments de ``left`` qui manquent dans ``right``, multiplicité comprise."""
    restants = list(right)
    manquants = []
    for item in left:
        if item in restants:
            restants.remove(item)
        else:
            manquants.append(item)
    # Sans doublons, en gardant l'ordre d'apparition.
    return list(dict.fromkeys(manquants))


# ------------------------------------------------------------------ Claude


class ClaudeVerifier:
    """Relit la relecture : ce qu'aucune règle ne peut voir."""

    name = "verification"

    def __init__(self, settings=None):
        from ..config import load_settings

        self.settings = settings or load_settings()
        self.backend = get_backend(self.settings)

    def is_available(self) -> tuple[bool, str]:
        return self.backend.is_available()

    def verify(
        self,
        pairs: list[TextPair],
        *,
        on_progress=None,
        should_cancel=None,
    ) -> list[Finding]:
        available, detail = self.is_available()
        if not available:
            raise ProofreadError(detail)

        findings: list[Finding] = []
        skipped = 0

        for index, pair in enumerate(pairs):
            if should_cancel is not None and should_cancel():
                raise ProofreadError("Vérification annulée.")
            if on_progress:
                on_progress(
                    index / max(len(pairs), 1),
                    f"Vérification du bloc {index + 1}/{len(pairs)}…",
                )
            if not pair.raw.strip() or not pair.clean.strip():
                skipped += 1
                continue

            reponse = self.backend.complete(
                system=prompts.VERIFICATION_SYSTEM,
                user=prompts.VERIFICATION_USER.format(
                    brut=pair.raw, relu=pair.clean
                ),
                max_tokens=MAX_TOKENS_VERIFICATION,
            ).text
            findings.extend(self._parse(reponse, pair))

        if skipped:
            logger.info("%d passage(s) non vérifié(s) par Claude : brut ou relu vide.", skipped)
        return findings

    @staticmethod
    def _parse(reponse: str, pair: TextPair) -> list[Finding]:
        findings = []
        for brut in parse_json_array(reponse):
            if not isinstance(brut, dict):
                continue
            message = str(brut.get("commentaire") or "").strip()
            if not message:
                continue
            kind = str(brut.get("type") or "sens").strip().lower()
            severity = str(brut.get("gravite") or "moyenne").strip().lower()
            findings.append(
                Finding(
                    kind=kind if kind in KINDS else "sens",
                    severity=severity if severity in SEVERITIES else "moyenne",
                    message=message,
                    start=pair.start,
                    raw_excerpt=str(brut.get("brut") or "")[:EXCERPT_CHARS],
                    clean_excerpt=str(brut.get("relu") or "")[:EXCERPT_CHARS],
                    source="claude",
                )
            )
        return findings


# --------------------------------------------------------------- orchestration


_ORDER = {"haute": 0, "moyenne": 1, "basse": 2}


def verify(
    pairs: list[TextPair],
    *,
    use_claude: bool = True,
    settings=None,
    on_progress=None,
    should_cancel=None,
) -> VerificationReport:
    """Vérifie une relecture. Les règles tournent toujours ; Claude si possible.

    Un échec de la vérification par Claude ne fait pas échouer le travail :
    le rapport des règles est rendu tel quel, et l'utilisateur garde son
    texte. Vérifier est un service, pas une condition.
    """
    lexicon_enabled = (settings or config_module.load_settings()).lexicon_enabled
    report = VerificationReport(
        findings=rule_findings(pairs, lexicon_enabled=lexicon_enabled),
        checked_pairs=len(pairs),
        skipped_pairs=sum(1 for p in pairs if not p.raw.strip() or not p.clean.strip()),
    )

    if use_claude and pairs:
        verifier = ClaudeVerifier(settings)
        available, detail = verifier.is_available()
        if not available:
            logger.info("Vérification par Claude indisponible : %s", detail)
        else:
            try:
                report.findings.extend(
                    verifier.verify(
                        pairs, on_progress=on_progress, should_cancel=should_cancel
                    )
                )
                report.mode = "claude"
            except ProofreadError as exc:
                if should_cancel is not None and should_cancel():
                    raise
                logger.warning("Vérification par Claude interrompue : %s", exc)

    report.findings.sort(key=lambda f: (_ORDER.get(f.severity, 3), f.start))
    if on_progress:
        on_progress(1.0, "Vérification terminée.")
    return report
