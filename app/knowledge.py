"""Propositions de capitalisation inter-cours, toujours soumises à validation."""
from __future__ import annotations

import json
import re

from . import config, exporters
from .obsidian.entities import update_themes_index
from .obsidian import index as vault_index
from .proofread import prompts
from .proofread.backends import get_backend
from .obsidian.vault import read, resolve, write_atomic

SOURCES_START = "<!-- sources:début -->"
SOURCES_END = "<!-- sources:fin -->"
SYNTHESIS_START = "<!-- synthese:début -->"
SYNTHESIS_END = "<!-- synthese:fin -->"
_SAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def build_knowledge(job: dict, settings) -> dict | None:
    backend = get_backend(settings)
    available, _ = backend.is_available()
    if not available:
        return None
    body = exporters.editorial_text(job)
    if not body:
        return None
    try:
        notes = vault_index.search(settings, limit=80) if settings.obsidian_vault_path else []
        context = "\n".join(f"- {note['title']}" for note in notes)
        result = backend.complete(
            system=prompts.KNOWLEDGE_SYSTEM,
            user=prompts.KNOWLEDGE_USER.format(body=body[:180000], vault_notes=context or "(aucune note)"),
            max_tokens=6000,
        )
        data = json.loads(result.text)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    concepts = [item for item in data.get("concepts", []) if isinstance(item, dict) and item.get("nom")]
    themes = [item for item in data.get("themes", []) if isinstance(item, dict) and item.get("nom")]
    # Chaque synthèse proposée tient compte de l'état déjà validé du thème.
    # Si cet appel échoue, le thème reste tout de même proposé sans écriture.
    for theme in themes:
        previous = _current_theme_synthesis(settings, str(theme["nom"]))
        theme["previous_synthesis"] = previous
        try:
            update = backend.complete(
                system=prompts.THEME_SYNTHESIS_SYSTEM,
                user=prompts.THEME_SYNTHESIS_USER.format(
                    theme=theme["nom"], previous=previous or "(aucune synthèse existante)", body=body[:120000],
                ),
                max_tokens=3000,
            )
            theme["synthesis"] = str(update.text or "").strip()
        except (OSError, TypeError, ValueError):
            theme["synthesis"] = ""
    return {"status": "proposed", "concepts": concepts, "themes": themes}


def validate_knowledge(job: dict, settings, *, concepts: list[dict], themes: list[dict]) -> dict:
    """Écrit uniquement les régions gérées après validation explicite."""
    if not settings.obsidian_vault_path:
        raise ValueError("Aucun coffre Obsidian configuré dans les réglages.")
    course = job.get("title") or job.get("filename") or "Cours"
    source = f"- [[{course}]]"
    written_concepts, written_themes = [], []
    for concept in concepts:
        name = str(concept.get("nom") or "").strip()
        if not name:
            continue
        path = resolve(settings.obsidian_vault_path, f"{settings.obsidian_concepts_folder}/{_safe(name)}.md")
        existing = read(path)
        if not existing:
            definition = str(concept.get("definition") or "").strip()
            existing = f"---\ntype: concept\n---\n\n# {name}\n" + (f"\n{definition}\n" if definition else "")
        write_atomic(path, _replace_region(existing, SOURCES_START, SOURCES_END, source))
        written_concepts.append(name)
    for theme in themes:
        name = str(theme.get("nom") or "").strip()
        if not name:
            continue
        path = resolve(settings.obsidian_vault_path, f"{settings.obsidian_themes_folder}/{_safe(name)}.md")
        existing = read(path) or f"---\ntype: synthese\n---\n\n# {name}\n"
        previous = _region(existing, SYNTHESIS_START, SYNTHESIS_END)
        if previous.strip():
            _archive_synthesis(name, previous, job)
        synthesis = str(theme.get("synthesis") or "").strip()
        managed = f"{synthesis}\n\n## Sources\n{source}" if synthesis else source
        write_atomic(path, _replace_region(existing, SYNTHESIS_START, SYNTHESIS_END, managed))
        written_themes.append(name)
    if written_themes:
        update_themes_index(settings, written_themes)
    return {"status": "published", "concepts": concepts, "themes": themes, "written": {"concepts": written_concepts, "themes": written_themes}}


def _safe(name: str) -> str:
    return _SAFE_NAME.sub("", name).strip() or "Sans titre"


def _replace_region(content: str, start: str, end: str, line: str) -> str:
    if start in content and end in content:
        before, rest = content.split(start, 1)
        inside, after = rest.split(end, 1)
        lines = [item for item in inside.strip().splitlines() if item.strip() and item.strip() != line]
        lines.append(line)
        return f"{before}{start}\n" + "\n".join(lines) + f"\n{end}{after}".rstrip() + "\n"
    return content.rstrip() + f"\n\n{start}\n{line}\n{end}\n"


def _region(content: str, start: str, end: str) -> str:
    if start not in content or end not in content:
        return ""
    return content.split(start, 1)[1].split(end, 1)[0].strip()


def _current_theme_synthesis(settings, name: str) -> str:
    if not settings.obsidian_vault_path:
        return ""
    path = resolve(settings.obsidian_vault_path, f"{settings.obsidian_themes_folder}/{_safe(name)}.md")
    return _region(read(path), SYNTHESIS_START, SYNTHESIS_END)


def _archive_synthesis(name: str, content: str, job: dict) -> None:
    date = str(job.get("created_at") or "")[:10] or "sans-date"
    path = config.DATA_DIR / "syntheses" / _safe(name) / f"{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
