"""Chaîne de traitement, en quatre étapes indépendantes.

    1. TRANSCRIPTION   fichier → audio → texte brut + segments horodatés
    2. RELECTURE       texte brut → texte relu, structuré, vérifié (fidélité)
    3. VÉRIFICATION    noms propres, rapports, statistiques → recherche web
    4. PUBLICATION     fiche écrite dans le coffre Obsidian

Chacune est séparée à dessein. La transcription est un calcul : elle rend ce
qui a été dit, mot pour mot, et le moteur — qu'il tourne sur cette machine ou
sur un GPU RunPod — ne fait que cela. Les trois étapes suivantes sont des
travaux de lecture et de recherche : elles interviennent après, sur du texte,
et peuvent être lancées plus tard, relancées avec d'autres réglages, ou
jamais.

Chaque état intermédiaire (« transcribed », « done », « checked ») est donc
stable et exploitable, pas une étape de passage : le texte brut, les segments
et les sous-titres sont déjà disponibles au téléchargement dès
« transcribed » ; le texte relu, dès « done ».

Un seul thread dépile la file : les moteurs Whisper saturent déjà la machine,
en lancer deux en parallèle ne ferait que les ralentir tous les deux — et un
appel à Claude, CLI ou API, n'a aucune raison d'être plus pressé qu'un autre.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path

from . import config, db, exporters, lexicon, media, obsidian
from .engines import get_engine
from .engines.base import TranscriptionError
from .proofread import ProofreadError, ProofreadResult, basic_proofread
from .proofread import factcheck as factcheck_module
from .proofread.base import TextPair
from .proofread.basic import split_paragraph_spans
from .proofread.chunking import segments_to_text
from .proofread.claude import ClaudeProofreader
from .proofread.nim import NimProofreader
from .proofread.verify import SEVERITIES, verify

logger = logging.getLogger(__name__)

TASK_TRANSCRIPTION = "transcription"
TASK_PROOFREAD = "relecture"
TASK_FACTCHECK = "verification_web"
TASK_PUBLISH = "publication"
TASK_NOTEBOOKLM = "synchronisation_notebooklm"

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


_RUNNERS = {}  # peuplé plus bas, une fois les fonctions run_* définies


def _loop() -> None:
    while True:
        job_id, task = _queue.get()
        try:
            _RUNNERS.get(task, run_transcription)(job_id)
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


# --------------------------------------------------- compilation NotebookLM


def _export_course_markdown(job_id: str) -> Path | None:
    """Écrit (ou réécrit) le .md finalisé du cours dans data/cours/.

    Relit systématiquement le travail en base plutôt que de réutiliser un
    dict déjà en main dans l'appelant : mark_finished() vient tout juste
    d'écrire de nouvelles valeurs (texte relu, vérifié…) que ce dict périmé
    ne porte pas encore. Sans ce rechargement, un appel après l'étape 3
    exporterait le texte d'avant la vérification web.

    N'importe pas depuis le module notebooklm_sync : celui-ci compile
    ensuite ces fichiers, mais ignore tout de la base de travaux — la
    frontière reste nette entre « ce qui vient de SQLite » et « ce qui vient
    du disque ». Échec d'écriture disque : journalisé, jamais remonté — un
    export raté ne doit pas faire échouer une étape par ailleurs réussie.
    """
    job = db.get_job(job_id)
    if job is None:
        return None

    try:
        config.COURSES_DIR.mkdir(parents=True, exist_ok=True)

        # Anti-doublon : un titre modifié entre deux runs ne doit pas laisser
        # une ancienne section du même cours à côté de la nouvelle.
        for stale in config.COURSES_DIR.glob(f"*-{job_id}.md"):
            stale.unlink(missing_ok=True)

        date = (job.get("created_at") or "")[:10] or "0000-00-00"
        title = job.get("title") or job.get("filename") or "cours"
        slug = exporters.safe_filename(title, "md")[: -len(".md")]
        path = config.COURSES_DIR / f"{date}-{slug}-{job_id}.md"

        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(exporters.render(job, "md"), encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError as exc:
        logger.warning("Export du cours %s en échec : %s", job_id, exc)
        return None


def _sync_notebooklm() -> bool:
    """Pousse la compilation de data/cours/ vers le Doc maître Drive.

    Étape non bloquante, comme la publication Obsidian : ``sync_master_doc``
    ne lève jamais (elle journalise et renvoie ``False``), le garde-fou ici
    ne sert qu'à couvrir un import inattendu — aucune erreur Google ne doit
    jamais faire mourir le thread du pipeline.
    """
    try:
        from . import notebooklm_sync

        return notebooklm_sync.sync_master_doc(config.COURSES_DIR)
    except Exception:  # pragma: no cover - garde-fou ultime
        logger.exception("Synchronisation NotebookLM en échec de façon inattendue.")
        return False


def _record_notebooklm_result(job_id: str, success: bool) -> None:
    """Mémorise un résultat de sync sans modifier l'état Obsidian du travail."""
    if success:
        db.update_job(
            job_id, notebooklm_status="synchronise", notebooklm_synced_at=db.now(),
            notebooklm_error=None,
        )
    else:
        db.update_job(
            job_id, notebooklm_status="erreur", notebooklm_error=(
                "La mise à jour du Doc maître a échoué. Vérifiez les identifiants Google "
                "et la connexion, puis réessayez."
            ),
        )


