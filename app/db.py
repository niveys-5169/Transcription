"""Persistance SQLite des travaux de transcription.

Une connexion est ouverte par opération : le serveur web (async) et le worker
(thread séparé) écrivent tous les deux, et SQLite gère très bien ce cas tant
qu'on ne partage pas une connexion entre threads.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    media_path    TEXT,
    wav_path      TEXT,
    size_bytes    INTEGER DEFAULT 0,
    duration      REAL DEFAULT 0,
    engine        TEXT,
    model         TEXT,
    language      TEXT,
    proofread     TEXT,
    structure     INTEGER DEFAULT 1,
    verify        INTEGER DEFAULT 1,
    chain         INTEGER DEFAULT 1,
    task          TEXT,
    status        TEXT NOT NULL,
    stage         TEXT,
    progress      REAL DEFAULT 0,
    title         TEXT,
    summary       TEXT,
    raw_text      TEXT,
    clean_text    TEXT,
    segments      TEXT,
    verification  TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at DESC);
"""

# Colonnes ajoutées après coup : appliquées à une base existante au démarrage.
MIGRATIONS = {
    "verify": "INTEGER DEFAULT 1",
    "chain": "INTEGER DEFAULT 1",
    "task": "TEXT",
    "verification": "TEXT",
}

# Colonnes lourdes, exclues des listes (une transcription d'une heure fait
# plusieurs centaines de kilo-octets).
HEAVY_COLUMNS = ("raw_text", "clean_text", "segments", "verification")

# Statuts d'un travail. La transcription et la relecture sont deux étapes
# distinctes : « transcribed » est un état stable et exploitable, pas une
# étape intermédiaire — le texte brut est déjà là, la relecture peut être
# lancée plus tard, relancée, ou jamais.
STATUSES = ("queued", "running", "transcribed", "done", "error", "canceled")

# Les chemins de fichiers sont inclus : le worker et plusieurs routes en ont
# besoin sans vouloir charger la transcription entière. Ils sont retirés des
# réponses HTTP par ``server._decorate`` — l'arborescence du disque de
# l'utilisateur n'a rien à faire dans le navigateur.
LIST_COLUMNS = (
    "id, filename, media_path, wav_path, size_bytes, duration, engine, model, "
    "language, proofread, structure, verify, chain, task, status, stage, "
    "progress, title, summary, error, created_at, updated_at, finished_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        existantes = {
            row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for colonne, definition in MIGRATIONS.items():
            if colonne not in existantes:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {colonne} {definition}")


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for colonne in ("segments", "verification"):
        if colonne in data:
            try:
                data[colonne] = json.loads(data[colonne]) if data[colonne] else []
            except (json.JSONDecodeError, TypeError):
                data[colonne] = []
    for colonne in ("structure", "verify", "chain"):
        if colonne in data:
            data[colonne] = bool(data[colonne])
    return data


def create_job(
    *,
    filename: str,
    media_path: str,
    size_bytes: int,
    engine: str,
    model: str,
    language: str,
    proofread: str,
    structure: bool,
    verify: bool = True,
    chain: bool = True,
) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, filename, media_path, size_bytes, engine, model,
                              language, proofread, structure, verify, chain,
                              status, stage, progress, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'En attente', 0, ?, ?)
            """,
            (
                job_id,
                filename,
                media_path,
                size_bytes,
                engine,
                model,
                language,
                proofread,
                int(structure),
                int(verify),
                int(chain),
                now,
                now,
            ),
        )
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    for colonne in ("segments", "verification"):
        if colonne in fields and not isinstance(fields[colonne], (str, type(None))):
            fields[colonne] = json.dumps(fields[colonne], ensure_ascii=False)
    for colonne in ("structure", "verify", "chain"):
        if colonne in fields:
            fields[colonne] = int(bool(fields[colonne]))
    fields["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            (*fields.values(), job_id),
        )


def get_job(job_id: str, *, with_content: bool = True) -> dict | None:
    columns = "*" if with_content else LIST_COLUMNS
    with connect() as conn:
        row = conn.execute(
            f"SELECT {columns} FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {LIST_COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def delete_job(job_id: str) -> dict | None:
    job = get_job(job_id, with_content=False)
    if job is None:
        return None
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return job


def pending_tasks() -> list[tuple[str, str]]:
    """Travaux à remettre en file au démarrage, avec l'étape à reprendre."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, task FROM jobs WHERE status IN ('queued', 'running') "
            "ORDER BY created_at ASC"
        ).fetchall()
    return [(row["id"], row["task"] or "transcription") for row in rows]


def reset_interrupted() -> None:
    """Un travail « running » au démarrage vient d'un arrêt du serveur.

    L'étape en cours (``task``) est conservée : une relecture interrompue
    reprend à la relecture, sans refaire la transcription.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'queued', progress = 0, "
            "stage = 'Repris après redémarrage', updated_at = ? "
            "WHERE status = 'running'",
            (_now(),),
        )


def search_jobs(query: str, limit: int = 50) -> list[dict]:
    """Recherche plein texte simple dans les transcriptions."""
    pattern = f"%{query}%"
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {LIST_COLUMNS} FROM jobs
            WHERE filename LIKE ? OR title LIKE ? OR raw_text LIKE ?
               OR clean_text LIKE ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def mark_finished(job_id: str, **fields: Any) -> None:
    update_job(job_id, finished_at=_now(), **fields)


def iter_all(columns: Iterable[str] = ("id",)) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM jobs").fetchall()
    return [dict(row) for row in rows]
