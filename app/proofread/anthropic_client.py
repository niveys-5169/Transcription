"""Socle commun aux étapes qui passent par l'API Anthropic.

La relecture et la vérification ont le même besoin : un appel, un texte en
retour, et des messages d'erreur compréhensibles par quelqu'un qui n'a pas
écrit le programme.
"""
from __future__ import annotations

import importlib.util
import logging

from ..config import Settings, load_settings
from .base import ProofreadError

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Appelle l'API Anthropic, en streaming, avec repli si l'effort est refusé."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()

    def is_available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("anthropic") is None:
            return False, (
                "Le paquet « anthropic » n'est pas installé "
                "(pip install -r requirements-app.txt)."
            )
        if not self.settings.anthropic_api_key:
            return False, "Clé API Anthropic à renseigner dans les réglages."
        return True, f"Modèle {self.settings.proofread_model} configuré."

    def _client(self):
        import anthropic

        return anthropic.Anthropic(api_key=self.settings.anthropic_api_key)

    def _call(self, client, *, system: str, user: str, max_tokens: int) -> str:
        import anthropic

        request = {
            "model": self.settings.proofread_model,
            "max_tokens": max_tokens,
            # Le prompt système est identique d'un bloc à l'autre : le mettre
            # en cache évite de le repayer à chaque appel sur un long cours.
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user}],
        }
        effort = {"output_config": {"effort": self.settings.proofread_effort}}

        try:
            message = self._stream(client, {**request, **effort})
        except anthropic.BadRequestError as exc:
            # Certains modèles n'acceptent pas « effort » : on réessaie sans.
            if "effort" not in str(exc).lower():
                raise ProofreadError(self._explain(exc)) from exc
            logger.info("Modèle sans réglage d'effort, nouvel essai sans.")
            try:
                message = self._stream(client, request)
            except anthropic.APIError as retry_exc:
                raise ProofreadError(self._explain(retry_exc)) from retry_exc
        except anthropic.APIError as exc:
            raise ProofreadError(self._explain(exc)) from exc

        if getattr(message, "stop_reason", None) == "refusal":
            raise ProofreadError(
                "Le modèle a refusé de traiter ce passage. Utilisez la relecture "
                "simple pour ce fichier."
            )

        return "".join(
            block.text
            for block in message.content
            if getattr(block, "type", "") == "text"
        )

    @staticmethod
    def _stream(client, request: dict):
        with client.messages.stream(**request) as stream:
            return stream.get_final_message()

    @staticmethod
    def _explain(exc: Exception) -> str:
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            return "Clé API Anthropic refusée : vérifiez-la dans les réglages."
        if isinstance(exc, anthropic.RateLimitError):
            return "Limite de débit Anthropic atteinte. Réessayez dans un moment."
        if isinstance(exc, anthropic.NotFoundError):
            return "Modèle Anthropic introuvable : vérifiez son identifiant."
        if isinstance(exc, anthropic.APIConnectionError):
            return "Impossible de joindre l'API Anthropic (connexion réseau ?)."
        return f"Erreur de l'API Anthropic : {exc}"
