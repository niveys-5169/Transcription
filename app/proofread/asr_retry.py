"""Seconde passe ASR déterministe sur les seuls passages suspects.

La première passe reste immuable. Ce module produit une tentative avec sa
provenance, puis une décision locale sans LLM, web ou moteur supplémentaire.
"""
from __future__ import annotations

import copy
import logging
import re
import tempfile
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from .. import media
from ..engines.base import Segment, TranscriptionError
from . import asr_quality

logger = logging.getLogger(__name__)

ASR_RETRY_PADDING_SECONDS = 3.0
ASR_RETRY_TIME_TOLERANCE_SECONDS = 0.5
ASR_RETRY_LOW_SIMILARITY = 0.35
ASR_RETRY_MAX_EXPANSION = 4.0
MAX_ASR_RETRIES_PER_FILE = 10

_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_ACRONYM_RE = re.compile(r"\b[A-ZÀ-ÖØ-Þ]{2,}(?:-[A-ZÀ-ÖØ-Þ]{2,})*\b")
_LEGAL_REFERENCE_RE = re.compile(
    r"\b(?:articles?|alin[ée]as?|loi|d[ée]crets?|ordonnances?)\s+"
    r"(?:[LRD]\.?\s*)?\d+(?:[-.]\d+)*", re.IGNORECASE,
)


@dataclass
class ASRRetryCandidate:
    original_start: float
    original_end: float
    clip_start: float
    clip_end: float
    original_text: str
    retry_text: str
    issue: str
    issues: list[str] = field(default_factory=list)
    engine: str | None = None
    model: str | None = None
    language: str | None = None
    retry_segments: list[dict] | None = None
    retry_words: list[dict] | None = None


@dataclass
class ASRRepairDecision:
    action: Literal["keep_original", "use_retry", "needs_review"]
    reasons: list[str]
    metrics: dict


def build_retry_clip_bounds(
    start: float,
    end: float,
    *,
    media_duration: float,
    padding_seconds: float = ASR_RETRY_PADDING_SECONDS,
) -> tuple[float, float]:
    return (
        max(0.0, float(start) - padding_seconds),
        min(float(media_duration), float(end) + padding_seconds),
    )


def _normalise(text: str) -> str:
    return " ".join(re.findall(r"\w+|<unk>", text.lower(), re.UNICODE))


def _word_count(text: str) -> int:
    return len(_normalise(text).split())


def _sensitive_references(text: str) -> set[str]:
    refs = {
        match.group(0).lower().replace(" ", "")
        for match in _LEGAL_REFERENCE_RE.finditer(text)
    }
    refs.update(_NUMBER_RE.findall(text))
    refs.update(_ACRONYM_RE.findall(text))
    return refs


def _overlaps(start: float, end: float, target_start: float, target_end: float) -> bool:
    return (
        end >= target_start - ASR_RETRY_TIME_TOLERANCE_SECONDS
        and start <= target_end + ASR_RETRY_TIME_TOLERANCE_SECONDS
    )


def _shift_and_select(
    segments: list[Segment], *, clip_start: float,
    original_start: float, original_end: float,
) -> tuple[list[dict], list[dict], str]:
    shifted = [segment.shifted(clip_start).to_dict() for segment in segments]
    relevant_segments = [
        segment for segment in shifted
        if _overlaps(float(segment["start"]), float(segment["end"]), original_start, original_end)
    ]
    all_words = [word for segment in shifted for word in (segment.get("words") or [])]
    relevant_words = [
        word for word in all_words
        if _overlaps(float(word["start"]), float(word["end"]), original_start, original_end)
    ]
    if all_words:
        text = " ".join(str(word.get("text") or "").strip() for word in relevant_words).strip()
    else:
        text = " ".join(str(segment.get("text") or "").strip() for segment in relevant_segments).strip()
    return relevant_segments, relevant_words, text