# ------------------------------------------------------ étape 1 : transcription


def run_transcription(job_id: str) -> None:
    """Fichier déposé → texte brut et segments horodatés. Rien de plus."""
    job = db.get_job(job_id, with_content=False)
    if job is None:
        logger.warning("Travail %s introuvable", job_id)
        return
    if job["status"] in {"done", "transcribed", "checked", "published", "canceled"}:
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
        settings = config.load_settings()
        initial_prompt = (
            lexicon.whisper_prompt()
            if settings.lexicon_enabled and settings.lexicon_whisper_prompt
            else None
        )
        for segment in engine.transcribe(
            wav_path,
            model=job["model"],
            language=job["language"],
            duration=duration,
            workdir=workdir,
            initial_prompt=initial_prompt,
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
            review_blocks=db.review_blocks_from_segments(segments),
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
        return
    except Exception as exc:  # pragma: no cover - garde-fou
        logger.exception("Relecture %s en échec", job_id)
        db.mark_finished(
            job_id,
            status="transcribed",
            stage="Transcrit — relecture en échec",
            task=None,
            error=str(exc),
        )
        return
    finally:
        _release(job_id)

    # Comme pour la transcription : chaque étape suivante est enfilée plutôt
    # qu'appelée, et seulement si demandée. Le fact-check saute directement
    # à la publication s'il n'est pas demandé, pour qu'un enchaînement
    # « relecture puis publication, sans vérification externe » reste
    # possible en un clic.
    if job.get("chain", True):
        if job.get("factcheck", True):
            enqueue(job_id, TASK_FACTCHECK)
        elif job.get("publish", True):
            enqueue(job_id, TASK_PUBLISH)
        else:
            # Rien ne suit : le Markdown intermédiaire reste disponible,
            # mais Google ne reçoit rien avant une publication Obsidian.
            _export_course_markdown(job_id)
    else:
        _export_course_markdown(job_id)


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

        # Le repli NIM est volontairement distinct du repli mécanique : il
        # ne s'active qu'après un échec/une indisponibilité Claude et si la
        # clé ainsi que l'option ont été explicitement configurées.
        nim = NimProofreader()
        available, detail = nim.is_available()
        if available:
            try:
                on_progress(0.05, "Claude indisponible : repli NVIDIA NIM…")
                return nim.proofread(segments, structure=bool(job.get("structure", True)), on_progress=on_progress, should_cancel=should_cancel)
            except ProofreadError as exc:
                if should_cancel():
                    raise
                logger.warning("Relecture NVIDIA NIM en échec (%s) : repli mécanique.", exc)
        else:
            logger.info("Repli NVIDIA NIM indisponible (%s).", detail)

    on_progress(0.5, "Relecture mécanique…")
    result = basic_proofread(segments)
    on_progress(1.0, "Relecture terminée.")
    return result


# ------------------------------------------------------ étape 3 : vérification


def run_factcheck(job_id: str) -> None:
    """Texte relu → recherche web ciblée sur ce qui peut se vérifier.

    Ne retouche ni l'audio ni la relecture de fidélité : repart du texte relu
    déjà en base, comme la relecture repart des segments déjà en base.
    """
    job = db.get_job(job_id)
    if job is None:
        logger.warning("Travail %s introuvable", job_id)
        return

    clean_text = job.get("clean_text") or ""
    if not clean_text.strip():
        db.update_job(
            job_id,
            status="error",
            stage="Erreur",
            task=None,
            error="Aucun texte relu à vérifier : lancez d'abord la relecture.",
        )
        return

    progress = _Progress(job_id)
    db.update_job(
        job_id,
        status="running",
        task=TASK_FACTCHECK,
        stage="Préparation de la vérification externe…",
        progress=0.0,
        error=None,
    )

    try:
        if is_cancelled(job_id):
            raise ProofreadError("Vérification externe annulée.")

        settings = config.load_settings()
        new_text, report, entities = factcheck_module.factcheck(
            clean_text,
            duration=float(job.get("duration") or 0.0),
            settings=settings,
            on_progress=progress.scaled(0.0, 1.0, "Vérification externe…"),
            should_cancel=lambda: is_cancelled(job_id),
        )

        db.mark_finished(
            job_id,
            status="checked",
            stage="Vérifié",
            progress=1.0,
            task=None,
            clean_text=new_text,
            verification=_merge_verification(job.get("verification"), report.findings),
            factcheck_report=report.to_dict(),
            entities=entities,
        )

    except ProofreadError as exc:
        # Le texte relu reste intact : on retombe sur l'état « relu » plutôt
        # que de marquer tout le travail en erreur.
        status = "canceled" if is_cancelled(job_id) else "done"
        db.mark_finished(
            job_id,
            status=status,
            stage="Annulé" if status == "canceled" else "Relu — vérification externe en échec",
            task=None,
            progress=1.0,
            error=str(exc),
        )
        logger.info("Vérification externe %s : %s (%s)", job_id, exc, status)
        return
    except Exception as exc:  # pragma: no cover - garde-fou
        logger.exception("Vérification externe %s en échec", job_id)
        db.mark_finished(
            job_id,
            status="done",
            stage="Relu — vérification externe en échec",
            task=None,
            error=str(exc),
        )
        return
    finally:
        _release(job_id)

    # Export systématique : le texte est définitif dès cette étape (fact-
    # check compris), que la publication Obsidian suive ou non.
    _export_course_markdown(job_id)
    if job.get("chain", True) and job.get("publish", True):
        enqueue(job_id, TASK_PUBLISH)


def _merge_verification(existing, new_findings: list) -> dict:
    """Fusionne les points du fact-check dans le rapport de vérification existant.

    Les règles et la lecture par Claude tournent à la relecture ; le
    fact-check tourne après, sur le texte déjà relu. Un seul rapport, dans
    l'ordre habituel — gravité, puis horodatage.
    """
    if isinstance(existing, dict):
        findings = list(existing.get("findings") or [])
        mode = existing.get("mode", "regles")
        checked_pairs = existing.get("checked_pairs", 0)
    else:
        findings, mode, checked_pairs = [], "regles", 0

    findings.extend(f.to_dict() for f in new_findings)
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order.get(f.get("severity"), len(SEVERITIES)), f.get("start", 0.0)))
    counts = {
        severity: sum(1 for f in findings if f.get("severity") == severity)
        for severity in SEVERITIES
    }
    return {
        "findings": findings,
        "checked_pairs": checked_pairs,
        "mode": mode,
        "counts": counts,
    }


