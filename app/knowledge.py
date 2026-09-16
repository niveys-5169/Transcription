"""Propositions de capitalisation inter-cours, toujours soumises à validation."""
from __future__ import annotations

import json

from . import exporters
from .obsidian import index as vault_index
from .proofread import prompts
from .proofread.backends import get_backend


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
    return {"status": "proposed", "concepts": concepts, "themes": themes}
