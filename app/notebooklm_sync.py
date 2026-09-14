"""Synchronisation d'un Doc maître Google Drive, pour NotebookLM.

NotebookLM sait se resynchroniser tout seul sur une source Google
Docs/Sheets/Slides posée dans Drive. Plutôt que d'ajouter un fichier par
cours dans NotebookLM (et de devoir les y maintenir à la main), ce module
compile tous les cours relus (``data/cours/*.md``, écrits par le pipeline —
voir ``pipeline._export_course_markdown``) dans **un seul** Google Doc,
créé une fois puis réécrit intégralement à chaque appel : NotebookLM n'a
plus qu'une source unique, toujours à jour, ajoutée une seule fois.

Les bibliothèques Google (``google-api-python-client``,
``google-auth-oauthlib``) sont importées à l'intérieur des fonctions, pas au
niveau du module : ce sont des dépendances facultatives (voir
requirements-app.txt), absentes de requirements-dev.txt, et
``build_master_markdown`` doit pouvoir être testée et importée sans elles.
``sync_master_doc`` est une étape non bloquante du pipeline : elle ne lève
jamais, elle journalise et renvoie ``False``.

Ligne de commande :

    python -m app.notebooklm_sync --init       # OAuth, crée le Doc une fois
    python -m app.notebooklm_sync --rebuild    # data/cours/ depuis la base
    python -m app.notebooklm_sync --sync       # reconstruit et pousse
    python -m app.notebooklm_sync --dry-run    # imprime le Markdown, hors ligne
"""
from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import unicodedata
from pathlib import Path

from . import config as config_module

logger = logging.getLogger(__name__)

# Scope minimal : l'application ne voit que les fichiers Drive qu'elle a
# elle-même créés (ou explicitement partagés avec elle), jamais le reste du
# Drive de l'utilisateur.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

MASTER_DOC_NAME = "Cours transcrits — compilation NotebookLM"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class NotebookLMSyncError(RuntimeError):
    """Échec de la synchronisation Drive — jamais laissé remonter au pipeline."""


# --------------------------------------------------------------- markdown


def _slugify_anchor(title: str) -> str:
    """Ancre façon GitHub : minuscules, accents dépliés, espaces en tirets."""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9\s-]", "", ascii_only.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "cours"


def _course_title(text: str, fallback: str) -> str:
    match = _TITLE_RE.search(text)
    return match.group(1) if match else fallback


def _strip_leading_title(text: str) -> str:
    """Retire le premier titre ``# ...`` du corps : la section porte déjà ``## titre``."""
    return _TITLE_RE.sub("", text, count=1).strip()


def build_master_markdown(courses_dir: Path) -> str:
    """Compile tous les ``.md`` de ``courses_dir`` en un seul document.

    Un sommaire en tête (liens vers chaque section), puis une section par
    cours (``## titre``, contenu), séparées par ``---``. Aucun appel réseau ;
    un dossier vide ou absent donne un document avec un sommaire vide plutôt
    que de lever une exception — appelée aussi bien depuis le pipeline que
    depuis ``--dry-run``, sans jamais casser l'un ou l'autre.
    """
    courses_dir = Path(courses_dir)
    files = sorted(courses_dir.glob("*.md")) if courses_dir.is_dir() else []

    entries: list[tuple[str, str, str]] = []  # (ancre, titre, corps)
    used_anchors: dict[str, int] = {}
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cours illisible, ignoré (%s) : %s", path.name, exc)
            continue
        title = _course_title(raw, fallback=path.stem)
        body = _strip_leading_title(raw)

        anchor = _slugify_anchor(title)
        if anchor in used_anchors:
            used_anchors[anchor] += 1
            anchor = f"{anchor}-{used_anchors[anchor]}"
        else:
            used_anchors[anchor] = 1

        entries.append((anchor, title, body))

    lines: list[str] = ["# Cours transcrits", ""]
    lines.append(
        "_Compilation générée automatiquement à partir des cours relus — "
        "ne pas modifier ce document directement, il est remplacé à chaque "
        "synchronisation._"
    )
    lines.append("")
    lines.append("## Sommaire")
    lines.append("")
    if entries:
        for anchor, title, _ in entries:
            lines.append(f"- [{title}](#{anchor})")
    else:
        lines.append("_Aucun cours relu pour l'instant._")
    lines.append("")

    for anchor, title, body in entries:
        lines.append("---")
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")

    return "\n".join(lines).strip() + "\n"


# ----------------------------------------------------------- authentification


def _credentials_paths(settings) -> tuple[Path, Path]:
    def _resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else Path.cwd() / path

    return _resolve(settings.notebooklm_credentials_path), _resolve(
        settings.notebooklm_token_path
    )


