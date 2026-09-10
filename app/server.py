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

from . import __version__, config, db, exporters, media, pipeline
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
        "models": config.WHISPER_MODELS,
        "settings": settings.public_dict(),
    }


@app.get("/api/settings")
async def get_settings() -> dict:
    return config.load_settings().public_dict()


@app.post("/api/settings")
async def post_settings(payload: dict = Body(...)) -> dict:
    settings = config.save_settings(payload)
    return settings.public_dict()


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
) -> dict:
    settings = config.load_settings()
    engine = engine or settings.default_engine
    model = model or settings.default_model
    language = (language if language is not None else settings.language) or ""
    proofread = proofread or settings.default_proofread

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
    if job["status"] not in {"transcribed", "done", "error", "canceled"}:
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
    # de la séparation des deux étapes.
    if job["status"] not in {"transcribed", "done"}:
        raise HTTPException(409, "La transcription n'est pas terminée.")

    filename = exporters.safe_filename(job["filename"], fmt)
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
