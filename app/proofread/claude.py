"""Relecture par Claude : la sortie brute de Whisper devient un texte lisible."""
from __future__ import annotations

import logging

from ..config import Settings, load_settings
from ..lexicon import glossary_block
from ..obsidian import index as vault_index
from . import prompts
from .backends import get_backend
from .base import ProofreadError, ProofreadResult, TextPair
from .basic import clean_line, split_paragraph_spans
from .grounding import grounding_hints
from .chunking import TextChunk, tail
from .structure import insert_headings, insert_wikilinks, parse_json_object

logger = logging.getLogger(__name__)

# En dessous de ce ratio, le modèle a résumé au lieu de relire : on garde
# alors la version mécanique du bloc plutôt qu'un texte amputé.
MIN_LENGTH_RATIO = 0.55

CONTEXT_CHARS = 400
MAX_TOKENS_RELECTURE = 32_000
MAX_TOKENS_STRUCTURE = 4_000
# Au-delà, on ne soumet que le début et la fin au sommaire : largement assez
# pour un plan, et inutile de payer le texte entier une seconde fois.
STRUCTURE_INPUT_LIMIT = 200_000


class ClaudeProofreader:
    """Relit une transcription bloc par bloc, puis en dresse le sommaire."""

    name = "claude"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.backend = get_backend(self.settings)

    def is_available(self) -> tuple[bool, str]:
        return self.backend.is_available()

    # ------------------------------------------------------------- relecture

    def proofread(
        self,
        segments,
        *,
        structure: bool = True,
        on_progress=None,
        should_cancel=None,
        completed_pairs=None,
        on_checkpoint=None,
    ) -> ProofreadResult:
        available, detail = self.is_available()
        if not available:
            raise ProofreadError(detail)

        # Les appels restent groupés par contexte, mais l'unité éditable est
        # un paragraphe stable dérivé des segments, jamais un gros chunk IA.
        paragraphs = split_paragraph_spans(segments, max_chars=self.settings.proofread_chunk_chars)
        chunks = [TextChunk(i, p.start, p.end, p.text) for i, p in enumerate(paragraphs)]
        if not chunks:
            return ProofreadResult(text="", mode="claude")

        # Un checkpoint ne vaut que pour le même bloc brut. Cela autorise une
        # reprise avec NIM après Claude (ou l'inverse), sans jamais réemployer
        # un texte qui ne correspondrait plus à la transcription source.
        saved = {pair.block_id: pair for pair in (completed_pairs or [])}
        cleaned: list[str] = []
        pairs: list[TextPair] = []

        for chunk in chunks:
            if should_cancel is not None and should_cancel():
                raise ProofreadError("Relecture annulée.")
            if on_progress:
                on_progress(
                    chunk.index / len(chunks),
                    f"Relecture du bloc {chunk.index + 1}/{len(chunks)}…",
                )

            paragraph = paragraphs[chunk.index]
            block_id = f"block-{paragraph.first_segment_index}-{paragraph.last_segment_index}"
            cached = saved.get(block_id)
            if cached and cached.raw == chunk.text:
                pairs.append(cached)
                cleaned.append(cached.clean)
                continue

            context = tail(cleaned[-1], CONTEXT_CHARS) if cleaned else ""
            text = self._proofread_chunk(chunk, len(chunks), context)
            pair = TextPair(
                start=chunk.start, end=chunk.end, raw=chunk.text, clean=text,
                block_id=block_id,
                source_segment_ids=[f"segment-{i}" for i in range(paragraph.first_segment_index, paragraph.last_segment_index + 1)],
            )
            pairs.append(pair)
            cleaned.append(text)
            if on_checkpoint:
                on_checkpoint(pairs)

        body = "\n\n".join(part for part in cleaned if part).strip()
        result = ProofreadResult(text=body, mode="claude", pairs=pairs)

        if structure and body:
            if on_progress:
                on_progress(0.9, "Rédaction du sommaire…")
            try:
                self._apply_structure(result)
            except Exception as exc:  # le sommaire est un bonus, pas un dû
                logger.warning("Sommaire non généré : %s", exc)

        if on_progress:
            on_progress(1.0, "Relecture terminée.")
        return result

    def _proofread_chunk(self, chunk, total: int, context: str) -> str:
        header = (
            prompts.RELECTURE_CONTEXT.format(context=context) if context else ""
        )
        user = prompts.RELECTURE_USER.format(
            context=header,
            index=chunk.index + 1,
            total=total,
            body=chunk.text,
        )
        system = prompts.RELECTURE_SYSTEM
        glossary = glossary_block(self.settings) if self.settings.lexicon_enabled else ""
        if glossary:
            system = f"{system}\n\n{glossary}"
        hints = grounding_hints(chunk.text, self.settings)
        if hints:
            system = f"{system}\n\n{hints}"

        text = self.backend.complete(
            system=system,
            user=user,
            max_tokens=MAX_TOKENS_RELECTURE,
        ).text.strip()

        # Garde-fou : une réponse nettement plus courte que l'entrée signifie
        # que le passage a été résumé. On préfère alors le nettoyage mécanique,
        # qui ne perd rien.
        if len(text) < len(chunk.text) * MIN_LENGTH_RATIO:
            logger.warning(
                "Bloc %s relu trop court (%s → %s caractères) : "
                "repli sur le nettoyage mécanique.",
                chunk.index + 1,
                len(chunk.text),
                len(text),
            )
            return clean_line(chunk.text)
        return text

    def _apply_structure(self, result: ProofreadResult) -> None:
        body = result.text
        if len(body) > STRUCTURE_INPUT_LIMIT:
            half = STRUCTURE_INPUT_LIMIT // 2
            body = f"{body[:half]}\n\n[…]\n\n{body[-half:]}"

        notes = vault_index.search(self.settings, limit=80) if self.settings.obsidian_vault_path else []
        titles = {str(note.get("title") or "").strip() for note in notes}
        raw = self.backend.complete(
            system=prompts.STRUCTURE_SYSTEM,
            user=prompts.STRUCTURE_USER.format(
                body=body,
                vault_notes="\n".join(f"- {title}" for title in sorted(titles)) or "(aucune note)",
            ),
            max_tokens=MAX_TOKENS_STRUCTURE,
        ).text
        data = parse_json_object(raw)
        if not data:
            return

        title = str(data.get("title") or "").strip()
        if title:
            result.title = title[:120]

        summary = data.get("summary")
        if isinstance(summary, list):
            result.summary = [
                str(point).strip() for point in summary if str(point).strip()
            ][:8]

        sections = data.get("sections")
        if isinstance(sections, list):
            result.text = insert_headings(
                result.text, [s for s in sections if isinstance(s, dict)]
            )
        if isinstance(data.get("wikilinks"), list):
            result.text = insert_wikilinks(result.text, data["wikilinks"], titles)
