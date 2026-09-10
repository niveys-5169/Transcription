"""Relecture : transformer la sortie brute de Whisper en texte lisible."""
from __future__ import annotations

from .base import ProofreadResult, ProofreadError
from .basic import basic_proofread
from .claude import ClaudeProofreader

__all__ = [
    "ProofreadResult",
    "ProofreadError",
    "basic_proofread",
    "ClaudeProofreader",
]