def retry_suspect_segment(
    audio_path: Path,
    segment: dict,
    issues: list[str],
    *,
    media_duration: float,
    engine,
    model: str,
    language: str | None,
    initial_prompt: str | None,
    workdir: Path,
    on_progress=None,
    should_cancel=None,
) -> ASRRetryCandidate:
    """Extrait, retranscrit et recale un passage, sans choisir le gagnant."""
    original_start = float(segment.get("start") or 0.0)
    original_end = float(segment.get("end") or 0.0)
    clip_start, clip_end = build_retry_clip_bounds(
        original_start, original_end, media_duration=media_duration,
    )
    if clip_end <= clip_start:
        raise media.MediaError("Plage de retry ASR vide.")
    if should_cancel and should_cancel():
        raise TranscriptionError("Transcription annulée.")

    workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asr-retry-", dir=workdir) as temp_dir:
        clip_path = Path(temp_dir) / "clip.wav"
        media.extract_wav_clip(
            audio_path, clip_path, start=clip_start, end=clip_end,
            should_cancel=should_cancel,
        )
        retry_segments = list(engine.transcribe(
            clip_path, model=model, language=language,
            duration=clip_end - clip_start, workdir=Path(temp_dir),
            initial_prompt=initial_prompt, on_progress=on_progress,
            should_cancel=should_cancel,
        ))

    selected_segments, selected_words, retry_text = _shift_and_select(
        retry_segments, clip_start=clip_start,
        original_start=original_start, original_end=original_end,
    )
    if not retry_text:
        raise TranscriptionError("Le retry ASR n'a rendu aucun texte dans la plage ciblée.")
    return ASRRetryCandidate(
        original_start=original_start, original_end=original_end,
        clip_start=clip_start, clip_end=clip_end,
        original_text=str(segment.get("text") or ""), retry_text=retry_text,
        issue=issues[0], issues=list(issues), engine=getattr(engine, "name", None),
        model=model, language=language, retry_segments=selected_segments,
        retry_words=selected_words or None,
    )


def choose_asr_candidate(
    original: str | ASRRetryCandidate,
    retry: str | None = None,
    issue: str | None = None,
) -> ASRRepairDecision:
    """Compare deux textes avec une politique conservatrice et déterministe."""
    if isinstance(original, ASRRetryCandidate):
        original_text, retry_text = original.original_text, original.retry_text
        issue = issue or original.issue
    else:
        original_text, retry_text = str(original or ""), str(retry or "")
        issue = str(issue or "")

    original_words, retry_words = _word_count(original_text), _word_count(retry_text)
    original_unk = asr_quality.unk_ratio(original_text)
    retry_unk = asr_quality.unk_ratio(retry_text)
    original_repeat = asr_quality.repeat_score(original_text)
    retry_repeat = asr_quality.repeat_score(retry_text)
    similarity = SequenceMatcher(None, _normalise(original_text), _normalise(retry_text)).ratio()
    metrics = {
        "original_word_count": original_words, "retry_word_count": retry_words,
        "original_unk_ratio": original_unk, "retry_unk_ratio": retry_unk,
        "original_repeat_score": original_repeat, "retry_repeat_score": retry_repeat,
        "text_similarity": similarity,
    }

    if not retry_text.strip():
        return ASRRepairDecision("keep_original", ["retry_empty"], metrics)
    if asr_quality.has_repeated_tokens(retry_text):
        action = "needs_review" if asr_quality.has_repeated_tokens(original_text) else "keep_original"
        return ASRRepairDecision(action, ["retry_still_repetitive"], metrics)
    if original_words and retry_words / max(original_words, 3) > ASR_RETRY_MAX_EXPANSION:
        return ASRRepairDecision("needs_review", ["retry_abnormal_expansion"], metrics)
    original_refs, retry_refs = _sensitive_references(original_text), _sensitive_references(retry_text)
    if original_refs != retry_refs and (original_refs or retry_refs):
        return ASRRepairDecision("needs_review", ["sensitive_reference_divergence"], metrics)

    if issue == "boucle_de_tokens":
        if original_repeat > retry_repeat and retry_words:
            return ASRRepairDecision("use_retry", ["repetition_removed"], metrics)
        return ASRRepairDecision("needs_review", ["repetition_not_improved"], metrics)
    if issue == "unk_ratio_eleve":
        if retry_unk < original_unk and retry_words:
            if similarity < ASR_RETRY_LOW_SIMILARITY:
                return ASRRepairDecision("needs_review", ["low_similarity"], metrics)
            return ASRRepairDecision("use_retry", ["unk_ratio_reduced"], metrics)
        return ASRRepairDecision("needs_review", ["unk_ratio_not_improved"], metrics)
    if issue == "texte_vide_plage_longue":
        if not original_text.strip() and retry_words:
            return ASRRepairDecision("use_retry", ["speech_recovered"], metrics)
        return ASRRepairDecision("keep_original", ["no_speech_recovered"], metrics)
    if original_words >= 4 and retry_words >= 4 and similarity < ASR_RETRY_LOW_SIMILARITY:
        return ASRRepairDecision("needs_review", ["low_similarity"], metrics)
    return ASRRepairDecision("keep_original", ["no_retry_policy"], metrics)


