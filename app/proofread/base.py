"""Types partagés par les deux modes de relecture."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


class ProofreadError(RuntimeError):
    """Erreur pendant la relecture."""


@dataclass
class TextPair:
    """Un passage, dans ses deux versions : avant et après relecture.

    C'est la matière première de la vérification — comparer ce qui a été dit
    à ce qui a été écrit suppose de savoir quel bout de texte correspond à
    quel autre.
    """

    start: float
    end: float
    raw: str
    clean: str
    block_id: str | None = None
    source_segment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProofreadResult:
    """Sortie d'une relecture."""

    text: str
    title: str = ""
    summary: list[str] = field(default_factory=list)
    mode: str = "basic"
    pairs: list[TextPair] = field(default_factory=list)
    # Blocs où un candidat de relecture a été rejeté par
    # ``proofread.validation.validate_proofread_candidate`` et remplacé par
    # une version sûre (texte brut nettoyé mécaniquement). Vide pour les
    # moteurs qui ne produisent pas de candidat à valider (Claude, basic).
    rejections: list[dict] = field(default_factory=list)
    # Observabilité complète, bloc par bloc (accepté ET rejeté) : modèle
    # exact utilisé (fallback compris), compteurs de mots, statut de
    # validation. Sert à répondre après coup à « pourquoi ce bloc précis
    # est-il revenu au brut ? ». Vide pour les moteurs sans notion de
    # candidat à valider (Claude, basic).
    block_log: list[dict] = field(default_factory=list)

    def as_markdown(self) -> str:
        """Document final : titre, résumé, puis le texte relu."""
        parts: list[str] = []
        if self.title:
            parts.append(f"# {self.title}")
        if self.summary:
            parts.append(
                "## En bref\n\n" + "\n".join(f"- {point}" for point in self.summary)
            )
        parts.append(self.text)
        return "\n\n".join(parts).strip() + "\n"
