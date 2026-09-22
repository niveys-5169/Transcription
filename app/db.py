"""Persistance SQLite des travaux de transcription.

Une connexion est ouverte par opération : le serveur web (async) et le worker
(thread séparé) écrivent tous les deux, et SQLite gère très bien ce cas tant
qu'on ne partage pas une connexion entre threads.
"""
from __future__ import annotations

import json
import difflib
import math
import re
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
    review_blocks TEXT,
    review_checkpoint TEXT,
    review_version INTEGER DEFAULT 0,
    manual_review_status TEXT DEFAULT 'not_started',
    verification  TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at DESC);
CREATE TABLE IF NOT EXISTS annotations (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    block_id   TEXT NOT NULL,
    type       TEXT NOT NULL,
    color      TEXT,
    content    TEXT,
    status     TEXT,
    range_start INTEGER,
    range_end   INTEGER,
    review_version INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_annotations_job ON annotations (job_id, block_id);
CREATE INDEX IF NOT EXISTS idx_annotations_status ON annotations (job_id, status);
CREATE TABLE IF NOT EXISTS review_versions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    reason TEXT NOT NULL,
    blocks TEXT NOT NULL,
    clean_text TEXT NOT NULL,
    annotations TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_review_versions_job ON review_versions (job_id, version DESC);
CREATE TABLE IF NOT EXISTS factcheck_cache (
    key            TEXT PRIMARY KEY,
    claim_type     TEXT,
    citation       TEXT,
    verdict        TEXT,
    forme_correcte TEXT,
    explication    TEXT,
    sources        TEXT,
    confiance      TEXT,
    created_at     TEXT NOT NULL
);
"""

# Colonnes ajoutées après coup : appliquées à une base existante au démarrage.
MIGRATIONS = {
    "verify": "INTEGER DEFAULT 1",
    "chain": "INTEGER DEFAULT 1",
    "task": "TEXT",
    "verification": "TEXT",
    # Étapes 3 (fact-check) et 4 (publication Obsidian).
    "factcheck": "INTEGER DEFAULT 1",
    "publish": "INTEGER DEFAULT 1",
    "factcheck_report": "TEXT",
    "entities": "TEXT",
    "obsidian_path": "TEXT",
    "obsidian_verbatim_path": "TEXT",
    "review_blocks": "TEXT",
    # Point de reprise durable de la relecture IA : chaque bloc confirmé y
    # est écrit avant de demander le suivant au fournisseur.
    "review_checkpoint": "TEXT",
    "review_version": "INTEGER DEFAULT 0",
    # Relecture humaine optionnelle, indépendante de la relecture IA.
    "manual_review_status": "TEXT DEFAULT 'not_started'",
    "obsidian_published_at": "TEXT",
    "notebooklm_status": "TEXT DEFAULT 'non_configure'",
    "notebooklm_synced_at": "TEXT",
    "notebooklm_error": "TEXT",
    "notebooklm_doc_id": "TEXT",
    "revision": "TEXT",
    "knowledge": "TEXT",
    # Détail du moteur de relecture réellement utilisé bloc par bloc — voir
    # proofread/nim.py. Distinct de la colonne ``proofread`` (le mode
    # demandé/obtenu, "nim"/"claude"/"basic"/"none") : ce champ garde la
    # trace des modèles NIM effectivement appelés (fallback compris) et des
    # blocs dont le candidat a été rejeté par le validateur local.
    "review_engine_detail": "TEXT",
    # Contrôle qualité ASR (app/proofread/asr_quality.py) : signalement pur,
    # ne modifie jamais segments/raw_text.
    "asr_quality": "TEXT",
}

ANNOTATION_MIGRATIONS = {
    "range_start": "INTEGER",
    "range_end": "INTEGER",
    "review_version": "INTEGER DEFAULT 0",
}

# Colonnes lourdes, exclues des listes (une transcription d'une heure fait
# plusieurs centaines de kilo-octets).
HEAVY_COLUMNS = (
    "raw_text",
    "clean_text",
    "segments",
    "review_blocks",
    "verification",
    "factcheck_report",
    "entities",
    "revision",
    "knowledge",
    "review_engine_detail",
    "asr_quality",
)

# Statuts d'un travail. Chaque étape est un état stable et exploitable, pas
# une étape intermédiaire : le texte brut est déjà là dès « transcribed », le
# texte relu dès « done » — chacune des étapes suivantes (vérification
# externe, publication) peut être lancée plus tard, relancée, ou jamais.
STATUSES = (
    "queued",
    "running",
    "transcribed",
    "done",
    "checked",
    "published",
    "error",
    "canceled",
)

# Les chemins de fichiers sont inclus : le worker et plusieurs routes en ont
# besoin sans vouloir charger la transcription entière. Le chemin média est
# retiré des réponses HTTP par ``server._decorate`` — l'arborescence du
# disque de l'utilisateur n'a rien à faire dans le navigateur.
# ``obsidian_path``, lui, est conservé : la page en a besoin pour proposer
# un lien « Ouvrir dans Obsidian ».
LIST_COLUMNS = (
    "id, filename, media_path, wav_path, size_bytes, duration, engine, model, "
    "language, proofread, structure, verify, chain, factcheck, publish, manual_review_status, review_version, task, "
    "status, stage, progress, title, summary, error, obsidian_path, obsidian_verbatim_path, obsidian_published_at, "
    "notebooklm_status, notebooklm_synced_at, notebooklm_error, notebooklm_doc_id, "
    "created_at, updated_at, finished_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now() -> str:
    """Horodatage UTC commun aux statuts de publication."""
    return _now()


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
        annotations = {
            row["name"] for row in conn.execute("PRAGMA table_info(annotations)").fetchall()
        }
        for colonne, definition in ANNOTATION_MIGRATIONS.items():
            if colonne not in annotations:
                conn.execute(f"ALTER TABLE annotations ADD COLUMN {colonne} {definition}")


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for colonne in (
    "segments", "review_blocks", "review_checkpoint", "verification", "factcheck_report", "entities", "revision", "knowledge",
    ):
        if colonne in data:
            try:
                data[colonne] = json.loads(data[colonne]) if data[colonne] else None
            except (json.JSONDecodeError, TypeError):
                data[colonne] = None
    # segments/verification restent des listes/objets par défaut, pour ne
    # rien casser côté appelants existants.
    if data.get("segments") is None and "segments" in data:
        data["segments"] = []
    if data.get("review_blocks") is None and "review_blocks" in data:
        data["review_blocks"] = []
    if data.get("review_checkpoint") is None and "review_checkpoint" in data:
        data["review_checkpoint"] = {}
    if data.get("verification") is None and "verification" in data:
        data["verification"] = []
    if data.get("entities") is None and "entities" in data:
        data["entities"] = []
    for colonne in ("structure", "verify", "chain", "factcheck", "publish"):
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
    factcheck: bool = True,
    publish: bool = True,
) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, filename, media_path, size_bytes, engine, model,
                              language, proofread, structure, verify, chain,
                              factcheck, publish,
                              status, stage, progress, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'En attente', 0, ?, ?)
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
                int(factcheck),
                int(publish),
                now,
                now,
            ),
        )
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    for colonne in (
        "segments", "review_blocks", "review_checkpoint", "verification", "factcheck_report", "entities",
    ):
        if colonne in fields and not isinstance(fields[colonne], (str, type(None))):
            fields[colonne] = json.dumps(fields[colonne], ensure_ascii=False)
    for colonne in ("structure", "verify", "chain", "factcheck", "publish"):
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
        conn.execute("DELETE FROM annotations WHERE job_id = ?", (job_id,))
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
        # La synchro NotebookLM tourne sans faire passer le travail par
        # « running » (la publication Obsidian reste acquise pendant la
        # synchro) : pending_tasks() ne la reprend donc jamais après un
        # redémarrage. Sans ce correctif, notebooklm_status reste bloqué à
        # « en_cours » pour toujours et le bouton reste grisé (il ne se
        # rallume que sur un statut différent de « en_cours »).
        conn.execute(
            "UPDATE jobs SET notebooklm_status = 'erreur', "
            "notebooklm_error = 'Synchronisation interrompue par un redémarrage du serveur.', "
            "updated_at = ? WHERE notebooklm_status = 'en_cours'",
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


def ensure_review_blocks(job_id: str) -> list[dict]:
    """Retourne les blocs éditables, créés à la demande depuis les segments.

    La transcription brute reste intacte : les blocs sont une copie séparée
    qui peut ensuite être corrigée par l'utilisateur.
    """
    job = get_job(job_id)
    if job is None:
        return []
    blocks = job.get("review_blocks") or []
    if blocks:
        # Les travaux créés avant les tours de parole avaient déjà leurs
        # ``review_blocks`` matérialisés, un par segment. On les modernise à
        # l'ouverture seulement tant qu'ils sont intacts et non annotés : une
        # correction humaine ou un lien d'annotation ne peut alors disparaître.
        if _legacy_blocks_can_be_regrouped(job_id, blocks, job.get("segments") or []):
            by_source = {block["source_segment_ids"][0]: block for block in blocks}
            segments = [
                {**segment,
                 "speaker": by_source[f"segment-{index}"].get("speaker"),
                 "role": by_source[f"segment-{index}"].get("role")}
                for index, segment in enumerate(job.get("segments") or [], start=1)
            ]
            blocks = review_blocks_from_segments(segments)
            update_job(job_id, review_blocks=blocks, clean_text=clean_text_from_blocks(blocks))
        return blocks
    segments = job.get("segments") or []
    if not segments:
        return []
    blocks = review_blocks_from_segments(segments)
    update_job(job_id, review_blocks=blocks, clean_text=clean_text_from_blocks(blocks))
    return blocks


def review_blocks_from_segments(segments: list[dict]) -> list[dict]:
    """Construit des tours de parole éditables sans toucher aux segments bruts.

    Whisper (et la diarisation) découpent l'audio en petits fragments pour
    obtenir des horodatages précis. Ces fragments ne sont pas une unité de
    lecture : quand un même locuteur parle à la suite, ils deviennent un seul
    bloc éditable. Chaque fragment reste toutefois référencé, pour le bouton
    ``Brut``, l'écoute et une éventuelle scission mot à mot.
    """
    blocks: list[dict] = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text") or "").strip()
        speaker = segment.get("speaker")
        block = {
            "id": f"segment-{index}",
            "start": float(segment.get("start") or 0),
            "end": float(segment.get("end") or 0),
            "text": text,
            "raw_text": text,
            "source_segment_ids": [f"segment-{index}"],
            "confidence": segment.get("confidence"),
            # La diarisation n'est pas devinée : Whisper ne fournit pas une
            # identité fiable. Ces champs permettent à la personne qui écoute
            # de distinguer sans ambiguïté professeur, élève et intervenants.
            "speaker": speaker,
            "role": segment.get("role"),
        }
        # Les travaux existants n'ont pas de mots : ne pas leur ajouter une
        # clé vide afin de garder les réponses historiques identiques.
        if segment.get("words") is not None:
            block["words"] = segment["words"]
        # Ne jamais fusionner des fragments sans locuteur : cela masquerait
        # précisément le changement que la diarisation n'aurait pas repéré.
        # Le bouton « Scinder » couvre le cas inverse, rare, où elle l'a raté.
        previous = blocks[-1] if blocks else None
        if speaker and previous and previous.get("speaker") == speaker:
            separator = " " if previous.get("text") and text else ""
            previous["id"] = f"block-{previous['source_segment_ids'][0].removeprefix('segment-')}-{index}"
            previous["end"] = block["end"]
            previous["text"] = f"{previous.get('text') or ''}{separator}{text}".strip()
            previous["raw_text"] = f"{previous.get('raw_text') or ''}{separator}{text}".strip()
            previous["source_segment_ids"].append(f"segment-{index}")
            if block.get("words") is not None:
                previous["words"] = list(previous.get("words") or []) + list(block["words"] or [])
            confidences = [value for value in (previous.get("confidence"), block.get("confidence")) if value is not None]
            previous["confidence"] = min(confidences) if confidences else None
        else:
            blocks.append(block)
    return blocks


def _legacy_blocks_can_be_regrouped(job_id: str, blocks: list[dict], segments: list[dict]) -> bool:
    """Détecte les anciens blocs 1:1 vierges qui peuvent devenir des tours."""
    if len(blocks) != len(segments) or not blocks or list_annotations(job_id):
        return False
    for index, block in enumerate(blocks, start=1):
        if block.get("id") != f"segment-{index}" or block.get("source_segment_ids") != [f"segment-{index}"]:
            return False
        if str(block.get("text") or "").strip() != str(block.get("raw_text") or "").strip():
            return False
        # Si le repère a été ajouté manuellement dans l'ancien éditeur, le
        # segment brut ne le connaît pas : ne pas transformer cette édition.
        if block.get("speaker") != segments[index - 1].get("speaker"):
            return False
    return any(
        blocks[index - 1].get("speaker") and blocks[index - 1].get("speaker") == block.get("speaker")
        for index, block in enumerate(blocks[1:], start=1)
    )


def review_blocks_from_pairs(pairs, segments: list[dict]) -> list[dict]:
    """Matérialise les paragraphes relus en gardant leur preuve Whisper."""
    blocks = []
    for number, pair in enumerate(pairs, start=1):
        source_ids = list(getattr(pair, "source_segment_ids", None) or [])
        if not source_ids:
            # Compatibilité des anciennes paires : intervalle semi-ouvert.
            source_ids = [
                f"segment-{index}" for index, segment in enumerate(segments, start=1)
                if float(segment.get("end") or 0) > pair.start
                and float(segment.get("start") or 0) < pair.end
            ]
            if not source_ids and segments:
                nearest = min(range(len(segments)), key=lambda i: abs(float(segments[i].get("start") or 0) - pair.start))
                source_ids = [f"segment-{nearest + 1}"]
        block_id = getattr(pair, "block_id", None) or f"block-{number}"
        source_segments = [segments[int(source_id.removeprefix("segment-")) - 1]
                           for source_id in source_ids
                           if source_id.removeprefix("segment-").isdigit()
                           and 0 < int(source_id.removeprefix("segment-")) <= len(segments)]
        speakers = {segment.get("speaker") for segment in source_segments if segment.get("speaker")}
        roles = {segment.get("role") for segment in source_segments if segment.get("role")}
        blocks.append({
            "id": block_id, "start": pair.start, "end": pair.end,
            "text": pair.clean, "raw_text": pair.raw,
            "source_segment_ids": source_ids, "confidence": None,
            "words": [word for segment in source_segments for word in (segment.get("words") or [])] or None,
            "speaker": speakers.pop() if len(speakers) == 1 else None,
            "role": roles.pop() if len(roles) == 1 else None,
        })
    return blocks


def clean_text_from_blocks(blocks: list[dict]) -> str:
    return "\n\n".join(str(block.get("text") or "").strip() for block in blocks if str(block.get("text") or "").strip())


def _retime_words(block: dict, text: str) -> list[dict] | None:
    """Préserve les mots alignés inchangés, interpole les autres dans le bloc."""
    old_words = block.get("words")
    if not isinstance(old_words, list) or not old_words:
        return None
    old_tokens = [str(word.get("text") or "").strip() for word in old_words]
    new_tokens = re.findall(r"\S+", text)
    if not new_tokens:
        return []
    start, end = float(block.get("start") or 0), float(block.get("end") or 0)
    step = (end - start) / len(new_tokens)
    words = [
        {"start": round(start + index * step, 3), "end": round(start + (index + 1) * step, 3),
         "text": token, "confidence": None}
        for index, token in enumerate(new_tokens)
    ]
    for match in difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False).get_matching_blocks():
        for offset in range(match.size):
            original = old_words[match.a + offset]
            words[match.b + offset] = {
                "start": original.get("start"), "end": original.get("end"),
                "text": new_tokens[match.b + offset], "confidence": original.get("confidence"),
            }
    return words


def archive_review_version(job_id: str, *, reason: str) -> str | None:
    job = get_job(job_id)
    if not job or not job.get("review_blocks"):
        return None
    version = int(job.get("review_version") or 0)
    snapshot_id = uuid.uuid4().hex
    annotations = list_annotations(job_id, review_version=version)
    with connect() as conn:
        conn.execute("INSERT INTO review_versions (id, job_id, version, reason, blocks, clean_text, annotations, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (snapshot_id, job_id, version, reason, json.dumps(job["review_blocks"], ensure_ascii=False),
                      job.get("clean_text") or clean_text_from_blocks(job["review_blocks"]),
                      json.dumps(annotations, ensure_ascii=False), _now()))
    return snapshot_id


def list_review_versions(job_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, version, reason, created_at FROM review_versions WHERE job_id = ? ORDER BY created_at DESC", (job_id,)).fetchall()
    return [dict(row) for row in rows]


def restore_review_version(job_id: str, snapshot_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    with connect() as conn:
        row = conn.execute("SELECT * FROM review_versions WHERE id = ? AND job_id = ?", (snapshot_id, job_id)).fetchone()
    if not row:
        return False
    archive_review_version(job_id, reason="Avant restauration")
    blocks = json.loads(row["blocks"])
    annotations = json.loads(row["annotations"])
    version = int(job.get("review_version") or 0) + 1
    update_job(job_id, review_blocks=blocks, clean_text=clean_text_from_blocks(blocks), review_version=version)
    for annotation in annotations:
        create_annotation(job_id, block_id=annotation["block_id"], kind=annotation["type"], color=annotation.get("color"),
                          content=annotation.get("content"), status=annotation.get("status"), range_start=annotation.get("range_start"),
                          range_end=annotation.get("range_end"), review_version=version)
    return True


_UNSET = object()


def update_review_block(
    job_id: str,
    block_id: str,
    text: str | None = None,
    speaker: str | None | object = _UNSET,
    role: str | None | object = _UNSET,
    start: float | object = _UNSET,
    end: float | object = _UNSET,
) -> dict | None:
    """Met à jour un bloc de révision sans jamais retoucher les segments bruts.

    Le champ ``speaker`` sert de repère pour regrouper les blocs qui
    correspondent à la même personne (ex. « Speaker 1 » répété par Whisper
    sur plusieurs passages). Renommer ce repère, ou lui attribuer un rôle,
    s'applique donc à tous les blocs qui partagent actuellement le même
    repère, pas seulement au bloc édité.
    """
    blocks = ensure_review_blocks(job_id)
    index = next((position for position, block in enumerate(blocks) if block.get("id") == block_id), -1)
    if index < 0:
        return None
    target = blocks[index]

    if start is not _UNSET or end is not _UNSET:
        new_start = float(target.get("start") or 0) if start is _UNSET else float(start)
        new_end = float(target.get("end") or 0) if end is _UNSET else float(end)
        if not math.isfinite(new_start) or not math.isfinite(new_end) or new_start < 0 or new_end <= new_start:
            raise ValueError("Les horodatages doivent être positifs et la fin postérieure au début.")
        previous = blocks[index - 1] if index else None
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if previous and new_start < float(previous.get("end") or 0):
            raise ValueError("Le début ne peut pas chevaucher le bloc précédent.")
        if following and new_end > float(following.get("start") or float("inf")):
            raise ValueError("La fin ne peut pas chevaucher le bloc suivant.")
        target["start"], target["end"] = round(new_start, 3), round(new_end, 3)
        words = target.get("words")
        if isinstance(words, list) and words:
            step = (new_end - new_start) / len(words)
            target["words"] = [{**word, "start": round(new_start + pos * step, 3), "end": round(new_start + (pos + 1) * step, 3)} for pos, word in enumerate(words)]

    if text is not None:
        target["text"] = text
        retimed = _retime_words(target, text)
        if retimed is not None:
            target["words"] = retimed

    # ``None`` signifie « non renseigné » et efface donc une attribution
    # précédente ; les anciens blocs restent compatibles.
    if speaker is not _UNSET:
        previous_speaker = target.get("speaker")
        target["speaker"] = speaker
        if previous_speaker:
            for block in blocks:
                if block is not target and block.get("speaker") == previous_speaker:
                    block["speaker"] = speaker

    if role is not _UNSET:
        target["role"] = role
        current_speaker = target.get("speaker")
        if current_speaker:
            for block in blocks:
                if block is not target and block.get("speaker") == current_speaker:
                    block["role"] = role

    update_job(job_id, review_blocks=blocks, clean_text=clean_text_from_blocks(blocks))
    return target


def replace_in_review_block(job_id: str, block_id: str, citation: str, replacement: str) -> bool:
    """Substitution exacte, limitée à un seul bloc canonique."""
    blocks = ensure_review_blocks(job_id)
    matches = [block for block in blocks if block.get("id") == block_id and str(block.get("text") or "").count(citation) == 1]
    if len(matches) != 1:
        return False
    matches[0]["text"] = str(matches[0]["text"]).replace(citation, replacement, 1)
    update_job(job_id, review_blocks=blocks, clean_text=clean_text_from_blocks(blocks))
    return True


def split_review_block(job_id: str, block_id: str, word_index: int) -> tuple[list[dict], str | None] | None:
    """Scinde un bloc entre deux mots, après archivage de l'état courant."""
    blocks = ensure_review_blocks(job_id)
    index = next((i for i, block in enumerate(blocks) if block.get("id") == block_id), -1)
    if index < 0:
        return None
    block = blocks[index]
    words = list(block.get("words") or [])
    if not 0 < word_index < len(words):
        return None
    snapshot_id = archive_review_version(job_id, reason="Avant scission de bloc")
    left_words, right_words = words[:word_index], words[word_index:]
    right_id = f"{block_id}-split-{uuid.uuid4().hex[:8]}"
    left = {**block, "words": left_words, "text": " ".join(str(w.get("text") or "") for w in left_words).strip(),
            "end": float(left_words[-1].get("end") or block.get("end") or 0)}
    right = {**block, "id": right_id, "words": right_words, "text": " ".join(str(w.get("text") or "") for w in right_words).strip(),
             "start": float(right_words[0].get("start") or block.get("start") or 0), "raw_text": " ".join(str(w.get("text") or "") for w in right_words).strip()}
    blocks[index:index + 1] = [left, right]
    version = int((get_job(job_id) or {}).get("review_version") or 0) + 1
    _move_split_annotations(job_id, block_id, right_id, len(left["text"]), version)
    update_job(job_id, review_blocks=blocks, clean_text=clean_text_from_blocks(blocks), review_version=version)
    return [left, right], snapshot_id


def merge_review_block(job_id: str, block_id: str) -> tuple[dict, str | None] | None:
    """Fusionne un bloc avec son suivant, après archivage de l'état courant."""
    blocks = ensure_review_blocks(job_id)
    index = next((i for i, block in enumerate(blocks) if block.get("id") == block_id), -1)
    if index < 0 or index + 1 >= len(blocks):
        return None
    snapshot_id = archive_review_version(job_id, reason="Avant fusion de blocs")
    first, second = blocks[index], blocks[index + 1]
    separator = " " if first.get("text") and second.get("text") else ""
    merged = {**first, "end": float(second.get("end") or first.get("end") or 0),
              "text": f"{first.get('text') or ''}{separator}{second.get('text') or ''}".strip(),
              "words": list(first.get("words") or []) + list(second.get("words") or []),
              "source_segment_ids": list(first.get("source_segment_ids") or []) + list(second.get("source_segment_ids") or [])}
    blocks[index:index + 2] = [merged]
    version = int((get_job(job_id) or {}).get("review_version") or 0) + 1
    _move_merged_annotations(job_id, second.get("id"), first.get("id"), len(str(first.get("text") or "")) + len(separator), version)
    update_job(job_id, review_blocks=blocks, clean_text=clean_text_from_blocks(blocks), review_version=version)
    return merged, snapshot_id


def _move_split_annotations(job_id: str, left_id: str, right_id: str, pivot: int, review_version: int) -> None:
    with connect() as conn:
        rows = conn.execute("SELECT id, range_start, range_end FROM annotations WHERE job_id = ? AND block_id = ?", (job_id, left_id)).fetchall()
        for row in rows:
            start, end = row["range_start"], row["range_end"]
            if start is not None and start >= pivot:
                conn.execute("UPDATE annotations SET block_id = ?, range_start = ?, range_end = ?, review_version = ?, updated_at = ? WHERE id = ?", (right_id, start - pivot, (end - pivot) if end is not None else None, review_version, _now(), row["id"]))
            elif end is not None and end > pivot:
                conn.execute("UPDATE annotations SET range_end = ?, review_version = ?, updated_at = ? WHERE id = ?", (pivot, review_version, _now(), row["id"]))
            else:
                conn.execute("UPDATE annotations SET review_version = ?, updated_at = ? WHERE id = ?", (review_version, _now(), row["id"]))


def _move_merged_annotations(job_id: str, from_id: str | None, to_id: str | None, offset: int, review_version: int) -> None:
    if not from_id or not to_id:
        return
    with connect() as conn:
        rows = conn.execute("SELECT id, range_start, range_end FROM annotations WHERE job_id = ? AND block_id = ?", (job_id, from_id)).fetchall()
        for row in rows:
            start = row["range_start"] + offset if row["range_start"] is not None else None
            end = row["range_end"] + offset if row["range_end"] is not None else None
            conn.execute("UPDATE annotations SET block_id = ?, range_start = ?, range_end = ?, review_version = ?, updated_at = ? WHERE id = ?", (to_id, start, end, review_version, _now(), row["id"]))
        conn.execute("UPDATE annotations SET review_version = ?, updated_at = ? WHERE job_id = ? AND block_id = ?", (review_version, _now(), job_id, to_id))


def list_annotations(job_id: str, *, review_version: int | None = None) -> list[dict]:
    if review_version is None:
        job = get_job(job_id, with_content=False)
        review_version = int((job or {}).get("review_version") or 0)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE job_id = ? AND review_version = ? ORDER BY created_at ASC", (job_id, review_version)
        ).fetchall()
    return [dict(row) for row in rows]


