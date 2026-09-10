"""Chaîne de traitement : extraction → transcription → relecture.

Un seul thread de travail dépile la file : les moteurs Whisper saturent déjà
la machine, en lancer deux en parallèle ne ferait que les ralentir tous les
deux. Les fichiers déposés pendant un traitement attendent leur tour, ce qui
donne la file d'attente demandée pour un usage quotidien.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path

from . import config, db, media
from .engines import get_engine
from .engines.base import TranscriptionError
from .proofread import ProofreadError, ProofreadResult, basic_proofread
from .proofread.basic import split_paragraphs
from .proofread.claude import ClaudeProofreader
from .proofread.chunking import segments_to_text

logger = logging.getLogger(__name__)

# Poids de chaque étape dans la barre de progression globale.
EXTRACTION_SHARE = 0.12
TRANSCRIPTION_SHARE = 0.73
PROOFREAD_SHARE = 0.15

# État en direct, hors base : la barre de progression bouge plusieurs fois par
# seconde, il serait absurde d'écrire dans SQLite à chaque fois.
_live: dict[str, dict] = {}
_cancelled: set[str] = set()
_lock = threading.Lock()

_queue: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
DB_WRITE_INTERVAL = 2.0


# --------------------------------------------------------------------- file


def start_worker() -> None:
    """Démarre le thread de traitement (idempotent)."""
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="transcription", daemon=True)
        _worker.start()


def enqueue(job_id: str) -> None:
    _queue.put(job_id)
    start_worker()


def cancel(job_id: str) -> None:
    with _lock:
        _cancelled.add(job_id)


def is_cancelled(job_id: str) -> bool:
    with _lock:
        return job_id in _cancelled


def live_state(job_id: str) -> dict | None:
    with _lock:
        state = _live.get(job_id)
        return dict(state) if state else None


def _loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            process(job_id)
        except Exception:  # ne jamais laisser mourir le worker
            logger.exception("Échec inattendu du travail %s", job_id)
        finally:
            _queue.task_done()


# ---------------------------------------------------------------- traitement


class _Progress:
    """Publie l'avancement, en direct en mémoire et par à-coups en base."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.last_write = 0.0
        self.stage = ""

    def __call__(self, fraction: float, stage: str) -> None:
        fraction = max(0.0, min(1.0, fraction))
        with _lock:
            _live[self.job_id] = {
                "progress": fraction,
                "stage": stage,
                "status": "running",
            }
        now = time.monotonic()
        if stage != self.stage or now - self.last_write >= DB_WRITE_INTERVAL:
            self.stage = stage
            self.last_write = now
            db.update_job(self.job_id, progress=fraction, stage=stage)

    def scaled(self, base: float, share: float, label: str):
        """Sous-progression d'une étape, ramenée à l'échelle globale."""

        def report(fraction: float, stage: str = label) -> None:
            self(base + fraction * share, stage)

        return report


def process(job_id: str) -> None:
    job = db.get_job(job_id, with_content=False)
    if job is None:
        logger.warning("Travail %s introuvable", job_id)
        return
    if job["status"] in {"done", "canceled"}:
        return

    progress = _Progress(job_id)
    db.update_job(job_id, status="running", stage="Démarrage…", progress=0.0, error=None)

    workdir = config.MEDIA_DIR / job_id
    source = Path(job["media_path"])

    try:
        if is_cancelled(job_id):
            raise TranscriptionError("Travail annulé.")
        if not source.exists():
            raise TranscriptionError(
                f"Le fichier déposé est introuvable ({source.name})."
            )

        # 1. Extraction audio -------------------------------------------------
        progress(0.0, "Analyse du fichier…")
        duration = media.probe_duration(source)
        if duration:
            db.update_job(job_id, duration=duration)

        wav_path = workdir / "audio.wav"
        media.extract_wav(
            source,
            wav_path,
            duration=duration,
            on_progress=lambda f: progress(
                f * EXTRACTION_SHARE, "Extraction de la piste audio…"
            ),
            should_cancel=lambda: is_cancelled(job_id),
        )
        if not duration:
            duration = media.wav_duration(wav_path)
            db.update_job(job_id, duration=duration)
        db.update_job(job_id, wav_path=str(wav_path))

        # 2. Transcription ----------------------------------------------------
        engine = get_engine(job["engine"])
        segments = []
        report = progress.scaled(
            EXTRACTION_SHARE, TRANSCRIPTION_SHARE, "Transcription…"
        )
        for segment in engine.transcribe(
            wav_path,
            model=job["model"],
            language=job["language"],
            duration=duration,
            workdir=workdir,
            on_progress=report,
            should_cancel=lambda: is_cancelled(job_id),
        ):
            segments.append(segment.to_dict())

        if not segments:
            raise TranscriptionError(
                "Aucune parole n'a été détectée dans ce fichier. Vérifiez que "
                "la piste audio n'est pas muette (le WAV extrait est "
                "téléchargeable pour contrôle)."
            )

        raw_text = segments_to_text(segments)
        db.update_job(job_id, segments=segments, raw_text=raw_text)

        # 3. Relecture --------------------------------------------------------
        base = EXTRACTION_SHARE + TRANSCRIPTION_SHARE
        result = _proofread(
            job,
            segments,
            on_progress=progress.scaled(base, PROOFREAD_SHARE, "Relecture…"),
            should_cancel=lambda: is_cancelled(job_id),
        )

        db.mark_finished(
            job_id,
            status="done",
            stage="Terminé",
            progress=1.0,
            clean_text=result.text,
            title=result.title,
            summary=json.dumps(result.summary, ensure_ascii=False),
            proofread=result.mode,
        )
        if not config.load_settings().keep_media:
            source.unlink(missing_ok=True)

    except (TranscriptionError, ProofreadError, media.MediaError) as exc:
        status = "canceled" if is_cancelled(job_id) else "error"
        db.mark_finished(
            job_id,
            status=status,
            stage="Annulé" if status == "canceled" else "Erreur",
            error=str(exc),
        )
        logger.info("Travail %s : %s (%s)", job_id, exc, status)
    except Exception as exc:  # pragma: no cover - garde-fou
        logger.exception("Travail %s en échec", job_id)
        db.mark_finished(job_id, status="error", stage="Erreur", error=str(exc))
    finally:
        with _lock:
            _live.pop(job_id, None)
            _cancelled.discard(job_id)


def _proofread(job: dict, segments: list[dict], *, on_progress, should_cancel):
    """Applique le mode de relecture demandé, avec repli en cas d'échec."""
    mode = job.get("proofread") or "none"

    if mode == "none":
        # « Aucune relecture » veut dire aucune : on se contente de regrouper
        # les segments en paragraphes, sans toucher aux mots prononcés.
        on_progress(1.0, "Sans relecture.")
        return ProofreadResult(
            text="\n\n".join(split_paragraphs(segments)), mode="none"
        )

    if mode == "claude":
        proofreader = ClaudeProofreader()
        available, detail = proofreader.is_available()
        if not available:
            logger.info("Relecture Claude indisponible (%s) : repli mécanique.", detail)
        else:
            try:
                return proofreader.proofread(
                    segments,
                    structure=bool(job.get("structure", True)),
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                )
            except ProofreadError as exc:
                if should_cancel():
                    raise
                logger.warning("Relecture Claude en échec (%s) : repli mécanique.", exc)

    on_progress(0.5, "Relecture mécanique…")
    result = basic_proofread(segments)
    on_progress(1.0, "Relecture terminée.")
    return result
