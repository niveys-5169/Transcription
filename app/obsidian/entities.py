"""Fiches d'entités, MOC et glossaire — le second brain du domaine choisi.

Une note d'entité existante n'est jamais modifiée, seulement liée : rien de
ce que l'utilisateur y a écrit à la main ne peut être écrasé, les backlinks
d'Obsidian font le reste. Le MOC et le glossaire, à l'inverse, sont
entièrement dérivés (personne n'y écrit à la main) : ils peuvent être
régénérés sans risque à chaque publication — seule la région balisée du MOC
est réécrite, le reste de la note reste à vous.
"""
from __future__ import annotations

import re

from .vault import read, resolve, write_atomic

MOC_START = "<!-- transcriptions:début -->"
MOC_END = "<!-- transcriptions:fin -->"

_SAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe(name: str) -> str:
    return _SAFE_NAME.sub("", name).strip() or "Sans titre"


def ensure_entity_notes(settings, entities: list[dict]) -> None:
    """Crée une fiche stub pour chaque entité qui n'existe pas encore."""
    if not settings.obsidian_create_entities:
        return
    for entity in entities:
        wikilink = str(entity.get("wikilink") or entity.get("nom") or "").strip()
        if not wikilink:
            continue
        relative = f"{settings.obsidian_entities_folder}/{_safe(wikilink)}.md"
        path = resolve(settings.obsidian_vault_path, relative)
        if path.exists():
            continue  # jamais écrasée : c'est le principe même du second brain
        write_atomic(path, _entity_stub(entity))


def _entity_stub(entity: dict) -> str:
    categorie = entity.get("categorie") or "autre"
    nom = entity.get("nom") or entity.get("wikilink") or ""
    lines = ["---", "type: entite", f"categorie: {categorie}", "---", "", f"# {nom}"]
    definition = str(entity.get("definition") or "").strip()
    if definition:
        lines += ["", definition]
    sources = entity.get("sources") or []
    if sources:
        lines += ["", "## Sources"]
        for source in sources:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "")
            if not url:
                continue
            titre = str(source.get("titre") or url)
            lines.append(f"- [{titre}]({url})")
    return "\n".join(lines).strip() + "\n"


def update_index(
    settings, *, title: str, wikilink: str, duration_minutes: float, incertains: int
) -> None:
    """Ajoute ou met à jour la ligne du MOC pour ce travail.

    Seule la région balisée est réécrite ; republier met la ligne à jour
    plutôt que de la dupliquer.
    """
    path = resolve(settings.obsidian_vault_path, settings.obsidian_index_note)
    content = read(path)
    if MOC_START not in content or MOC_END not in content:
        header = content.rstrip() + "\n\n" if content.strip() else "# MOC Formation\n\n"
        content = header + f"{MOC_START}\n{MOC_END}\n"

    before, rest = content.split(MOC_START, 1)
    inside, after = rest.split(MOC_END, 1)

    badge = (
        f" · ⚠️ {incertains} point{'s' if incertains > 1 else ''} à vérifier"
        if incertains
        else " · ✅"
    )
    line = f"- [[{wikilink}]] · {duration_minutes:.0f} min{badge}"
    marker = f"[[{wikilink}]]"

    lines = [l for l in inside.strip("\n").split("\n") if l.strip() and marker not in l]
    lines.append(line)

    new_content = f"{before}{MOC_START}\n" + "\n".join(lines) + f"\n{MOC_END}{after}"
    write_atomic(path, new_content)


def write_glossary(settings) -> None:
    """Régénère le glossaire du domaine à partir du lexique.

    Entièrement dérivée du lexique de l'application : rien n'y est jamais
    écrit à la main, elle peut donc être régénérée en entier sans risque.
    """
    from ..lexicon import load_lexicon

    path = resolve(settings.obsidian_vault_path, settings.obsidian_glossary_note)

    by_category: dict[str, list] = {}
    for term in load_lexicon():
        by_category.setdefault(term.categorie, []).append(term)

    lines = [
        f"# Glossaire {settings.domain_label}",
        "",
        "_Généré automatiquement depuis le lexique de l'application — les "
        "modifications faites ici seraient écrasées à la prochaine publication._",
        "",
    ]
    for categorie in sorted(by_category):
        lines.append(f"## {categorie.capitalize()}")
        lines.append("")
        for term in sorted(by_category[categorie], key=lambda t: t.terme):
            marque = "" if term.verifie else " · ⚠️ non vérifié"
            sigles = f" ({', '.join(term.sigles)})" if term.sigles else ""
            lines.append(f"### [[{term.wikilink}|{term.terme}]]{sigles}{marque}")
            if term.definition:
                lines += ["", term.definition]
            if term.reference:
                lines += ["", f"*{term.reference}*"]
            lines.append("")

    write_atomic(path, "\n".join(lines).rstrip() + "\n")
