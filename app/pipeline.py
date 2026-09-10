"""Chaîne de traitement, en deux étapes indépendantes.

    1. TRANSCRIPTION   fichier → audio → texte brut + segments horodatés
    2. RELECTURE       texte brut → texte relu, structuré, vérifié

Les deux sont séparées à dessein. La transcription est un calcul : elle rend
ce qui a été dit, mot pour mot, et le moteur — qu'il tourne sur cette machine
ou sur un GPU RunPod — ne fait que cela. La relecture est un travail de
lecture : elle intervient après, sur du texte, et peut être lancée plus tard,
relancée avec d'autres réglages, ou jamais.

Un travail « transcribed » est donc un état stable et exploitable, pas une
étape intermédiaire : le texte brut, les segments et les sous-titres sont déjà
disponibles au téléchargement.

Un seul thread dépile la file : les moteurs Whisper saturent déjà la machine,
en lancer deux en parallèle ne ferait que les ralentir tous les deux.
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
from .proofread.base import TextPair
from .proofread.basic import split_paragraph_spans
from .proofread.chunking import segments_to_text
from .proofread.claude import ClaudeProofreader
from .proofread.verify import verify

logger = logging.getLogger(__name__)

TASK_TRANSCRIPTION = "transcription"
TASK_PROOFREAD = "relecture"

# Poids de chaque phase dans la barre de progression, par étape.
EXTRACTION_SHARE = 0.15
TRANSCRIPTION_SHARE = 0.85
PROOFREAD_SHARE = 0.70
VERIFICATION_SHARE = 0.30

# État en direct, hors base : la barre de progression bouge plusieurs fois par
# seconde, il serait absurde d'écrire dans SQLite à chaque fois.
_live: dict[str, dict] = {}
_cancelled: set[str] = set()
_lock = threading.Lock()

_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
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


def enqueue(job_id: str, task: str = TASK_TRANSCRIPTION) -> None:
    db.update_job(job_id, task=task)
    _queue.put((job_id, task))
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
        job_id, task = _queue.get()
        try:
            if task == TASK_PROOFREAD:
                run_proofread(job_id)
            else:
                run_transcription(job_id)
        except Exception:  # ne jamais laisser mourir le worker
            logger.exception("Échec inattendu du travail %s (%s)", job_id, task)
        finally:
            _queue.task_done()


# ---------------------------------------------------------------- avancement


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


def _fail(job_id: str, exc: Exception, stage_label: str) -> None:
    status = "canceled" if is_cancelled(job_id) else "error"
    db.mark_finished(
        job_id,
        status=status,
        stage="Annulé" if status == "canceled" else stage_label,
        task=None,
        error=str(exc),
    )
    logger.info("Travail %s : %s (%s)", job_id, exc, status)


def _release(job_id: str) -> None:
    with _lock:
        _live.pop(job_id, None)
        _cancelled.discard(job_id)


# ------------------------------------------------------ étape 1 : transcription


def run_transcription(job_id: str) -> None:
    """Fichier déposé → texte brut et segments horodatés. Rien de plus."""
    job = db.get_job(job_id, with_content=False)
    if job is None:
        logger.warning("Travail %s introuvable", job_id)
        return
    if job["status"] in {"done", "transcribed", "canceled"}:
        return

    progress = _Progress(job_id)
    db.update_job(
        job_id,
        status="running",
        task=TASK_TRANSCRIPTION,
        stage="Démarrage…",
        progress=0.0,
        error=None,
    )

    workdir = config.MEDIA_DIR / job_id
    source = Path(job["media_path"] or "")

    try:
        if is_cancelled(job_id):
            raise TranscriptionError("Travail annulé.")
        if not source.exists():
            raise TranscriptionError(
                f"Le fichier déposé est introuvable ({source.name})."
            )

        # -- Extraction audio ------------------------------------------------
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

        # -- Transcription ---------------------------------------------------
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

        db.mark_finished(
            job_id,
            status="transcribed",
            stage="Transcrit",
            progress=1.0,
            task=None,
            segments=segments,
            raw_text=segments_to_text(segments),
        )
        if not config.load_settings().keep_media:
            source.unlink(missing_ok=True)

    except (TranscriptionError, media.MediaError) as exc:
        _fail(job_id, exc, "Erreur de transcription")
        return
    except Exception as exc:  # pragma: no cover - garde-fou
        logger.exception("Travail %s en échec", job_id)
        db.mark_finished(
            job_id, status="error", stage="Erreur", task=None, error=str(exc)
        )
        return
    finally:
        _release(job_id)

    # La relecture est une étape à part : on l'enfile plutôt que de l'appeler.
    # Le travail est déjà consultable et téléchargeable à ce stade ; si
    # l'enchaînement n'est pas demandé, il en reste là et attend qu'on le
    # relance — plus tard, ou jamais.
    if job.get("chain", True):
        enqueue(job_id, TASK_PROOFREAD)


# --------------------------------------------------------- étape 2 : relecture


def run_proofread(job_id: str) -> None:
    """Texte brut → texte relu, structuré et vérifié. Ne retouche pas l'audio."""
    job = db.get_job(job_id)
    if job is None:
        logger.warning("Travail %s introuvable", job_id)
        return

    segments = job.get("segments") or []
    if not segments:
        db.update_job(
            job_id,
            status="error",
            stage="Erreur",
            task=None,
            error="Aucune transcription à relire : lancez d'abord la transcription.",
        )
        return

    progress = _Progress(job_id)
    db.update_job(
        job_id,
        status="running",
        task=TASK_PROOFREAD,
        stage="Préparation de la relecture…",
        progress=0.0,
        error=None,
    )

    try:
        if is_cancelled(job_id):
            raise ProofreadError("Relecture annulée.")

        result = _proofread(
            job,
            segments,
            on_progress=progress.scaled(0.0, PROOFREAD_SHARE, "Relecture…"),
            should_cancel=lambda: is_cancelled(job_id),
        )

        report = verify(
            result.pairs,
            use_claude=bool(job.get("verify", True)) and result.mode == "claude",
            on_progress=progress.scaled(
                PROOFREAD_SHARE, VERIFICATION_SHARE, "Vérification…"
            ),
            should_cancel=lambda: is_cancelled(job_id),
        )

        db.mark_finished(
            job_id,
            status="done",
            stage="Terminé",
            progress=1.0,
            task=None,
            clean_text=result.text,
            title=result.title,
            summary=json.dumps(result.summary, ensure_ascii=False),
            proofread=result.mode,
            verification=report.to_dict(),
        )

    except ProofreadError as exc:
        # Le texte brut reste intact : on retombe sur l'état « transcrit »
        # plutôt que de marquer tout le travail en erreur.
        status = "canceled" if is_cancelled(job_id) else "transcribed"
        db.mark_finished(
            job_id,
            status=status,
            stage="Annulé" if status == "canceled" else "Transcrit — relecture en échec",
            task=None,
            progress=1.0,
            error=str(exc),
        )
        logger.info("Relecture %s : %s (%s)", job_id, exc, status)
    except Exception as exc:  # pragma: no cover - garde-fou
        logger.exception("Relecture %s en échec", job_id)
        db.mark_finished(
            job_id,
            status="transcribed",
            stage="Transcrit — relecture en échec",
            task=None,
            error=str(exc),
        )
    finally:
        _release(job_id)


def _proofread(job: dict, segments: list[dict], *, on_progress, should_cancel):
    """Applique le mode de relecture demandé, avec repli en cas d'échec."""
    mode = job.get("proofread") or "none"

    if mode == "none":
        # « Aucune relecture » veut dire aucune : on se contente de regrouper
        # les segments en paragraphes, sans toucher aux mots prononcés.
        on_progress(1.0, "Sans relecture.")
        paragraphs = split_paragraph_spans(segments)
        return ProofreadResult(
            text="\n\n".join(p.text for p in paragraphs),
            mode="none",
            pairs=[
                TextPair(start=p.start, end=p.end, raw=p.text, clean=p.text)
                for p in paragraphs
            ],
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
                # Une annulation doit remonter ; un incident d'API, non :
                # mieux vaut une relecture mécanique qu'un travail en échec.
                if should_cancel():
                    raise
                logger.warning("Relecture Claude en échec (%s) : repli mécanique.", exc)

    on_progress(0.5, "Relecture mécanique…")
    result = basic_proofread(segments)
    on_progress(1.0, "Relecture terminée.")
    return result
