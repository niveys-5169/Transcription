"""Relecture par Claude : la sortie brute de Whisper devient un texte lisible."""
from __future__ import annotations

import logging

from . import prompts
from .anthropic_client import ClaudeClient
from .base import ProofreadError, ProofreadResult, TextPair
from .basic import clean_line
from .chunking import build_chunks, tail
from .structure import insert_headings, parse_json_object

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


class ClaudeProofreader(ClaudeClient):
    """Relit une transcription bloc par bloc, puis en dresse le sommaire."""

    name = "claude"

    # ------------------------------------------------------------- relecture

    def proofread(
        self,
        segments,
        *,
        structure: bool = True,
        on_progress=None,
        should_cancel=None,
    ) -> ProofreadResult:
        available, detail = self.is_available()
        if not available:
            raise ProofreadError(detail)

        chunks = build_chunks(segments, self.settings.proofread_chunk_chars)
        if not chunks:
            return ProofreadResult(text="", mode="claude")

        client = self._client()
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

            context = tail(cleaned[-1], CONTEXT_CHARS) if cleaned else ""
            text = self._proofread_chunk(client, chunk, len(chunks), context)
            cleaned.append(text)
            pairs.append(
                TextPair(
                    start=chunk.start, end=chunk.end, raw=chunk.text, clean=text
                )
            )

        body = "\n\n".join(part for part in cleaned if part).strip()
        result = ProofreadResult(text=body, mode="claude", pairs=pairs)

        if structure and body:
            if on_progress:
                on_progress(0.9, "Rédaction du sommaire…")
            try:
                self._apply_structure(client, result)
            except Exception as exc:  # le sommaire est un bonus, pas un dû
                logger.warning("Sommaire non généré : %s", exc)

        if on_progress:
            on_progress(1.0, "Relecture terminée.")
        return result

    def _proofread_chunk(self, client, chunk, total: int, context: str) -> str:
        header = (
            prompts.RELECTURE_CONTEXT.format(context=context) if context else ""
        )
        user = prompts.RELECTURE_USER.format(
            context=header,
            index=chunk.index + 1,
            total=total,
            body=chunk.text,
        )

        text = self._call(
            client,
            system=prompts.RELECTURE_SYSTEM,
            user=user,
            max_tokens=MAX_TOKENS_RELECTURE,
        ).strip()

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

    def _apply_structure(self, client, result: ProofreadResult) -> None:
        body = result.text
        if len(body) > STRUCTURE_INPUT_LIMIT:
            half = STRUCTURE_INPUT_LIMIT // 2
            body = f"{body[:half]}\n\n[…]\n\n{body[-half:]}"

        raw = self._call(
            client,
            system=prompts.STRUCTURE_SYSTEM,
            user=prompts.STRUCTURE_USER.format(body=body),
            max_tokens=MAX_TOKENS_STRUCTURE,
        )
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

