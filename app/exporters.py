"""Formats de sortie : texte, Markdown, sous-titres, JSON."""
from __future__ import annotations

import json
import re

EXTENSIONS = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "json": "application/json; charset=utf-8",
}

_UNSAFE = re.compile(r"[^\w\- ]+", re.UNICODE)


def timecode(seconds: float, separator: str = ",") -> str:
    """Horodatage ``HH:MM:SS,mmm`` (SRT) ou ``HH:MM:SS.mmm`` (WebVTT)."""
    seconds = max(0.0, float(seconds))
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{milliseconds:03d}"


def to_srt(segments) -> str:
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(index))
        lines.append(
            f"{timecode(segment.get('start', 0))} --> "
            f"{timecode(segment.get('end', 0))}"
        )
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def to_vtt(segments) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            f"{timecode(segment.get('start', 0), '.')} --> "
            f"{timecode(segment.get('end', 0), '.')}"
        )
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def to_json(job: dict) -> str:
    payload = {
        "fichier": job.get("filename"),
        "duree_secondes": job.get("duration"),
        "moteur": job.get("engine"),
        "modele": job.get("model"),
        "langue": job.get("language"),
        "relecture": job.get("proofread"),
        "titre": job.get("title"),
        "resume": _summary_list(job),
        "texte_relu": job.get("clean_text"),
        "texte_brut": job.get("raw_text"),
        "segments": job.get("segments") or [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _summary_list(job: dict) -> list[str]:
    raw = job.get("summary")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def render(job: dict, fmt: str) -> str:
    """Contenu du fichier à télécharger, pour le format demandé."""
    if fmt == "txt":
        return (job.get("clean_text") or job.get("raw_text") or "").strip() + "\n"
    if fmt == "md":
        return _markdown(job)
    if fmt == "srt":
        return to_srt(job.get("segments") or [])
    if fmt == "vtt":
        return to_vtt(job.get("segments") or [])
    if fmt == "json":
        return to_json(job)
    raise ValueError(f"Format inconnu : {fmt}")


def _markdown(job: dict) -> str:
    parts: list[str] = []
    title = job.get("title") or job.get("filename") or "Transcription"
    parts.append(f"# {title}")

    summary = _summary_list(job)
    if summary:
        parts.append("## En bref\n\n" + "\n".join(f"- {point}" for point in summary))

    body = (job.get("clean_text") or job.get("raw_text") or "").strip()
    parts.append(body)
    return "\n\n".join(parts).strip() + "\n"


def safe_filename(name: str, extension: str) -> str:
    """Nom de fichier de téléchargement, débarrassé de tout caractère gênant.

    ``PurePosixPath(...).stem`` écarte d'emblée les séparateurs de chemin ; le
    filtre qui suit ne laisse passer que lettres, chiffres, tirets et espaces,
    ce qui garantit aussi qu'aucun guillemet ne vienne casser l'en-tête
    ``Content-Disposition``.
    """
    from pathlib import PurePosixPath

    stem = PurePosixPath((name or "transcription").replace("\\", "/")).stem
    stem = _UNSAFE.sub("", stem).strip()
    stem = re.sub(r"\s+", "-", stem).strip("-") or "transcription"
    return f"{stem[:80]}.{extension}"
