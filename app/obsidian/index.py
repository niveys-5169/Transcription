"""Index en lecture seule des notes d'un coffre Obsidian.

L'index ne modifie jamais le coffre : il sert uniquement à reconnaître une
note déjà existante (son titre ou un alias) avant de créer une fiche d'entité.
Une copie sérialisée est gardée hors du coffre et est invalidée dès qu'un
fichier Markdown change.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .. import config
from .vault import ObsidianError, resolve

_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def normalize(value: str) -> str:
    """Clé de rapprochement tolérante à la casse et aux accents."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^\w]+", " ", plain, flags=re.UNICODE).strip()


def build(settings, *, force: bool = False) -> list[dict]:
    """Retourne les notes indexées, en réutilisant le cache si possible."""
    vault = resolve(settings.obsidian_vault_path, ".")
    files = sorted(vault.rglob("*.md"))
    signature = _signature(vault, files)
    cache_path = config.DATA_DIR / "vault_index.json"
    if not force:
        cached = _read_cache(cache_path)
        if cached and cached.get("vault") == str(vault) and cached.get("signature") == signature:
            return list(cached.get("notes") or [])

    notes = [_parse_note(vault, path) for path in files]
    notes = [note for note in notes if note]
    _write_cache(cache_path, {"vault": str(vault), "signature": signature, "notes": notes})
    return notes


def search(settings, query: str = "", *, force: bool = False, limit: int = 50) -> list[dict]:
    """Notes dont le titre, les alias ou les tags correspondent à ``query``."""
    needle = normalize(query)
    notes = build(settings, force=force)
    if not needle:
        return notes[:limit]
    return [note for note in notes if needle in normalize(" ".join(
        [note.get("title", ""), *note.get("aliases", []), *note.get("tags", [])]
    ))][:limit]


def resolve_entities(settings, entities: list[dict]) -> list[dict]:
    """Pointe les entités vers des notes existantes, alias compris.

    Le dictionnaire d'origine n'est pas muté : la publication reste
    déterministe et l'entité brute reste disponible en base.
    """
    if not entities:
        return []
    by_name: dict[str, dict] = {}
    for note in build(settings):
        for candidate in [note.get("title", ""), *note.get("aliases", [])]:
            key = normalize(candidate)
            if key and key not in by_name:
                by_name[key] = note

    resolved = []
    for entity in entities:
        copy = dict(entity)
        name = str(copy.get("wikilink") or copy.get("nom") or "")
        match = by_name.get(normalize(name))
        if match:
            copy["wikilink"] = match["title"]
            copy["vault_path"] = match["path"]
        resolved.append(copy)
    return resolved


def _signature(vault: Path, files: list[Path]) -> str:
    rows = []
    for path in files:
        try:
            rows.append(f"{path.relative_to(vault).as_posix()}:{path.stat().st_mtime_ns}")
        except OSError:
            continue
    return "|".join(rows)


def _parse_note(vault: Path, path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    frontmatter = _parse_frontmatter(text)
    heading = _TITLE.search(text)
    title = str(frontmatter.get("title") or frontmatter.get("titre") or (heading.group(1) if heading else path.stem)).strip()
    if not title:
        return None
    return {
        "title": title,
        "aliases": _as_list(frontmatter.get("aliases")),
        "tags": _as_list(frontmatter.get("tags")),
        "type": str(frontmatter.get("type") or ""),
        "path": path.relative_to(vault).as_posix(),
    }


def _parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _as_list(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [item.strip().strip('"').strip("'") for item in raw.split(",") if item.strip()]


def _read_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        # Un cache facultatif ne doit jamais empêcher une publication.
        pass