def create_annotation(
    job_id: str,
    *,
    block_id: str,
    kind: str,
    color: str | None = None,
    content: str | None = None,
    status: str | None = None,
    range_start: int | None = None,
    range_end: int | None = None,
    review_version: int | None = None,
) -> dict:
    annotation_id = uuid.uuid4().hex
    now = _now()
    if review_version is None:
        job = get_job(job_id, with_content=False)
        review_version = int((job or {}).get("review_version") or 0)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO annotations
                (id, job_id, block_id, type, color, content, status, range_start, range_end, review_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (annotation_id, job_id, block_id, kind, color, content, status, range_start, range_end, review_version, now, now),
        )
    return {
        "id": annotation_id, "job_id": job_id, "block_id": block_id,
        "type": kind, "color": color, "content": content, "status": status,
        "range_start": range_start, "range_end": range_end, "review_version": review_version,
        "created_at": now, "updated_at": now,
    }


def update_annotation(annotation_id: str, **fields: Any) -> dict | None:
    allowed = {"type", "color", "content", "status", "range_start", "range_end"}
    fields = {key: value for key, value in fields.items() if key in allowed}
    if not fields:
        return get_annotation(annotation_id)
    fields["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE annotations SET {assignments} WHERE id = ?",
            (*fields.values(), annotation_id),
        )
    return get_annotation(annotation_id)


