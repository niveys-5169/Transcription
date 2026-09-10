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
    status        TEXT NOT NULL,
    stage         TEXT,
    progress      REAL DEFAULT 0,
    title         TEXT,
    summary       TEXT,
    raw_text      TEXT,
    clean_text    TEXT,
    segments      TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at DESC);
"""

# Colonnes lourdes, exclues des listes (une transcription d'une heure fait
# plusieurs centaines de kilo-octets).
HEAVY_COLUMNS = ("raw_text", "clean_text", "segments")

# Les chemins de fichiers sont inclus : le worker et plusieurs routes en ont
# besoin sans vouloir charger la transcription entière. Ils sont retirés des
# réponses HTTP par ``server._decorate`` — l'arborescence du disque de
# l'utilisateur n'a rien à faire dans le navigateur.
LIST_COLUMNS = (
    "id, filename, media_path, wav_path, size_bytes, duration, engine, model, "
    "language, proofread, structure, status, stage, progress, title, summary, "
    "error, created_at, updated_at, finished_at"
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


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    if "segments" in data:
        data["segments"] = json.loads(data["segments"]) if data["segments"] else []
    if "structure" in data:
        data["structure"] = bool(data["structure"])
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
) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, filename, media_path, size_bytes, engine, model,
                              language, proofread, structure, status, stage,
                              progress, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'En attente', 0, ?, ?)
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
                now,
                now,
            ),
        )
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    if "segments" in fields and not isinstance(fields["segments"], str):
        fields["segments"] = json.dumps(fields["segments"], ensure_ascii=False)
    if "structure" in fields:
        fields["structure"] = int(bool(fields["structure"]))
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


def pending_job_ids() -> list[str]:
    """Travaux à (re)mettre en file au démarrage du serveur."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN ('queued', 'running') "
            "ORDER BY created_at ASC"
        ).fetchall()
    return [row["id"] for row in rows]


def reset_interrupted() -> None:
    """Un travail « running » au démarrage vient d'un arrêt du serveur."""
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
