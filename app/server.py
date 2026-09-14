"""Serveur web de l'application : API JSON + page unique."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, db, exporters, lexicon, media, obsidian, pipeline
from .engines import availability as engine_availability
from .proofread.claude import ClaudeProofreader

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_CHUNK = 1 << 20  # 1 Mo
SSE_INTERVAL = 0.7


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    db.init_db()
    db.reset_interrupted()
    for job_id, task in db.pending_tasks():
        pipeline.enqueue(job_id, task)
    pipeline.start_worker()
    yield


app = FastAPI(title="Transcription de cours", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ------------------------------------------------------------------- page


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ état


@app.get("/api/status")
async def status() -> dict:
    settings = config.load_settings()
    proofreader = ClaudeProofreader(settings)
    claude_ok, claude_detail = proofreader.is_available()
    ffmpeg_ok = media.ffmpeg_available()
    obsidian_ok, obsidian_detail = _obsidian_status(settings)

    return {
        "version": __version__,
        "ffmpeg": {
            "available": ffmpeg_ok,
            "detail": (
                "Extraction audio opérationnelle."
                if ffmpeg_ok
                else "ffmpeg introuvable : installez-le ou "
                "« pip install imageio-ffmpeg »."
            ),
        },
        "engines": engine_availability(),
        "proofread": {"claude": {"available": claude_ok, "detail": claude_detail}},
        "obsidian": {"available": obsidian_ok, "detail": obsidian_detail},
        "models": config.WHISPER_MODELS,
        "settings": settings.public_dict(),
    }


def _obsidian_status(settings) -> tuple[bool, str]:
    if not settings.obsidian_vault_path:
        return False, "Aucun coffre Obsidian configuré dans les réglages."
    try:
        obsidian.resolve(settings.obsidian_vault_path, ".")
    except obsidian.ObsidianError as exc:
        return False, str(exc)
    return True, f"Coffre trouvé : {settings.obsidian_vault_path}"


@app.get("/api/settings")
async def get_settings() -> dict:
    return config.load_settings().public_dict()


@app.post("/api/settings")
async def post_settings(payload: dict = Body(...)) -> dict:
    settings = config.save_settings(payload)
    return settings.public_dict()


# ------------------------------------------------------------------ lexique


@app.get("/api/lexicon")
async def get_lexicon() -> dict:
    """Le lexique MJPM courant — livré, plus les ajouts de l'utilisateur."""
    return {"terms": [term.to_dict() for term in lexicon.load_lexicon()]}


@app.post("/api/lexicon")
async def post_lexicon(payload: dict = Body(...)) -> dict:
    """Ajoute (ou remplace) une entrée du lexique utilisateur.

    Sert notamment à accepter une entité confirmée par une recherche web
    pendant un fact-check : voir ``jobs/{id}`` → ``entities``.
    """
    terme = str(payload.get("terme") or "").strip()
    if not terme:
        raise HTTPException(400, "Le champ « terme » est obligatoire.")

    term = lexicon.Term(
        terme=terme,
        categorie=str(payload.get("categorie") or "autre"),
        sigles=[str(s) for s in payload.get("sigles") or []],
        variantes=[str(v) for v in payload.get("variantes") or []],
        definition=str(payload.get("definition") or ""),
        reference=str(payload.get("reference") or ""),
        wikilink=str(payload.get("wikilink") or terme),
        sources=list(payload.get("sources") or []),
        verifie=bool(payload.get("verifie", False)),
        verifie_le=payload.get("verifie_le"),
    )
    lexicon.save_user_term(term)
    return {"terms": [t.to_dict() for t in lexicon.load_lexicon()]}


# ----------------------------------------------------------------- travaux