def get_annotation(annotation_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
    return dict(row) if row else None


def delete_annotation(annotation_id: str) -> bool:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
    return cursor.rowcount > 0


def pending_annotation_counts(job_ids: Iterable[str]) -> dict[str, int]:
    """Nombre d'annotations « à vérifier » par travail, pour les cartes de la
    bibliothèque — une seule requête groupée plutôt qu'un aller-retour par
    travail affiché."""
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT job_id, COUNT(*) AS n FROM annotations
            WHERE job_id IN ({placeholders}) AND status = 'a_verifier'
            GROUP BY job_id
            """,
            ids,
        ).fetchall()
    return {row["job_id"]: row["n"] for row in rows}


def search_transcripts(query: str, limit: int = 50) -> list[dict]:
    """Recherche avec extrait et horodatage quand un bloc éditable correspond."""
    needle = query.strip().casefold()
    if not needle:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, filename, title, raw_text, clean_text, segments, review_blocks
            FROM jobs ORDER BY created_at DESC
            """
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        job = _row_to_dict(row)
        matches: list[tuple[str, float | None, float | None]] = []
        blocks = job.get("review_blocks") or review_blocks_from_segments(
            job.get("segments") or []
        )
        for block in blocks:
            text = str(block.get("text") or "")
            if needle in text.casefold():
                matches.append((text, block.get("start"), block.get("end")))
        if not matches:
            for field in ("title", "filename", "clean_text", "raw_text"):
                text = str(job.get(field) or "")
                if needle in text.casefold():
                    matches.append((text, None, None))
                    break
        if matches:
            text, start, end = matches[0]
            at = text.casefold().find(needle)
            excerpt = text[max(0, at - 70):at + len(query) + 110].strip()
            results.append({
                "job_id": job["id"],
                "title": job.get("title") or job["filename"],
                "match_text": excerpt,
                "start": start,
                "end": end,
                "match_count": len(matches),
            })
        if len(results) >= limit:
            break
    return results


def mark_finished(job_id: str, **fields: Any) -> None:
    update_job(job_id, finished_at=_now(), **fields)


def iter_all(columns: Iterable[str] = ("id",)) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM jobs").fetchall()
    return [dict(row) for row in rows]


# ------------------------------------------------------ cache des verdicts

# Un verdict « introuvable » ou « ambigu » n'est pas une réponse mais un
# échec de recherche : le mémoriser aussi longtemps qu'un verdict établi
# (``factcheck_cache_days``, potentiellement 90 jours) figerait une absence
# de résultat qu'une prochaine recherche aurait pu lever. Durée de vie
# courte, indépendante de ``max_age_days``.
SHORT_LIVED_VERDICTS = ("introuvable", "ambigu")
SHORT_CACHE_DAYS = 7


def factcheck_cache_get(key: str, *, max_age_days: int) -> dict | None:
    """Verdict mémorisé pour ``key``, ou ``None`` s'il est absent ou périmé.

    Ne décide jamais s'il *faut* mettre en cache ou réutiliser un verdict —
    ce jugement appartient à l'appelant (``factcheck.py``) ; ce module
    n'obéit qu'à la règle de péremption ci-dessus.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM factcheck_cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    entry = dict(row)
    max_age = SHORT_CACHE_DAYS if entry.get("verdict") in SHORT_LIVED_VERDICTS else max_age_days
    try:
        created_at = datetime.fromisoformat(entry["created_at"])
    except (TypeError, ValueError):
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400
    if age_days > max_age:
        return None
    # Une entrée illisible est traitée comme absente : mieux vaut refaire la
    # recherche que faire échouer le fact-check sur une ligne corrompue.
    try:
        entry["sources"] = json.loads(entry["sources"]) if entry.get("sources") else []
    except (json.JSONDecodeError, TypeError):
        return None
    return entry


def factcheck_cache_put(key: str, entry: dict) -> None:
    """Mémorise (ou remplace) le verdict de ``key``."""
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO factcheck_cache
                (key, claim_type, citation, verdict, forme_correcte, explication, sources, confiance, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                entry.get("claim_type"),
                entry.get("citation"),
                entry.get("verdict"),
                entry.get("forme_correcte"),
                entry.get("explication"),
                json.dumps(entry.get("sources") or [], ensure_ascii=False),
                entry.get("confiance"),
                _now(),
            ),
        )
