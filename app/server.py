"""Serveur web de l'application : API JSON + page unique."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, db, exporters, lexicon, media, obsidian, pipeline
from .engines import availability as engine_availability
from .proofread import factcheck as factcheck_module
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


# -------------------------------------------------------- navigation de dossiers


@app.get("/api/browse")
async def browse(path: str | None = None) -> dict:
    """Liste les sous-dossiers d'un chemin, pour le sélecteur de coffre Obsidian.

    Une application locale à un seul utilisateur n'a pas besoin d'un vrai
    sélecteur de fichiers natif : ce point d'entrée, en lecture seule, en
    tient lieu — il ne renvoie que des noms de dossiers, jamais un contenu
    de fichier. Comme le reste de l'application, il suppose un usage local ;
    lancée avec ``--host 0.0.0.0``, l'arborescence des dossiers devient
    visible à qui atteint le port sur le réseau (voir le README).
    """
    target = Path(path).expanduser() if path else Path.home()
    try:
        target = target.resolve()
    except OSError:
        target = Path.home().resolve()

    if not target.is_dir():
        target = Path.home().resolve()

    directories: list[dict] = []
    try:
        entries = sorted(
            (entry for entry in target.iterdir() if entry.is_dir() and not entry.name.startswith(".")),
            key=lambda entry: entry.name.lower(),
        )
        directories = [{"name": entry.name, "path": str(entry)} for entry in entries]
    except OSError:
        pass  # dossier illisible (permissions) : liste vide, pas une erreur

    parent = target.parent
    return {
        "path": str(target),
        "parent": str(parent) if parent != target else None,
        "directories": directories,
    }


# ----------------------------------------------------------------- travaux


@app.get("/api/jobs")
async def list_jobs(q: str | None = None, limit: int = 100) -> dict:
    jobs = db.search_jobs(q, limit) if q else db.list_jobs(limit)
    return {"jobs": _decorate_list(jobs)}


@app.get("/api/search")
async def search_transcripts(q: str, limit: int = 50) -> dict:
    """Recherche globale avec contexte et position média lorsqu'elle existe."""
    return {"results": db.search_transcripts(q, max(1, min(limit, 100)))}


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


@app.get("/api/jobs/{job_id}/media")
async def job_media(job_id: str) -> FileResponse:
    """Le média d'origine, destiné au lecteur synchronisé du navigateur."""
    job = db.get_job(job_id, with_content=False)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    source = Path(job["media_path"] or "")
    if not source.is_file():
        raise HTTPException(
            404,
            "Le média d'origine n'est plus disponible ; utilisez l'audio extrait.",
        )
    media_type, _ = mimetypes.guess_type(job["filename"] or source.name)
    return FileResponse(source, media_type=media_type or "application/octet-stream")


@app.get("/api/jobs/{job_id}/review-blocks")
async def get_review_blocks(job_id: str) -> dict:
    if db.get_job(job_id, with_content=False) is None:
        raise HTTPException(404, "Travail introuvable.")
    return {"blocks": db.ensure_review_blocks(job_id)}


@app.put("/api/jobs/{job_id}/review-blocks/{block_id}")
async def put_review_block(job_id: str, block_id: str, payload: dict = Body(...)) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "Le texte du bloc est obligatoire.")
    block = db.update_review_block(job_id, block_id, text.strip())
    if block is None:
        raise HTTPException(404, "Bloc de révision introuvable.")
    return block


_ANNOTATION_TYPES = {"note", "highlight", "review"}
_ANNOTATION_STATUSES = {"a_verifier", "valide", "ignore"}


def _annotation_payload(payload: dict, *, partial: bool = False) -> dict:
    fields: dict = {}
    if "type" in payload:
        kind = payload["type"]
        if kind not in _ANNOTATION_TYPES:
            raise HTTPException(400, "Type d'annotation inconnu.")
        fields["type"] = kind
    if "status" in payload:
        status = payload["status"]
        if status is not None and status not in _ANNOTATION_STATUSES:
            raise HTTPException(400, "Statut de révision inconnu.")
        fields["status"] = status
    for key in ("color", "content"):
        if key in payload:
            value = payload[key]
            if value is not None and not isinstance(value, str):
                raise HTTPException(400, f"Le champ « {key} » doit être du texte.")
            fields[key] = value
    if not partial and "type" not in fields:
        raise HTTPException(400, "Le type d'annotation est obligatoire.")
    return fields


@app.get("/api/jobs/{job_id}/annotations")
async def get_annotations(job_id: str) -> dict:
    if db.get_job(job_id, with_content=False) is None:
        raise HTTPException(404, "Travail introuvable.")
    return {"annotations": db.list_annotations(job_id)}


@app.post("/api/jobs/{job_id}/annotations")
async def post_annotation(job_id: str, payload: dict = Body(...)) -> dict:
    if db.get_job(job_id, with_content=False) is None:
        raise HTTPException(404, "Travail introuvable.")
    block_id = payload.get("block_id")
    if not isinstance(block_id, str) or not any(
        block.get("id") == block_id for block in db.ensure_review_blocks(job_id)
    ):
        raise HTTPException(400, "Le bloc associé est introuvable.")
    fields = _annotation_payload(payload)
    return db.create_annotation(job_id, block_id=block_id, kind=fields["type"],
                                color=fields.get("color"), content=fields.get("content"),
                                status=fields.get("status"))


