"""Deux façons d'appeler Claude : le CLI (abonnement) ou l'API (clé).

Les deux implémentent le même protocole (``ClaudeBackend``, dans ``base.py``) ;
la relecture, la vérification et le fact-check ne connaissent que lui.
"""
from __future__ import annotations

from .base import BackendResult, ClaudeBackend, get_backend

__all__ = ["BackendResult", "ClaudeBackend", "get_backend"]
