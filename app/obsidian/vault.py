"""Écriture dans le coffre Obsidian : résolution de chemin, garde-fous, écriture atomique."""
from __future__ import annotations

from pathlib import Path


class ObsidianError(RuntimeError):
    """Le coffre est absent, non inscriptible, ou la cible sort du coffre."""


def resolve(vault_path: str, relative: str) -> Path:
    """Résout ``relative`` à l'intérieur du coffre ; refuse tout ce qui en sort.

    ``relative`` vient de gabarits de réglages et de titres de travaux : rien
    ne garantit qu'il ne contienne pas de ``..`` — la vérification porte sur
    le chemin résolu, pas sur son apparence.
    """
    if not vault_path:
        raise ObsidianError("Aucun coffre Obsidian configuré dans les réglages.")
    vault = Path(vault_path).expanduser().resolve()
    if not vault.exists() or not vault.is_dir():
        raise ObsidianError(f"Le coffre Obsidian est introuvable : {vault}")

    target = (vault / relative).resolve()
    try:
        target.relative_to(vault)
    except ValueError:
        raise ObsidianError(f"Chemin hors du coffre refusé : « {relative} ».") from None
    return target


def write_atomic(path: Path, content: str) -> None:
    """Écrit ``content`` dans ``path``, en créant les dossiers, de façon atomique."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise ObsidianError(f"Impossible d'écrire dans le coffre ({path.name}) : {exc}") from exc


def read(path: Path) -> str:
    """Contenu d'une note existante, chaîne vide si elle n'existe pas encore."""
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return ""