@app.patch("/api/jobs/{job_id}/annotations/{annotation_id}")
async def patch_annotation(job_id: str, annotation_id: str, payload: dict = Body(...)) -> dict:
    annotation = db.get_annotation(annotation_id)
    if annotation is None or annotation["job_id"] != job_id:
        raise HTTPException(404, "Annotation introuvable.")
    updated = db.update_annotation(annotation_id, **_annotation_payload(payload, partial=True))
    assert updated is not None
    return updated


@app.delete("/api/jobs/{job_id}/annotations/{annotation_id}")
async def delete_annotation(job_id: str, annotation_id: str) -> dict:
    annotation = db.get_annotation(annotation_id)
    if annotation is None or annotation["job_id"] != job_id:
        raise HTTPException(404, "Annotation introuvable.")
    db.delete_annotation(annotation_id)
    return {"deleted": annotation_id}


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


def _find_pending(job: dict, correction_id: str) -> tuple[dict, dict]:
    """Le rapport de fact-check du travail, et la correction en attente visée.

    Lève une HTTPException si le travail n'a pas de rapport, ou si la
    correction n'y figure pas — un ``correction_id`` obsolète (déjà traité
    ailleurs, ou d'un autre travail) doit être signalé, pas deviné.
    """
    report = job.get("factcheck_report")
    if not isinstance(report, dict):
        raise HTTPException(409, "Ce travail n'a pas de vérification par recherche web.")
    for item in report.get("pending") or []:
        if item.get("id") == correction_id:
            return report, item
    raise HTTPException(404, "Correction introuvable.")


@app.post("/api/jobs/{job_id}/corrections/{correction_id}/valider")
async def valider_correction(job_id: str, correction_id: str) -> dict:
    """Applique une correction du fact-check laissée en attente de validation.

    Ces corrections (confiance insuffisante, ou catégorie sensible même à
    confiance haute — voir ``factcheck.SENSITIVE_CLAIM_TYPES``) ne sont
    jamais appliquées seules : cette route est la décision humaine qui
    manquait.
    """
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    report, item = _find_pending(job, correction_id)
    if item.get("status") != "attente":
        raise HTTPException(409, "Cette correction a déjà été traitée.")

    correction = factcheck_module.PendingCorrection(**item)
    nouveau_texte = factcheck_module.accept_pending(job.get("clean_text") or "", correction)
    if nouveau_texte is None:
        raise HTTPException(
            409,
            "Cette citation ne se retrouve plus dans le texte relu actuel : "
            "corrigez-la manuellement si nécessaire.",
        )

    item["status"] = "validee"
    report["corrections"] = int(report.get("corrections") or 0) + 1
    db.update_job(job_id, clean_text=nouveau_texte, factcheck_report=report)
    return _decorate(db.get_job(job_id))


@app.post("/api/jobs/{job_id}/corrections/{correction_id}/rejeter")
async def rejeter_correction(job_id: str, correction_id: str) -> dict:
    """Rejette une correction du fact-check laissée en attente de validation.

    Le texte relu n'est pas modifié : seule la note de bas de page change de
    libellé, pour ne pas laisser croire qu'une validation reste en attente.
    """
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    report, item = _find_pending(job, correction_id)
    if item.get("status") != "attente":
        raise HTTPException(409, "Cette correction a déjà été traitée.")

    correction = factcheck_module.PendingCorrection(**item)
    nouveau_texte = factcheck_module.reject_pending(job.get("clean_text") or "", correction)

    item["status"] = "rejetee"
    db.update_job(job_id, clean_text=nouveau_texte, factcheck_report=report)
    return _decorate(db.get_job(job_id))


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
            snapshot = _decorate_list(db.list_jobs(50))
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


def _decorate_list(jobs: list[dict]) -> list[dict]:
    """Décore une liste de travaux pour la bibliothèque : avancement en
    direct, plus le compteur de points à vérifier et l'état de publication
    utilisés par les cartes (voir _publication_status)."""
    decorated = [_decorate(job) for job in jobs]
    counts = db.pending_annotation_counts(job["id"] for job in decorated)
    for job in decorated:
        job["pending_review_count"] = counts.get(job["id"], 0)
        job["publication_status"] = _publication_status(job)
    return decorated


def _publication_status(job: dict) -> str:
    """État de publication affiché sur les cartes de bibliothèque.

    Valeurs : ``publie`` (une note Obsidian existe déjà), ``pret`` (le texte
    relu est disponible mais rien n'a encore été publié), ``non_disponible``
    (pas encore de texte relu à publier).
    """
    if job.get("obsidian_path"):
        return "publie"
    if job.get("status") in ("done", "checked", "published"):
        return "pret"
    return "non_disponible"
