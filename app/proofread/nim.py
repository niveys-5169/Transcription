"""Relecture de secours via NVIDIA NIM (API OpenAI-compatible)."""
from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import Settings, load_settings
from ..lexicon import glossary_block
from . import prompts
from .base import ProofreadError, ProofreadResult, TextPair
from .basic import clean_line, split_paragraph_spans
from .chunking import TextChunk, tail
from .claude import CONTEXT_CHARS, MIN_LENGTH_RATIO, MAX_TOKENS_RELECTURE, MAX_TOKENS_STRUCTURE, STRUCTURE_INPUT_LIMIT
from .structure import insert_headings, parse_json_object

logger = logging.getLogger(__name__)


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

    def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        payload = json.dumps({
            "model": self.settings.nim_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": min(max_tokens, 16384),
            "stream": False,
        }).encode("utf-8")
        request = Request(
            self.settings.nim_base_url,
            data=payload,
            headers={"Authorization": f"Bearer {self.settings.nim_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Ne jamais inclure le corps de réponse : certains proxys le
            # réinjectent dans les logs et une clé ne doit jamais y transiter.
            raise ProofreadError(f"NVIDIA NIM a répondu {exc.code}.") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProofreadError("Impossible de joindre NVIDIA NIM.") from exc
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ProofreadError("Réponse NVIDIA NIM inutilisable.") from exc

    def proofread(self, segments, *, structure=True, on_progress=None, should_cancel=None) -> ProofreadResult:
        available, detail = self.is_available()
        if not available:
            raise ProofreadError(detail)
        paragraphs = split_paragraph_spans(segments, max_chars=self.settings.proofread_chunk_chars)
        chunks = [TextChunk(i, p.start, p.end, p.text) for i, p in enumerate(paragraphs)]
        if not chunks:
            return ProofreadResult(text="", mode="nim")
        cleaned, pairs = [], []
        for chunk in chunks:
            if should_cancel and should_cancel():
                raise ProofreadError("Relecture annulée.")
            if on_progress:
                on_progress(chunk.index / len(chunks), f"Relecture NVIDIA NIM {chunk.index + 1}/{len(chunks)}…")
            context = tail(cleaned[-1], CONTEXT_CHARS) if cleaned else ""
            header = prompts.RELECTURE_CONTEXT.format(context=context) if context else ""
            system = prompts.RELECTURE_SYSTEM
            glossary = glossary_block(self.settings) if self.settings.lexicon_enabled else ""
            if glossary:
                system = f"{system}\n\n{glossary}"
            text = self.complete(system=system, user=prompts.RELECTURE_USER.format(context=header, index=chunk.index + 1, total=len(chunks), body=chunk.text), max_tokens=MAX_TOKENS_RELECTURE)
            if len(text) < len(chunk.text) * MIN_LENGTH_RATIO:
                logger.warning("NIM a trop raccourci le bloc %s ; repli mécanique.", chunk.index + 1)
                text = clean_line(chunk.text)
            cleaned.append(text)
            paragraph = paragraphs[chunk.index]
            pairs.append(TextPair(start=chunk.start, end=chunk.end, raw=chunk.text, clean=text,
                                  block_id=f"block-{paragraph.first_segment_index}-{paragraph.last_segment_index}",
                                  source_segment_ids=[f"segment-{i}" for i in range(paragraph.first_segment_index, paragraph.last_segment_index + 1)]))
        result = ProofreadResult(text="\n\n".join(part for part in cleaned if part).strip(), mode="nim", pairs=pairs)
        if structure and result.text:
            try:
                body = result.text
                if len(body) > STRUCTURE_INPUT_LIMIT:
                    half = STRUCTURE_INPUT_LIMIT // 2
                    body = f"{body[:half]}\n\n[…]\n\n{body[-half:]}"
                data = parse_json_object(self.complete(system=prompts.STRUCTURE_SYSTEM, user=prompts.STRUCTURE_USER.format(body=body), max_tokens=MAX_TOKENS_STRUCTURE))
                if data:
                    result.title = str(data.get("title") or "").strip()[:120]
                    if isinstance(data.get("summary"), list):
                        result.summary = [str(point).strip() for point in data["summary"] if str(point).strip()][:8]
                    if isinstance(data.get("sections"), list):
                        result.text = insert_headings(result.text, [item for item in data["sections"] if isinstance(item, dict)])
            except ProofreadError as exc:
                logger.warning("Sommaire NIM non généré : %s", exc)
        if on_progress:
            on_progress(1.0, "Relecture NVIDIA NIM terminée.")
        return result
