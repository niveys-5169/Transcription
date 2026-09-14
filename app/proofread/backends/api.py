"""Back-end API : appelle l'API Anthropic avec une clé, facturée au jeton.

C'est l'implémentation historique de l'application, conservée comme option
(``claude_backend: "api"``) et comme repli si le CLI n'est pas disponible sur
la machine. Le back-end par défaut est désormais le CLI ``claude``, qui
s'authentifie sur l'abonnement plutôt que sur un compte API — voir ``cli.py``.
"""
from __future__ import annotations

import importlib.util
import logging

from ...config import Settings, load_settings
from ..base import ProofreadError
from ..structure import parse_json_array, parse_json_object
from .base import BackendResult

logger = logging.getLogger(__name__)

# Nécessite Opus 5/4.8/4.7/4.6, Sonnet 5 ou Sonnet 4.6 — le modèle par défaut
# de l'application (claude-sonnet-5) le supporte.
WEB_SEARCH_TOOL = "web_search_20260209"


class ApiBackend:
    """Appelle l'API Anthropic, en streaming, avec repli si l'effort est refusé."""

    name = "api"

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

    # ------------------------------------------------------------------ appel

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        schema: dict | None = None,
        web_search: bool = False,
    ) -> BackendResult:
        import anthropic

        client = self._client()
        request: dict = {
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
        if web_search:
            request["tools"] = [
                {
                    "type": WEB_SEARCH_TOOL,
                    "name": "web_search",
                    "max_uses": self.settings.factcheck_max_searches,
                }
            ]
        effort = {"output_config": {"effort": self.settings.proofread_effort}}

        try:
            message = self._run(client, {**request, **effort})
        except anthropic.BadRequestError as exc:
            # Certains modèles n'acceptent pas « effort » : on réessaie sans.
            if "effort" not in str(exc).lower():
                raise ProofreadError(self._explain(exc)) from exc
            logger.info("Modèle sans réglage d'effort, nouvel essai sans.")
            try:
                message = self._run(client, request)
            except anthropic.APIError as retry_exc:
                raise ProofreadError(self._explain(retry_exc)) from retry_exc
        except anthropic.APIError as exc:
            raise ProofreadError(self._explain(exc)) from exc

        if getattr(message, "stop_reason", None) == "refusal":
            raise ProofreadError(
                "Le modèle a refusé de traiter ce passage. Utilisez la relecture "
                "simple pour ce fichier, ou basculez sur le back-end CLI."
            )

        text, web_searches, sources = self._extract(message)
        parsed = self._parse_schema(text, schema) if schema else None
        return BackendResult(
            text=text, web_searches=web_searches, sources=sources, parsed=parsed
        )

    def _run(self, client, request: dict):
        """Un appel, en streaming — et une reprise si le modèle a fait une pause
        pour un tour d'outil serveur (recherche web) et attend la suite."""
        messages = list(request["messages"])
        current = dict(request)
        while True:
            with client.messages.stream(**current) as stream:
                message = stream.get_final_message()
            if getattr(message, "stop_reason", None) != "pause_turn":
                return message
            messages.append({"role": "assistant", "content": message.content})
            current = {**current, "messages": messages}

    @staticmethod
    def _extract(message) -> tuple[str, int, list[str]]:
        text_parts: list[str] = []
        web_searches = 0
        sources: list[str] = []
        for block in message.content:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "server_tool_use" and getattr(block, "name", "") == "web_search":
                web_searches += 1
            elif block_type == "web_search_tool_result":
                content = getattr(block, "content", None)
                if isinstance(content, list):
                    for result in content:
                        url = getattr(result, "url", None)
                        if url:
                            sources.append(url)
        return "".join(text_parts), web_searches, sources

    @staticmethod
    def _parse_schema(text: str, schema: dict) -> dict | list | None:
        if schema.get("type") == "array":
            parsed = parse_json_array(text)
            return parsed or None
        parsed = parse_json_object(text)
        return parsed or None

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
