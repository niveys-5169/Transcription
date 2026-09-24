"""Contrôle qualité ASR : repère les segments Whisper suspects, sans y toucher.

Distinct de la relecture : ce contrôle porte exclusivement sur ce que le
moteur de reconnaissance vocale a produit, avant toute relecture. Il ne
modifie jamais ``segments`` ni ``raw_text`` — c'est un rapport, au même
titre que ``verify.VerificationReport``, jamais une réécriture. Et il ne
demande jamais à un LLM de « reconstruire » un passage suspect : un
segment marqué ici a besoin d'une nouvelle transcription (moteur ou
paramètres différents), pas d'une invention.

Seuls certains signaux textuels déclenchent une seconde passe ASR ciblée.
Un segment seulement long reste un problème de découpage : sans autre
indice textuel, le réécouter avec le même moteur n'apporte pas de signal
exploitable et ne doit donc pas consommer un appel GPU.
"""
from __future__ import annotations

import re

from .chunking import _bounds_of, _text_of

# Une plage de silence Whisper produit un texte vide ; au-delà de cette
# durée sans aucun texte, ça mérite d'être signalé plutôt que silencieux.
EMPTY_SEGMENT_DURATION_THRESHOLD = 8.0

# Un segment plus long que ça est suspect : Whisper découpe normalement sur
# les pauses, un très long segment signale souvent une boucle de décodage.
LONG_SEGMENT_DURATION_THRESHOLD = 45.0

# Une répétition du même mot ou groupe de mots au-delà de ce nombre
# d'occurrences consécutives est caractéristique d'une boucle de tokens.
REPEAT_THRESHOLD = 6

# Une boucle peut aussi porter sur une phrase entière, ponctuation comprise
# (« CRG, Certificat médical de protection des majeurs, CRG, Certificat… »,
# typiquement l'amorce ``initial_prompt`` recrachée par Whisper). À partir de
# LONG_PHRASE_MIN_WORDS mots, trois occurrences consécutives suffisent : une
# phrase aussi longue répétée trois fois d'affilée n'est jamais du discours
# naturel, contrairement à « oui oui » ou « non, non, non ». Même règle côté
# interface (``repetitionLoop`` dans app/static/app.js).
LONG_PHRASE_MIN_WORDS = 4
LONG_PHRASE_MAX_WORDS = 20
LONG_PHRASE_REPEAT_THRESHOLD = 3

_UNK_RE = re.compile(r"<unk>|\[unk\]|\bunk\b", re.IGNORECASE)
# Un « mot » pour la détection de boucle : toute suite hors espaces et hors
# ponctuation courante. U+FFFD y reste un caractère de mot, pour qu'un texte
# déjà corrompu (« m\ufffd\ufffddicale ») soit encore reconnu comme bouclant.
_LOOP_WORD_RE = re.compile(r"[^\s.,;:!?…«»\"()\[\]{}]+")


def _loop_words(text: str) -> list[str]:
    return [word.lower() for word in _LOOP_WORD_RE.findall(text or "")]


def _has_repetition_loop(text: str) -> bool:
    words = _loop_words(text)
    total = len(words)
    for size in range(1, LONG_PHRASE_MAX_WORDS + 1):
        repeats = REPEAT_THRESHOLD if size < LONG_PHRASE_MIN_WORDS else LONG_PHRASE_REPEAT_THRESHOLD
        if size * repeats > total:
            continue
        # Plus longue suite où words[i] == words[i + size] : size·(n-1)
        # égalités d'affilée = n occurrences consécutives du même groupe.
        run = 0
        for index in range(total - size):
            if words[index] == words[index + size]:
                run += 1
                if run >= size * (repeats - 1):
                    return True
            else:
                run = 0
    return False


def _unk_ratio(text: str) -> float:
    words = text.split()
    if not words:
        return 0.0
    unk_count = len(_UNK_RE.findall(text))
    return unk_count / len(words)


def unk_ratio(text: str) -> float:
    """Part de tokens inconnus, exposée pour comparer un retry à l'original."""
    return _unk_ratio(text)


def has_repeated_tokens(text: str) -> bool:
    """Vrai pour la même boucle massive que celle signalée par le contrôle."""
    return _has_repetition_loop(text)


def repeat_score(text: str) -> float:
    """Score mécanique minimal : 1 pour une boucle manifeste, 0 sinon."""
    return 1.0 if has_repeated_tokens(text) else 0.0


def should_retry_asr(issue: str | dict) -> bool:
    """Indique si un finding justifie une seconde passe du même moteur ASR."""
    code = str(issue.get("issue") if isinstance(issue, dict) else issue)
    return code in {
        "boucle_de_tokens",
        "unk_ratio_eleve",
        "texte_vide_plage_longue",
    }


def check_segments(segments: list[dict]) -> list[dict]:
    """Signale les segments ASR suspects. Ne modifie jamais ``segments``.

    Chaque entrée : ``{"segment_index", "start", "end", "issue", "detail"}``.
    Une liste vide est le cas courant et attendu — la plupart des
    transcriptions n'ont rien de suspect.
    """
    issues: list[dict] = []
    previous_end = 0.0

    for index, segment in enumerate(segments, start=1):
        text = _text_of(segment)
        start, end = _bounds_of(segment)
        duration = max(0.0, end - start)
        gap = max(0.0, start - previous_end)
        previous_end = max(previous_end, end)

        stripped = text.strip()

        unk_ratio = _unk_ratio(stripped)
        if unk_ratio > 0.2:
            issues.append({
                "segment_index": index, "start": start, "end": end,
                "issue": "unk_ratio_eleve",
                "detail": f"{unk_ratio:.0%} de tokens <unk> sur ce segment.",
            })

        if has_repeated_tokens(stripped):
            issues.append({
                "segment_index": index, "start": start, "end": end,
                "issue": "boucle_de_tokens",
                "detail": "Répétition massive du même mot ou groupe de mots.",
            })

        if not stripped and (duration >= EMPTY_SEGMENT_DURATION_THRESHOLD or gap >= EMPTY_SEGMENT_DURATION_THRESHOLD):
            issues.append({
                "segment_index": index, "start": start, "end": end,
                "issue": "texte_vide_plage_longue",
                "detail": "Aucun texte reconnu sur une plage audio longue.",
            })

        if duration >= LONG_SEGMENT_DURATION_THRESHOLD:
            issues.append({
                "segment_index": index, "start": start, "end": end,
                "issue": "segment_anormalement_long",
                "detail": f"Segment de {duration:.0f} s, largement au-dessus du typique.",
            })

    return issues