# -------------------------------------------------------- étape 4 : publication


def run_publish(job_id: str) -> None:
    """Écrit la fiche dans le coffre Obsidian, à partir du texte déjà en base.

    Peut partir d'un travail « done » (fact-check non demandé : la fiche
    porte alors le statut « non vérifié ») ou « checked » (fiche « incertain »
    ou « vérifié » selon ce que le fact-check a trouvé). Ne recalcule rien.
    """
    job = db.get_job(job_id)
    if job is None:
        logger.warning("Travail %s introuvable", job_id)
        return

    if not (job.get("clean_text") or job.get("raw_text")):
        db.update_job(
            job_id,
            status="error",
            stage="Erreur",
            task=None,
            error="Aucun texte à publier : lancez d'abord la transcription.",
        )
        return

    # Repli en cas d'échec : l'état d'avant cette tentative de publication —
    # capturé ici, avant que le « running » ci-dessous n'écrase la ligne.
    fallback_status = job.get("status") or "done"

    progress = _Progress(job_id)
    db.update_job(
        job_id,
        status="running",
        task=TASK_PUBLISH,
        stage="Publication dans Obsidian…",
        progress=0.0,
        error=None,
    )

    # État final de cette tentative, renseigné dans chaque branche : sert
    # après le bloc try/finally à décider s'il faut exporter et synchroniser
    # (voir plus bas — délibérément hors du try, pour qu'un incident Drive
    # ne puisse jamais se faire passer pour un échec de publication Obsidian).
    final_status: str | None = None

    try:
        if is_cancelled(job_id):
            raise obsidian.ObsidianError("Publication annulée.")

        progress(0.2, "Écriture de la fiche…")
        settings = config.load_settings()
        relative_path = obsidian.publish(job, settings=settings)
        # Le statut « publié » ne doit devenir visible qu'une fois son
        # Markdown intermédiaire disponible. Cela évite une course entre
        # l'interface (ou une synchronisation) et l'export du cours.
        _export_course_markdown(job_id)

        db.mark_finished(
            job_id,
            status="published",
            stage="Publié",
            progress=1.0,
            task=None,
            obsidian_path=relative_path,
            obsidian_published_at=db.now(),
        )
        final_status = "published"

    except obsidian.ObsidianError as exc:
        status = "canceled" if is_cancelled(job_id) else fallback_status
        db.mark_finished(
            job_id,
            status=status,
            stage="Annulé" if status == "canceled" else "Publication Obsidian en échec",
            task=None,
            progress=1.0,
            error=str(exc),
        )
        logger.info("Publication %s : %s (%s)", job_id, exc, status)
        final_status = status
    except Exception as exc:  # pragma: no cover - garde-fou
        logger.exception("Publication %s en échec", job_id)
        db.mark_finished(
            job_id,
            status=fallback_status,
            stage="Publication Obsidian en échec",
            task=None,
            error=str(exc),
        )
        final_status = fallback_status
    finally:
        _release(job_id)

    # Le texte relu (et vérifié, le cas échéant) est définitif dès l'étape 2
    # ou 3 : un échec de publication Obsidian n'a aucune raison d'empêcher le
    # cours d'arriver dans le Doc maître NotebookLM. Seule l'annulation
    # explicite du travail le retient.
    # Google ne passe qu'après une publication Obsidian effectivement
    # réussie ; un échec du coffre ne peut donc jamais créer un état de
    # synchronisation trompeur.
    if final_status == "published":
        sync_requested = config.load_settings().notebooklm_sync_enabled
        synced = _sync_notebooklm()
        if sync_requested:
            _record_notebooklm_result(job_id, synced)


def run_notebooklm_sync(job_id: str) -> None:
    """Met à jour le même Doc maître sans défaire une publication Obsidian."""
    job = db.get_job(job_id, with_content=False)
    if job is None:
        return
    if not job.get("obsidian_path"):
        db.update_job(
            job_id, task=None, notebooklm_status="erreur",
            notebooklm_error="Publiez d’abord cette transcription dans Obsidian.",
        )
        return

    progress = _Progress(job_id)
    progress(0.2, "Synchronisation du Doc maître NotebookLM…")
    _export_course_markdown(job_id)
    success = _sync_notebooklm()
    if success:
        db.update_job(
            job_id, task=None, stage="Publié — synchronisé NotebookLM", progress=1.0,
        )
    else:
        db.update_job(
            job_id, task=None, stage="Publié — synchronisation NotebookLM en échec", progress=1.0,
        )
    _record_notebooklm_result(job_id, success)
    _release(job_id)


_RUNNERS.update(
    {
        TASK_TRANSCRIPTION: run_transcription,
        TASK_PROOFREAD: run_proofread,
        TASK_FACTCHECK: run_factcheck,
        TASK_PUBLISH: run_publish,
        TASK_NOTEBOOKLM: run_notebooklm_sync,
    }
)
