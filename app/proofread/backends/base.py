"""Interface commune aux deux back-ends : le CLI ``claude`` ou l'API Anthropic.

La relecture, la vérification de fidélité et le fact-check appellent Claude de
la même façon quel que soit le back-end choisi dans les réglages
(``claude_backend``) : un texte système, un texte utilisateur, et en retour un
texte, éventuellement structuré selon un schéma JSON, éventuellement épaulé
d'une recherche web. Le choix du back-end ne les concerne pas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class BackendResult:
    """Résultat d'un appel à Claude, quel que soit le back-end.

    ``parsed`` n'est renseigné que si un ``schema`` a été demandé et que la
    réponse a pu être validée contre lui ; sinon les appelants retombent sur
    ``text`` et leurs propres analyseurs tolérants (voir ``structure.py``).
    """

    text: str
    web_searches: int = 0
    sources: list[str] = field(default_factory=list)
    parsed: dict | list | None = None


class ClaudeBackend(Protocol):
    """Ce qu'attendent de Claude la relecture, la vérification et le fact-check."""

    def is_available(self) -> tuple[bool, str]:
        """Le back-end peut-il être utilisé maintenant ? Message expliquant pourquoi sinon."""
        ...

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        schema: dict | None = None,
        web_search: bool = False,
        fast: bool = False,
    ) -> BackendResult:
        """Un appel, une réponse. Lève ``ProofreadError`` en cas d'échec.

        ``fast=True`` demande le couple modèle/effort rapide des réglages
        (``proofread_model_fast``/``proofread_effort_fast``), réservé aux
        passes mécaniques (repérage des affirmations, comparaison
        brut/relu) — jamais à la relecture ni aux verdicts de fact-check.
        """
        ...


def get_backend(settings=None) -> ClaudeBackend:
    """Choisit le back-end selon les réglages (``claude_backend``)."""
    from ... import config as config_module

    settings = settings or config_module.load_settings()
    if settings.claude_backend == "api":
        from .api import ApiBackend

        return ApiBackend(settings)
    from .cli import CliBackend

    return CliBackend(settings)
