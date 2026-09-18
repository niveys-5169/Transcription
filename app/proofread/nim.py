"""Relecture de secours via NVIDIA NIM (API OpenAI-compatible)."""
from __future__ import annotations

import json
import logging
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import Settings, load_settings
from ..lexicon import glossary_block
from ..obsidian import index as vault_index
from . import prompts
from .base import ProofreadError, ProofreadResult, TextPair
from .basic import clean_line, split_paragraph_spans
from .chunking import TextChunk, tail
from .claude import CONTEXT_CHARS, MIN_LENGTH_RATIO, MAX_TOKENS_RELECTURE, MAX_TOKENS_STRUCTURE, STRUCTURE_INPUT_LIMIT
from .structure import insert_headings, insert_wikilinks, parse_json_object

logger = logging.getLogger(__name__)

# Le catalogue est téléchargé une seule fois par démarrage de l'application.
# Cela évite de refaire une requête réseau à chaque rafraîchissement de l'UI.
_MODELS_CACHE_LOCK = threading.Lock()
_MODELS_CACHE: dict[tuple[str, str], tuple[list[str], str]] = {}


class NimProofreader:
    """Même contrat que ClaudeProofreader, limité à la relecture/structure.

    NIM ne remplace pas Claude pour le fact-check car cette étape nécessite
    l'outil de recherche web côté Anthropic.
    """

    name = "nim"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()

    def is_available(self) -> tuple[bool, str]:
        if not self.settings.nim_fallback_enabled:
            return False, "Le repli NVIDIA NIM n'est pas activé."
        if not self.settings.nim_api_key:
            return False, "Clé API NVIDIA NIM à renseigner dans les réglages."
        if not self.settings.nim_base_url.startswith(("https://", "http://")):
            return False, "URL NVIDIA NIM invalide."
        return True, f"Repli prêt avec {self.settings.nim_model}."

    @staticmethod
    def _endpoint(base_url: str) -> str:
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        return url

    @classmethod
    def _models_endpoint(cls, base_url: str) -> str:
        """Déduit ``/v1/models`` de l'URL OpenAI-compatible configurée."""
        endpoint = cls._endpoint(base_url)
        suffix = "/chat/completions"
        return endpoint[:-len(suffix)] + "/models" if endpoint.endswith(suffix) else endpoint + "/models"

    def list_models(self) -> tuple[list[str], str]:
        """Liste les modèles exposés par ce serveur NIM, sans générer de texte."""
        if not self.settings.nim_api_key:
            return [], "Clé API NVIDIA NIM à renseigner pour charger les modèles."
        try:
            request = Request(
                self._models_endpoint(self.settings.nim_base_url),
                headers={"Authorization": f"Bearer {self.settings.nim_api_key}"},
                method="GET",
            )
            with urlopen(request, timeout=min(self.settings.nim_timeout, 15)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            entries = payload.get("data", []) if isinstance(payload, dict) else []
            models = sorted({
                str(entry.get("id")).strip()
                for entry in entries if isinstance(entry, dict) and entry.get("id")
            }, key=str.casefold)
            if not models:
                return [], "NVIDIA NIM n'a renvoyé aucun modèle de texte."
            return models, f"{len(models)} modèle(s) NIM chargé(s) au démarrage."
        except HTTPError as exc:
            return [], f"Impossible de charger les modèles NIM ({exc.code})."
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Catalogue NVIDIA NIM indisponible (%s: %s).", type(exc).__name__, exc)
            return [], "Impossible de charger les modèles NIM ; le modèle enregistré reste utilisable."

    def startup_models(self) -> tuple[list[str], str]:
        """Retourne le catalogue rafraîchi une fois par démarrage/configuration."""
        fingerprint = (self.settings.nim_base_url, self.settings.nim_api_key)
        with _MODELS_CACHE_LOCK:
            cached = _MODELS_CACHE.get(fingerprint)
        if cached is not None:
            return cached
        result = self.list_models()
        with _MODELS_CACHE_LOCK:
            return _MODELS_CACHE.setdefault(fingerprint, result)

    def _models_to_try(self) -> list[str]:
        """Modèle principal puis deux secours, sans jamais essayer un doublon."""
        return list(dict.fromkeys(
            model.strip() for model in (
                self.settings.nim_model,
                self.settings.nim_fallback_model_1,
                self.settings.nim_fallback_model_2,
            ) if model.strip()
        ))

    def complete(self, *, system: str, user: str, max_tokens: int,
                 minimum_length: int = 0) -> str:
        """Appelle jusqu'à trois modèles NIM, dans l'ordre configuré."""
        last_exc: Exception | None = None
        models = self._models_to_try()
        if not models:
            raise ProofreadError("Aucun modèle NVIDIA NIM n'est configuré.")

        for model_index, model in enumerate(models, start=1):
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.1,
                "max_tokens": min(max_tokens, 16384),
                "stream": False,
            }).encode("utf-8")
            request = Request(
                self._endpoint(self.settings.nim_base_url), data=payload,
                headers={"Authorization": f"Bearer {self.settings.nim_api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.settings.nim_timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                text = str(data["choices"][0]["message"]["content"]).strip()
                if len(text) < minimum_length:
                    raise ValueError("réponse trop courte")
                return text
            except HTTPError as exc:
                # Ne jamais inclure le corps de réponse : certains proxys le
                # réinjectent dans les logs et une clé ne doit jamais y transiter.
                last_exc = exc
                if exc.code in (401, 403):
                    raise ProofreadError(f"NVIDIA NIM a refusé la clé ({exc.code}).") from exc
            except (URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_exc = exc
            logger.warning(
                "Modèle NIM %s en échec (%d/%d — %s) ; essai du suivant.",
                model, model_index, len(models), type(last_exc).__name__,
            )
        raise ProofreadError("Les modèles NVIDIA NIM configurés sont indisponibles ou inutilisables.") from last_exc

    def proofread(self, segments, *, structure=True, on_progress=None, should_cancel=None,
                  completed_pairs=None, on_checkpoint=None) -> ProofreadResult:
        available, detail = self.is_available()
        if not available:
            raise ProofreadError(detail)
        paragraphs = split_paragraph_spans(segments, max_chars=self.settings.proofread_chunk_chars)
        chunks = [TextChunk(i, p.start, p.end, p.text) for i, p in enumerate(paragraphs)]
        if not chunks:
            return ProofreadResult(text="", mode="nim")
        saved = {pair.block_id: pair for pair in (completed_pairs or [])}
        cleaned, pairs = [], []
        for chunk in chunks:
            if should_cancel and should_cancel():
                raise ProofreadError("Relecture annulée.")
            if on_progress:
                on_progress(chunk.index / len(chunks), f"Relecture NVIDIA NIM {chunk.index + 1}/{len(chunks)}…")
            paragraph = paragraphs[chunk.index]
            block_id = f"block-{paragraph.first_segment_index}-{paragraph.last_segment_index}"
            cached = saved.get(block_id)
            if cached and cached.raw == chunk.text:
                pairs.append(cached)
                cleaned.append(cached.clean)
                continue
            context = tail(cleaned[-1], CONTEXT_CHARS) if cleaned else ""
            header = prompts.RELECTURE_CONTEXT.format(context=context) if context else ""
            system = prompts.RELECTURE_SYSTEM
            glossary = glossary_block(self.settings) if self.settings.lexicon_enabled else ""
            if glossary:
                system = f"{system}\n\n{glossary}"
            text = self.complete(
                system=system,
                user=prompts.RELECTURE_USER.format(context=header, index=chunk.index + 1, total=len(chunks), body=chunk.text),
                max_tokens=MAX_TOKENS_RELECTURE,
                minimum_length=int(len(chunk.text) * MIN_LENGTH_RATIO),
            )
            if len(text) < len(chunk.text) * MIN_LENGTH_RATIO:
                logger.warning("NIM a trop raccourci le bloc %s ; repli mécanique.", chunk.index + 1)
                text = clean_line(chunk.text)
            cleaned.append(text)
            pairs.append(TextPair(start=chunk.start, end=chunk.end, raw=chunk.text, clean=text,
                                  block_id=block_id,
                                  source_segment_ids=[f"segment-{i}" for i in range(paragraph.first_segment_index, paragraph.last_segment_index + 1)]))
            if on_checkpoint:
                on_checkpoint(pairs)
        result = ProofreadResult(text="\n\n".join(part for part in cleaned if part).strip(), mode="nim", pairs=pairs)
        if structure and result.text:
            try:
                body = result.text
                if len(body) > STRUCTURE_INPUT_LIMIT:
                    half = STRUCTURE_INPUT_LIMIT // 2
                    body = f"{body[:half]}\n\n[…]\n\n{body[-half:]}"
                notes = vault_index.search(self.settings, limit=80) if self.settings.obsidian_vault_path else []
                titles = {str(note.get("title") or "").strip() for note in notes}
                data = parse_json_object(self.complete(system=prompts.STRUCTURE_SYSTEM, user=prompts.STRUCTURE_USER.format(body=body, vault_notes="\n".join(f"- {title}" for title in sorted(titles)) or "(aucune note)"), max_tokens=MAX_TOKENS_STRUCTURE))
                if data:
                    result.title = str(data.get("title") or "").strip()[:120]
                    if isinstance(data.get("summary"), list):
                        result.summary = [str(point).strip() for point in data["summary"] if str(point).strip()][:8]
                    if isinstance(data.get("sections"), list):
                        result.text = insert_headings(result.text, [item for item in data["sections"] if isinstance(item, dict)])
                    if isinstance(data.get("wikilinks"), list):
                        result.text = insert_wikilinks(result.text, data["wikilinks"], titles)
            except ProofreadError as exc:
                logger.warning("Sommaire NIM non généré : %s", exc)
        if on_progress:
            on_progress(1.0, "Relecture NVIDIA NIM terminée.")
        return result