@app.get("/api/jobs")
async def list_jobs(q: str | None = None, limit: int = 100) -> dict:
    jobs = db.search_jobs(q, limit) if q else db.list_jobs(limit)
    return {"jobs": [_decorate(job) for job in jobs]}


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    engine: str | None = Form(None),
    model: str | None = Form(None),
    language: str | None = Form(None),
    proofread: str | None = Form(None),
    structure: bool = Form(True),
    verify: bool = Form(True),
    chain: bool = Form(True),
    factcheck: bool = Form(True),
    publish: bool = Form(True),
    one_click: bool = Form(False),
) -> dict:
    settings = config.load_settings()
    engine = engine or settings.default_engine
    model = model or settings.default_model
    language = (language if language is not None else settings.language) or ""
    proofread = proofread or settings.default_proofread

    # « Tout faire » : le bouton principal de la page. Il n'ajoute aucune
    # option nouvelle — chain/factcheck/publish sont déjà les valeurs par
    # défaut de ce formulaire — mais force la relecture Claude si elle est
    # disponible, pour qu'un simple dépôt donne la chaîne complète sans
    # avoir à déplier les options avancées.
    if one_click:
        chain, factcheck, publish = True, True, True
        if proofread == "none":
            proofread = settings.default_proofread

    if engine not in config.ENGINES:
        raise HTTPException(400, f"Moteur inconnu : {engine}")
    if model not in config.WHISPER_MODELS:
        raise HTTPException(400, f"Modèle inconnu : {model}")
    if proofread not in config.PROOFREAD_MODES:
        raise HTTPException(400, f"Mode de relecture inconnu : {proofread}")

    original = Path(file.filename or "cours").name
    config.ensure_dirs()
    staging = config.MEDIA_DIR / "_incoming"
    staging.mkdir(parents=True, exist_ok=True)
    temp_path = staging / f"{uuid.uuid4().hex}{Path(original).suffix.lower()}"

    size = 0
    try:
        with temp_path.open("wb") as out:
            while True:
                block = await file.read(UPLOAD_CHUNK)
                if not block:
                    break
                size += len(block)
                out.write(block)
    finally:
        await file.close()

    if size == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(400, "Le fichier envoyé est vide.")

    job_id = db.create_job(
        filename=original,
        media_path=str(temp_path),
        size_bytes=size,
        engine=engine,
        model=model,
        language=language,
        proofread=proofread,
        structure=structure,
        verify=verify,
        chain=chain,
        factcheck=factcheck,
        publish=publish,
    )

    # Ranger le média dans le dossier du travail, maintenant qu'on a son id.
    final_dir = config.MEDIA_DIR / job_id
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"source{temp_path.suffix}"
    shutil.move(str(temp_path), final_path)
    db.update_job(job_id, media_path=str(final_path))

    pipeline.enqueue(job_id)
    return _decorate(db.get_job(job_id, with_content=False))


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    return _decorate(job)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    if job["status"] in {"done", "error", "canceled"}:
        return _decorate(job)
    pipeline.cancel(job_id)
    db.update_job(job_id, stage="Annulation en cours…")
    return _decorate(db.get_job(job_id, with_content=False))


@app.post("/api/jobs/{job_id}/proofread")
async def proofread_job(job_id: str, payload: dict = Body(default={})) -> dict:
    """Lance (ou relance) la relecture d'un travail déjà transcrit.

    C'est l'étape 2, indépendante : elle repart des segments déjà en base,
    sans retoucher à l'audio ni refaire tourner le moteur de transcription.
    Elle peut donc être jouée plus tard, et rejouée avec d'autres réglages.
    """
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    if job["status"] == "running":
        raise HTTPException(409, "Ce travail est déjà en cours.")
    if job["status"] not in {
        "transcribed", "done", "checked", "published", "error", "canceled",
    }:
        raise HTTPException(409, "Ce travail n'est pas encore transcrit.")

    complet = db.get_job(job_id)
    if not (complet and complet.get("segments")):
        raise HTTPException(
            409, "Aucune transcription à relire : relancez d'abord la transcription."
        )

    mode = payload.get("proofread") or job["proofread"] or "basic"
    if mode not in config.PROOFREAD_MODES:
        raise HTTPException(400, f"Mode de relecture inconnu : {mode}")

    db.update_job(
        job_id,
        proofread=mode,
        structure=bool(payload.get("structure", job["structure"])),
        verify=bool(payload.get("verify", job["verify"])),
        status="queued",
        stage="Relecture en attente",
        progress=0.0,
        error=None,
    )
    pipeline.enqueue(job_id, pipeline.TASK_PROOFREAD)
    return _decorate(db.get_job(job_id, with_content=False))


@app.post("/api/jobs/{job_id}/factcheck")
async def factcheck_job(job_id: str) -> dict:
    """Lance (ou relance) la vérification externe d'un travail déjà relu.

    C'est l'étape 3, indépendante : elle repart du texte relu déjà en base,
    sans refaire tourner ni la transcription ni la relecture.
    """
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    if job["status"] == "running":
        raise HTTPException(409, "Ce travail est déjà en cours.")
    if job["status"] not in {"done", "checked", "published", "error", "canceled"}:
        raise HTTPException(409, "Ce travail n'est pas encore relu.")

    complet = db.get_job(job_id)
    if not (complet and complet.get("clean_text")):
        raise HTTPException(
            409, "Aucun texte relu à vérifier : relancez d'abord la relecture."
        )

    db.update_job(
        job_id,
        status="queued",
        stage="Vérification externe en attente",
        progress=0.0,
        error=None,
    )
    pipeline.enqueue(job_id, pipeline.TASK_FACTCHECK)
    return _decorate(db.get_job(job_id, with_content=False))


