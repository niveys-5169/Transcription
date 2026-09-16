"""Rendu de la fiche Obsidian : frontmatter, encart, corps, notes de bas de page.

Le corps arrive déjà annoté par le fact-check (marqueurs ``[^vN]`` et notes
de bas de page insérés par ``factcheck.apply_verdicts``) : cette étape
entoure ce texte de frontmatter et d'un encart, elle ne le réécrit jamais.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# Caractères interdits dans un nom de fichier, sous Windows comme ailleurs.
# Volontairement permissif au-delà : accents, apostrophes, tiret cadratin
# (utilisés par le gabarit de nom par défaut) restent intacts — Obsidian les
# accepte tous.
_FORBIDDEN_IN_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

_CATEGORY_TO_FIELD = {
    "nom_propre": "personnes",
    "organisme": "organismes",
    "rapport": "references",
    "reference_juridique": "references",
    "lexique": "references",
}


def render_note(job: dict, *, settings=None) -> str:
    """La fiche complète, telle qu'elle sera écrite dans le coffre."""
    from .. import config

    settings = settings or config.load_settings()
    findings = _findings(job)
    entities = job.get("entities") or []
    ran = job.get("status") in ("checked", "published") and job.get("factcheck_report") is not None
    status = _statut(ran, findings)

    parts = [
        _frontmatter(job, entities=entities, findings=findings, status=status, settings=settings),
        _callout(status, findings),
    ]

    summary = job.get("summary") or []
    if isinstance(summary, list) and summary:
        parts.append("## En bref\n\n" + "\n".join(f"- {point}" for point in summary))

    # Les corrections humaines des blocs prévalent sur la relecture IA. Le
    # helper centralise la comparaison avec les segments bruts et ne modifie
    # jamais ces derniers.
    from ..exporters import editorial_text

    body = editorial_text(job)
    if body:
        parts.append(body)

    return "\n\n".join(part for part in parts if part).strip() + "\n"


def filename_for(job: dict, template: str) -> str:
    """Nom de fichier (sans extension), d'après le gabarit des réglages."""
    titre = job.get("title") or job.get("filename") or "Transcription"
    try:
        name = template.format(date=_date(job), titre=titre, job_id=job.get("id") or "")
    except (KeyError, IndexError):
        name = f"{_date(job)} — {titre}"
    name = _FORBIDDEN_IN_FILENAME.sub("", name).strip()
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150] or "Transcription"


def render_verbatim(job: dict, *, settings=None, fiche_name: str = "") -> str:
    """Version relue conservée telle quelle, avec tours de parole et temps."""
    from .. import config
    settings = settings or config.load_settings()
    title = job.get("title") or job.get("filename") or "Transcription"
    lines = ["---", "type: verbatim", f"titre: {_yaml_str(title)}", f"date: {_date(job)}",
             f"fiche: \"[[{fiche_name}]]\"" if fiche_name else "", "---", "", f"# {title} (verbatim)", ""]
    for block in job.get("review_blocks") or []:
        start = float(block.get("start") or 0)
        speaker = str(block.get("speaker") or "Locuteur")
        text = str(block.get("text") or "").strip()
        if text:
            hours, rem = divmod(int(start), 3600); minutes, seconds = divmod(rem, 60)
            lines.append(f"**[{hours:02d}:{minutes:02d}:{seconds:02d}] {speaker}** — {text}")
            lines.append("")
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


# ------------------------------------------------------------------ détail


def _findings(job: dict) -> list[dict]:
    verification = job.get("verification")
    if isinstance(verification, dict):
        entries = verification.get("findings")
        return entries if isinstance(entries, list) else []
    return []


def _statut(ran: bool, findings: list[dict]) -> str:
    if not ran:
        return "non_verifie"
    reels = [f for f in findings if f.get("kind") in ("fait", "source")]
    return "incertain" if reels else "verifie"


