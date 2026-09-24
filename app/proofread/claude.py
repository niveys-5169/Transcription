"""Relecture par Claude : la sortie brute de Whisper devient un texte lisible."""
from __future__ import annotations

import logging

from ..config import Settings, load_settings
from ..lexicon import glossary_block
from ..obsidian import index as vault_index
from . import prompts
from .backends import get_backend
from .base import ProofreadError, ProofreadResult, TextPair
from .asr_quality import has_repeated_tokens
from .basic import clean_line, split_paragraph_spans
from .grounding import grounding_hints
from .chunking import TextChunk, tail
from .parallel import run_in_parallel
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


def _block_id(paragraph) -> str:
    return f"block-{paragraph.first_segment_index}-{paragraph.last_segment_index}"


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
        total = len(chunks)
        by_index: dict[int, TextPair] = {}
        todo: list[TextChunk] = []
        for chunk in chunks:
            paragraph = paragraphs[chunk.index]
            cached = saved.get(_block_id(paragraph))
            if cached and cached.raw == chunk.text:
                by_index[chunk.index] = cached
            else:
                todo.append(chunk)

        def work(chunk: TextChunk) -> TextPair:
            paragraph = paragraphs[chunk.index]
            # Contexte tiré du brut précédent, jamais de sa version relue :
            # les blocs deviennent indépendants (donc parallélisables) et une
            # dérive du bloc N ne contamine pas le prompt du bloc N+1 — même
            # règle que la relecture NIM.
            context = tail(paragraphs[chunk.index - 1].text, CONTEXT_CHARS) if chunk.index > 0 else ""
            text = self._proofread_chunk(chunk, total, context)
            return TextPair(
                start=chunk.start, end=chunk.end, raw=chunk.text, clean=text,
                block_id=_block_id(paragraph),
                source_segment_ids=[f"segment-{i}" for i in range(paragraph.first_segment_index, paragraph.last_segment_index + 1)],
            )

        def done(position: int, pair: TextPair) -> None:
            by_index[todo[position].index] = pair
            if on_progress:
                on_progress(len(by_index) / total, f"Relecture : {len(by_index)}/{total} blocs…")
            if on_checkpoint:
                on_checkpoint([by_index[i] for i in sorted(by_index)])

        if on_progress:
            on_progress(len(by_index) / total, f"Relecture : {len(by_index)}/{total} blocs…")
        run_in_parallel(
            todo, work,
            workers=self.settings.proofread_workers,
            should_cancel=should_cancel,
            on_done=done,
        )

        pairs = [by_index[i] for i in range(total)]
        body = "\n\n".join(pair.clean for pair in pairs if pair.clean).strip()
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

        if "\ufffd" in text:
            raise ProofreadError(
                f"Le bloc {chunk.index + 1} relu contient le caractère de "
                "remplacement Unicode (U+FFFD) : relecture interrompue, la "
                "transcription précédente est conservée."
            )
        # Garde-fou : une boucle de répétition absente du brut signifie que le
        # modèle a dégénéré ; le nettoyage mécanique, lui, n'invente rien.
        if has_repeated_tokens(text) and not has_repeated_tokens(chunk.text):
            logger.warning(
                "Bloc %s relu en boucle de répétition : repli sur le nettoyage mécanique.",
                chunk.index + 1,
            )
            return clean_line(chunk.text)
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
            # Titre, résumé et intertitres ne touchent pas au texte relu : le
            # couple rapide suffit, et évite un long appel en fin de relecture.
            fast=True,
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