def load_credentials(settings=None, *, interactive: bool = False):
    """Charge (et rafraîchit si besoin) les identifiants OAuth Drive.

    Flux « application de bureau » classique : ``token.json`` est réutilisé
    et rafraîchi automatiquement tant qu'il a un ``refresh_token`` ; le
    consentement interactif (ouverture du navigateur) n'a lieu que si
    ``interactive`` est vrai — c'est-à-dire uniquement depuis
    ``python -m app.notebooklm_sync --init``, jamais depuis le pipeline.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover - dépendance facultative
        raise NotebookLMSyncError(
            "Bibliothèques Google absentes (google-api-python-client, "
            "google-auth-oauthlib) : voir requirements-app.txt."
        ) from exc

    settings = settings or config_module.load_settings()
    credentials_path, token_path = _credentials_paths(settings)

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except (ValueError, OSError) as exc:
            logger.warning("Jeton NotebookLM illisible (%s) : %s", token_path.name, exc)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token(creds, token_path)
            return creds
        except Exception as exc:  # noqa: BLE001 - toute erreur réseau/OAuth possible ici
            logger.warning("Rafraîchissement du jeton NotebookLM en échec : %s", exc)
            creds = None

    if not interactive:
        raise NotebookLMSyncError(
            "Aucun accès Drive valide. Lancez « python -m app.notebooklm_sync "
            "--init » pour vous authentifier une première fois."
        )

    if not credentials_path.exists():
        raise NotebookLMSyncError(
            f"Fichier d'identifiants OAuth introuvable : {credentials_path}. "
            "Téléchargez-le depuis Google Cloud Console (identifiants "
            "« Application de bureau »)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    _write_token(creds, token_path)
    return creds


def _write_token(creds, token_path: Path) -> None:
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = token_path.with_suffix(token_path.suffix + ".tmp")
        tmp.write_text(creds.to_json(), encoding="utf-8")
        tmp.replace(token_path)
        token_path.chmod(0o600)
    except OSError as exc:
        logger.warning("Écriture du jeton NotebookLM impossible : %s", exc)


# -------------------------------------------------------------------- drive


def _drive_service(creds):
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def create_master_doc(drive_folder_id: str, creds) -> str:
    """Crée le Doc maître (une fois) et renvoie son ``fileId``.

    À n'appeler que si aucun ``notebooklm_master_doc_id`` n'existe encore en
    configuration — republier passe toujours par ``sync_master_doc``, qui
    réutilise l'identifiant existant plutôt que d'en créer un second.
    """
    service = _drive_service(creds)
    body: dict = {"name": MASTER_DOC_NAME, "mimeType": GOOGLE_DOC_MIME}
    if drive_folder_id:
        body["parents"] = [drive_folder_id]
    created = service.files().create(body=body, fields="id").execute()
    return created["id"]


def _markdown_to_html(markdown: str) -> str:
    """Repli grossier si l'import Markdown natif échoue : Drive importe le HTML depuis longtemps."""
    import html as html_module

    parts: list[str] = ["<html><body>"]
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "---":
            parts.append("<hr>")
        elif stripped.startswith("## "):
            parts.append(f"<h2>{html_module.escape(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            parts.append(f"<h1>{html_module.escape(stripped[2:])}</h1>")
        elif stripped.startswith("- "):
            parts.append(f"<p>• {html_module.escape(stripped[2:])}</p>")
        else:
            parts.append(f"<p>{html_module.escape(stripped)}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _update_master_doc(file_id: str, markdown: str, creds) -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload

    service = _drive_service(creds)
    upload = MediaIoBaseUpload(
        io.BytesIO(markdown.encode("utf-8")), mimetype="text/markdown", resumable=False
    )
    try:
        service.files().update(
            fileId=file_id, media_body=upload, body={"mimeType": GOOGLE_DOC_MIME}
        ).execute()
        return
    except HttpError as exc:
        logger.warning(
            "Import Markdown refusé par Drive (%s) : repli en HTML simple.", exc
        )

    html_upload = MediaIoBaseUpload(
        io.BytesIO(_markdown_to_html(markdown).encode("utf-8")),
        mimetype="text/html",
        resumable=False,
    )
    service.files().update(
        fileId=file_id, media_body=html_upload, body={"mimeType": GOOGLE_DOC_MIME}
    ).execute()


def sync_master_doc(courses_dir: Path, creds=None, *, settings=None) -> bool:
    """Régénère le Doc maître et remplace intégralement son contenu sur Drive.

    Étape non bloquante : ne lève jamais, renvoie ``True``/``False``. Le
    pipeline l'appelle après chaque fin de chaîne ; un échec (réseau,
    identifiants absents, bibliothèques Google non installées) est
    journalisé et n'interrompt rien d'autre.
    """
    settings = settings or config_module.load_settings()

    if not settings.notebooklm_sync_enabled:
        return False

    if not settings.notebooklm_master_doc_id:
        logger.warning(
            "Synchronisation NotebookLM activée mais aucun Doc maître : "
            "lancez « python -m app.notebooklm_sync --init »."
        )
        return False

    try:
        if creds is None:
            creds = load_credentials(settings, interactive=False)
        markdown = build_master_markdown(courses_dir)
        _update_master_doc(settings.notebooklm_master_doc_id, markdown, creds)
        logger.info("Doc maître NotebookLM synchronisé (%s).", settings.notebooklm_master_doc_id)
        return True
    except NotebookLMSyncError as exc:
        logger.warning("Synchronisation NotebookLM en échec : %s", exc)
        return False
    except ImportError as exc:  # pragma: no cover - dépendance facultative
        logger.warning("Synchronisation NotebookLM en échec (dépendance absente) : %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001 - pare-feu : aucune erreur Drive ne doit remonter
        logger.warning("Synchronisation NotebookLM en échec (%s) : %s", type(exc).__name__, exc)
        return False


# -------------------------------------------------------------------------- CLI


def _rebuild_courses_dir() -> int:
    """Régénère data/cours/ depuis tous les travaux déjà relus en base.

    Nécessaire au premier usage : un cours transcrit avant que cette
    fonctionnalité existe n'a aucun ``.md`` sur disque, et le Doc maître
    serait vide ou partiel sans ce rattrapage.
    """
    from . import db
    from . import pipeline as pipeline_module

    config_module.ensure_dirs()
    db.init_db()
    count = 0
    for row in db.iter_all(columns=("id", "status")):
        if row.get("status") in {"done", "checked", "published"}:
            if pipeline_module._export_course_markdown(row["id"]) is not None:
                count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.notebooklm_sync")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Authentification OAuth (ouvre le navigateur) et création du Doc maître si besoin.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Régénère data/cours/ depuis les cours déjà relus en base, sans appel réseau.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Reconstruit le Markdown maître et le pousse sur Drive tout de suite.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Imprime le Markdown maître sur stdout, sans aucun appel réseau.",
    )
    args = parser.parse_args(argv)

    if not any((args.init, args.rebuild, args.sync, args.dry_run)):
        parser.print_help()
        return 1

    settings = config_module.load_settings()

    if args.rebuild:
        count = _rebuild_courses_dir()
        print(f"{count} cours exporté(s) dans {config_module.COURSES_DIR}.")

    if args.dry_run:
        print(build_master_markdown(config_module.COURSES_DIR))

    if args.init:
        try:
            creds = load_credentials(settings, interactive=True)
        except NotebookLMSyncError as exc:
            print(f"Échec de l'authentification : {exc}", file=sys.stderr)
            return 1

        if settings.notebooklm_master_doc_id:
            print(f"Doc maître déjà configuré : {settings.notebooklm_master_doc_id}")
        else:
            try:
                file_id = create_master_doc(settings.notebooklm_drive_folder_id, creds)
            except Exception as exc:  # noqa: BLE001
                print(f"Échec de la création du Doc : {exc}", file=sys.stderr)
                return 1
            settings = config_module.save_settings({"notebooklm_master_doc_id": file_id})
            print(f"Doc maître créé : {file_id}")
            print(f"URL : https://docs.google.com/document/d/{file_id}/edit")
            print(
                "Ajoutez NOTEBOOKLM_MASTER_DOC_ID (ou le réglage équivalent) à votre "
                "configuration, puis NOTEBOOKLM_SYNC_ENABLED=true pour l'activer."
            )

    if args.sync:
        settings = config_module.load_settings(refresh=True)
        # Une invocation explicite en ligne de commande vaut consentement :
        # elle n'a pas à attendre que NOTEBOOKLM_SYNC_ENABLED soit activé,
        # ce réglage ne gouvernant que la synchronisation automatique depuis
        # le pipeline. La configuration sur disque n'est pas modifiée.
        from dataclasses import replace

        ok = sync_master_doc(
            config_module.COURSES_DIR, settings=replace(settings, notebooklm_sync_enabled=True)
        )
        if not ok:
            print(
                "Synchronisation en échec — voir le journal ci-dessus "
                "(activation, Doc maître, ou identifiants manquants).",
                file=sys.stderr,
            )
            return 1
        print("Doc maître synchronisé.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
