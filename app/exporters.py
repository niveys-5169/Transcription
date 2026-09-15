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
    # La fiche telle qu'elle serait écrite dans le coffre Obsidian (étape 4) :
    # frontmatter, encart de vérification, notes de bas de page comprises.
    # Téléchargeable même sans coffre configuré — c'est un aperçu.
    "obsidian": "text/markdown; charset=utf-8",
}

# Extension de fichier réelle par format : la plupart correspondent au nom du
# format, sauf « obsidian » qui reste un Markdown ordinaire pour Obsidian.
DOWNLOAD_EXTENSIONS = {**{fmt: fmt for fmt in EXTENSIONS}, "obsidian": "md"}

_UNSAFE = re.compile(r"[^\w\- ]+", re.UNICODE)


def editorial_text(job: dict) -> str:
    """Retourne la version éditoriale sans jamais modifier le texte brut.

    Les blocs de révision ne deviennent canoniques qu'après une correction
    humaine, repérée par comparaison avec le segment brut correspondant.
    Sans correction, garder ``clean_text`` préserve la structure ajoutée par
    la relecture (titre, intertitres et éventuelles notes de sources).
    """
    blocks = job.get("review_blocks") or []
    segments = job.get("segments") or []
    if blocks and (len(blocks) != len(segments) or any(
        str(block.get("text") or "").strip()
        != str(segments[index].get("text") or "").strip()
        for index, block in enumerate(blocks)
        if index < len(segments)
    )):
        return "\n\n".join(str(block.get("text") or "").strip() for block in blocks).strip()
    return (job.get("clean_text") or job.get("raw_text") or "").strip()


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
        "texte_relu": editorial_text(job),
        "texte_brut": job.get("raw_text"),
        "segments": job.get("segments") or [],
        "blocs_revision": job.get("review_blocks") or [],
        "verification": _verification(job),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _verification(job: dict) -> dict:
    raw = job.get("verification")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def findings(job: dict) -> list[dict]:
    """Points à vérifier, du plus grave au moins grave."""
    entries = _verification(job).get("findings")
    return entries if isinstance(entries, list) else []


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
        return editorial_text(job) + "\n"
    if fmt == "md":
        return _markdown(job)
    if fmt == "srt":
        return to_srt(job.get("segments") or [])
    if fmt == "vtt":
        return to_vtt(job.get("segments") or [])
    if fmt == "json":
        return to_json(job)
    if fmt == "obsidian":
        from .obsidian.notes import render_note

        return render_note(job)
    raise ValueError(f"Format inconnu : {fmt}")


def _markdown(job: dict) -> str:
    parts: list[str] = []
    title = job.get("title") or job.get("filename") or "Transcription"
    parts.append(f"# {title}")

    summary = _summary_list(job)
    if summary:
        parts.append("## En bref\n\n" + "\n".join(f"- {point}" for point in summary))

    body = editorial_text(job)
    parts.append(body)

    points = findings(job)
    if points:
        lignes = ["## Points à vérifier", ""]
        lignes.append(
            "_Signalés par la vérification automatique : à confronter à "
            "l'enregistrement._"
        )
        lignes.append("")
        for point in points:
            horodatage = timecode(point.get("start", 0)).split(",")[0]
            gravite = str(point.get("severity", "")).strip()
            message = str(point.get("message", "")).strip()
            lignes.append(f"- **{horodatage}** ({gravite}) — {message}")
        parts.append("\n".join(lignes))

    return "\n\n".join(parts).strip() + "\n"


def course_markdown(job: dict) -> str:
    """Version destinée à la compilation NotebookLM : l'essentiel du cours.

    Contrairement à ``_markdown`` (export complet, verbatim intégral),
    NotebookLM n'a pas besoin de la retranscription mot à mot : seul le
    résumé pédagogique (``## En bref``, produit par la relecture IA)
    est pertinent pour réviser le cours. Sans résumé disponible (relecture
    pas encore passée par l'IA), on retombe sur le texte complet plutôt que
    de ne rien synchroniser du tout.
    """
    parts: list[str] = []
    title = job.get("title") or job.get("filename") or "Transcription"
    parts.append(f"# {title}")

    summary = _summary_list(job)
    if summary:
        parts.append("\n".join(f"- {point}" for point in summary))
    else:
        parts.append(editorial_text(job))

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
