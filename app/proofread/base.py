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