def _callout(status: str, findings: list[dict]) -> str:
    if status == "verifie":
        return "> [!success] Vérifié — tous les éléments confirmés"
    if status == "non_verifie":
        return (
            "> [!warning] Document non vérifié\n"
            "> La vérification par recherche web n'a pas été effectuée sur ce "
            "document. Ne pas le citer comme référence sans l'avoir vérifié."
        )
    reels = [f for f in findings if f.get("kind") in ("fait", "source")]
    n = len(reels)
    pluriel = n > 1
    sujet = "Ils portent" if pluriel else "Il porte"
    return (
        f"> [!warning] Document non validé — {n} point{'s' if pluriel else ''} incertain{'s' if pluriel else ''}\n"
        f"> {n} élément{'s' if pluriel else ''} n'{'ont' if pluriel else 'a'} pas pu être "
        f"confirmé{'s' if pluriel else ''} par recherche web. {sujet} un appel de note "
        "dans le texte.\n"
        "> Ne pas citer ce document comme référence sans les avoir levés."
    )


def _yaml_str(value: str) -> str:
    if value == "":
        return '""'
    if any(c in value for c in ':#{}[]&*!|>\'"%@`,') or value != value.strip():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(_yaml_str(v) for v in values) + "]" if values else "[]"


def _frontmatter(job: dict, *, entities: list[dict], findings: list[dict], status: str, settings) -> str:
    grouped: dict[str, list[str]] = {"personnes": [], "organismes": [], "references": []}
    entity_aliases: list[str] = []
    for entity in entities:
        field = _CATEGORY_TO_FIELD.get(entity.get("categorie"))
        wikilink = entity.get("wikilink") or entity.get("nom")
        if field and wikilink and wikilink not in grouped[field]:
            grouped[field].append(wikilink)
        original = str(entity.get("nom") or "").strip()
        if original and wikilink and original != wikilink:
            entity_aliases.append(f"{original} → {wikilink}")

    tags = [t.strip() for t in (settings.obsidian_tags or "").split(",") if t.strip()]
    if status != "verifie" and "à-vérifier" not in tags:
        tags.append("à-vérifier")

    reels = [f for f in findings if f.get("kind") in ("fait", "source")]
    duree = job.get("duration") or 0

    lines = [
        "---",
        "type: transcription",
        f"domaine: {_yaml_str(settings.domain_label)}",
        f"statut_verification: {status}",
        f"points_incertains: {len(reels)}",
        f"titre: {_yaml_str(job.get('title') or job.get('filename') or 'Transcription')}",
        f"date: {_date(job)}",
        f"source_fichier: {_yaml_str(job.get('filename') or '')}",
        f"verbatim: \"[[{_note_name(job.get('obsidian_verbatim_path'))}]]\"" if job.get("obsidian_verbatim_path") else "",
        f"duree_minutes: {round(duree / 60, 1)}",
        f"moteur: {_yaml_str(job.get('engine') or '')}",
        f"modele: {_yaml_str(job.get('model') or '')}",
        f"relecture: {_yaml_str(job.get('proofread') or '')}",
        f"personnes: {_yaml_list([f'[[{p}]]' for p in grouped['personnes']])}",
        f"organismes: {_yaml_list([f'[[{o}]]' for o in grouped['organismes']])}",
        f"references: {_yaml_list([f'[[{r}]]' for r in grouped['references']])}",
        f"aliases_entites: {_yaml_list(entity_aliases)}",
        f"tags: {_yaml_list(tags)}",
        f"job_id: {job.get('id') or ''}",
        "---",
    ]
    return "\n".join(line for line in lines if line)


def _note_name(relative_path: str | None) -> str:
    """Nom Obsidian d'un chemin relatif, sans extension Markdown."""
    name = str(relative_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-3] if name.lower().endswith(".md") else name


def _date(job: dict) -> str:
    raw = str(job.get("created_at") or "")
    return raw[:10] if raw else datetime.now(timezone.utc).date().isoformat()
