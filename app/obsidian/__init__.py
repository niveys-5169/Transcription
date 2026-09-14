"""Point d'entrée : publie un travail dans le coffre Obsidian.

Écrit la fiche, crée les fiches d'entités manquantes (jamais celles qui
existent déjà), met à jour le MOC et régénère le glossaire. Republier un
travail déjà publié réécrit la même fiche (son chemin est mémorisé en base
dans ``obsidian_path``), il n'en crée pas une seconde.
"""
from __future__ import annotations

from .. import config as config_module
from .entities import ensure_entity_notes, update_index, write_glossary
from .notes import filename_for, render_note
from .vault import ObsidianError, resolve, write_atomic

__all__ = ["ObsidianError", "publish"]


def publish(job: dict, *, settings=None) -> str:
    """Écrit la fiche (et ce qui en dépend) dans le coffre.

    Renvoie le chemin relatif écrit, à mémoriser dans ``jobs.obsidian_path``.
    """
    settings = settings or config_module.load_settings()
    if not settings.obsidian_vault_path:
        raise ObsidianError("Aucun coffre Obsidian configuré dans les réglages.")

    existing = job.get("obsidian_path")
    if existing:
        relative = existing
    else:
        name = filename_for(job, settings.obsidian_filename_template)
        relative = f"{settings.obsidian_notes_folder}/{name}.md"

    path = resolve(settings.obsidian_vault_path, relative)
    write_atomic(path, render_note(job))

    entities = job.get("entities") or []
    ensure_entity_notes(settings, entities)

    verification = job.get("verification") or {}
    findings = verification.get("findings") if isinstance(verification, dict) else []
    reels = [f for f in (findings or []) if f.get("kind") in ("fait", "source")]

    title = job.get("title") or job.get("filename") or "Transcription"
    wikilink = filename_for(job, settings.obsidian_filename_template)
    duree_minutes = (job.get("duration") or 0) / 60
    update_index(
        settings,
        title=title,
        wikilink=wikilink,
        duration_minutes=duree_minutes,
        incertains=len(reels),
    )

    write_glossary(settings)

    return relative
