"""Types partagés par les deux modes de relecture."""
from __future__ import annotations

from dataclasses import dataclass, field


class ProofreadError(RuntimeError):
    """Erreur pendant la relecture."""


@dataclass
class ProofreadResult:
    """Sortie d'une relecture."""

    text: str
    title: str = ""
    summary: list[str] = field(default_factory=list)
    mode: str = "basic"

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
