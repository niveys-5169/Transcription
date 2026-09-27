"""Fiche de révision structurée, facultative et régénérable."""
from __future__ import annotations

import json

from . import exporters
from .proofread.backends import get_backend
from .proofread import prompts


def build_revision(job: dict, settings) -> dict | None:
    if not job.get("claude_opt_in"):
        return None
    backend = get_backend(settings)
    available, _ = backend.is_available()
    if not available:
        return None
    text = exporters.editorial_text(job)
    if not text:
        return None
    result = backend.complete(
        system=prompts.REVISION_SYSTEM,
        user=prompts.REVISION_USER.format(body=text[:180000]),
        max_tokens=8000,
    )
    try:
        data = json.loads(result.text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {key: data.get(key, [] if key != "plan" else []) for key in
            ("points_cles", "definitions", "questions", "flashcards", "plan")}