def _retry_groups(quality_issues: list[dict]) -> list[tuple[int, list[str]]]:
    grouped: dict[int, list[str]] = {}
    for finding in quality_issues:
        if not asr_quality.should_retry_asr(finding):
            continue
        index = int(finding.get("segment_index") or 0)
        if index > 0:
            grouped.setdefault(index, []).append(str(finding["issue"]))
    priority = {
        "boucle_de_tokens": 0,
        "unk_ratio_eleve": 1,
        "texte_vide_plage_longue": 2,
    }
    return [
        (index, sorted(issues, key=lambda code: priority.get(code, 99)))
        for index, issues in grouped.items()
    ]


def apply_asr_retries(
    segments: list[dict], quality_issues: list[dict], *, audio_path: Path,
    media_duration: float, engine, model: str, language: str | None,
    initial_prompt: str | None, workdir: Path, on_progress=None,
    should_cancel=None,
) -> None:
    """Ajoute les tentatives aux segments originaux, sans modifier leur RAW."""
    for position, (segment_index, issues) in enumerate(_retry_groups(quality_issues)):
        segment = segments[segment_index - 1]
        if position >= MAX_ASR_RETRIES_PER_FILE:
            segment["asr_retry"] = {
                "attempted": False, "issues": issues, "decision": "needs_review",
                "reasons": ["retry_limit_reached"], "metrics": {},
            }
            continue
        if should_cancel and should_cancel():
            raise TranscriptionError("Transcription annulée.")
        clip_start, clip_end = build_retry_clip_bounds(
            float(segment.get("start") or 0.0),
            float(segment.get("end") or 0.0),
            media_duration=media_duration,
        )
        logger.info(
            "ASR retry segment=%s issues=%s clip=%.1f-%.1f",
            segment_index, ",".join(issues), clip_start, clip_end,
        )
        try:
            candidate = retry_suspect_segment(
                audio_path, segment, issues, media_duration=media_duration,
                engine=engine, model=model, language=language,
                initial_prompt=initial_prompt, workdir=workdir,
                on_progress=on_progress, should_cancel=should_cancel,
            )
            decision = choose_asr_candidate(candidate)
            metadata = asdict(candidate)
            metadata.update({
                "attempted": True, "decision": decision.action,
                "reasons": decision.reasons, "metrics": decision.metrics,
            })
            segment["asr_retry"] = metadata
            if decision.action == "use_retry":
                segment["effective_text"] = candidate.retry_text
                if candidate.retry_words is not None:
                    segment["effective_words"] = candidate.retry_words
            logger.info(
                "ASR retry segment=%s decision=%s reason=%s",
                segment_index, decision.action, ",".join(decision.reasons),
            )
        except Exception as exc:  # un retry secondaire ne perd jamais le RAW
            if should_cancel and should_cancel():
                raise
            segment["asr_retry"] = {
                "attempted": True, "issues": issues, "retry_failed": True,
                "decision": "keep_original", "reasons": ["retry_failed"],
                "metrics": {}, "error": str(exc),
            }
            logger.warning("ASR retry segment=%s failed: %s", segment_index, exc)


def effective_asr_segments(segments: list[dict]) -> list[dict]:
    """Vue aval : applique seulement les retries décidés ``use_retry``."""
    effective = copy.deepcopy(segments)
    for segment in effective:
        retry = segment.get("asr_retry") or {}
        if retry.get("decision") == "use_retry" and segment.get("effective_text"):
            segment["text"] = segment["effective_text"]
            if "effective_words" in segment:
                segment["words"] = segment["effective_words"]
    return effective