@app.post("/api/jobs/{job_id}/publish")
async def publish_job(job_id: str) -> dict:
    """Écrit (ou réécrit) la fiche du travail dans le coffre Obsidian.

    C'est l'étape 4, indépendante : elle repart du texte déjà en base — relu,
    vérifié ou non — sans rien recalculer.
    """
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    if job["status"] == "running":
        raise HTTPException(409, "Ce travail est déjà en cours.")
    if job["status"] not in {"done", "checked", "published", "error", "canceled"}:
        raise HTTPException(409, "Ce travail n'est pas encore relu.")

    settings = config.load_settings()
    if not settings.obsidian_vault_path:
        raise HTTPException(409, "Aucun coffre Obsidian configuré dans les réglages.")

    db.update_job(
        job_id,
        status="queued",
        stage="Publication en attente",
        progress=0.0,
        error=None,
    )
    pipeline.enqueue(job_id, pipeline.TASK_PUBLISH)
    return _decorate(db.get_job(job_id, with_content=False))


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> dict:
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    if job["status"] == "running":
        raise HTTPException(409, "Ce travail est déjà en cours.")
    if not Path(job["media_path"] or "").exists():
        raise HTTPException(
            410, "Le fichier d'origine n'est plus disponible : déposez-le à nouveau."
        )
    db.update_job(
        job_id, status="queued", stage="En attente", progress=0.0, error=None
    )
    pipeline.enqueue(job_id, pipeline.TASK_TRANSCRIPTION)
    return _decorate(db.get_job(job_id, with_content=False))


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict:
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    pipeline.cancel(job_id)
    db.delete_job(job_id)
    shutil.rmtree(config.MEDIA_DIR / job_id, ignore_errors=True)

    # Un cours supprimé ne doit pas rester à jamais dans le Doc maître
    # NotebookLM : son .md (data/cours/) est retiré, puis le Doc maître
    # resynchronisé pour refléter la suppression. Échec Drive sans
    # conséquence sur la suppression elle-même, déjà actée ci-dessus.
    for stale in config.COURSES_DIR.glob(f"*-{job_id}.md"):
        stale.unlink(missing_ok=True)
    try:
        from . import notebooklm_sync

        notebooklm_sync.sync_master_doc(config.COURSES_DIR)
    except Exception:  # pragma: no cover - garde-fou : jamais fatal ici non plus
        logger.exception("Synchronisation NotebookLM en échec après suppression.")

    return {"deleted": job_id}


@app.get("/api/jobs/{job_id}/audio")
async def job_audio(job_id: str) -> FileResponse:
    """Le WAV extrait — pour vérifier l'extraction avant de blâmer Whisper."""
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    wav = Path(job["wav_path"] or "")
    if not wav.exists():
        raise HTTPException(404, "Audio extrait indisponible.")
    return FileResponse(wav, media_type="audio/wav", filename=f"{job_id}.wav")


@app.get("/api/jobs/{job_id}/download/{fmt}")
async def download(job_id: str, fmt: str) -> Response:
    if fmt not in exporters.EXTENSIONS:
        raise HTTPException(
            400, f"Formats possibles : {', '.join(exporters.EXTENSIONS)}."
        )
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    # Un travail transcrit mais pas encore relu est déjà téléchargeable : le
    # texte brut, les segments et les sous-titres sont là. C'est tout l'objet
    # de la séparation des étapes.
    if job["status"] not in {"transcribed", "done", "checked", "published"}:
        raise HTTPException(409, "La transcription n'est pas terminée.")

    filename = exporters.safe_filename(job["filename"], exporters.DOWNLOAD_EXTENSIONS[fmt])
    return Response(
        content=exporters.render(job, fmt),
        media_type=exporters.EXTENSIONS[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------- SSE


@app.get("/api/events")
async def events() -> StreamingResponse:
    """Flux d'avancement : un instantané de la file dès qu'elle change."""

    async def stream():
        previous = None
        idle = 0
        while True:
            snapshot = [_decorate(job) for job in db.list_jobs(50)]
            payload = json.dumps({"jobs": snapshot}, ensure_ascii=False)
            if payload != previous:
                previous = payload
                idle = 0
                yield f"data: {payload}\n\n"
            else:
                idle += 1
                if idle >= 20:  # battement de cœur anti-coupure des proxys
                    idle = 0
                    yield ": ping\n\n"
            await asyncio.sleep(SSE_INTERVAL)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ helpers


def _decorate(job: dict | None) -> dict:
    """Ajoute l'avancement en direct et déplie le résumé JSON."""
    if job is None:
        return {}
    job = dict(job)
    live = pipeline.live_state(job["id"])
    if live and job["status"] == "running":
        job["progress"] = live["progress"]
        job["stage"] = live["stage"]
    if isinstance(job.get("summary"), str):
        try:
            job["summary"] = json.loads(job["summary"]) if job["summary"] else []
        except json.JSONDecodeError:
            job["summary"] = []
    job.pop("media_path", None)
    job.pop("wav_path", None)
    return job
