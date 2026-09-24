"""
Serveur HTTP du pod GPU RunPod.

Le pod expose une transcription GPU continue par son URL de proxy
(``https://{pod_id}-{port}.proxy.runpod.net``).

L'image Docker démarre directement ce serveur. RunPod Serverless ne fait plus
partie de l'architecture.
"""
from __future__ import annotations

import base64
import ctypes
from email import policy
from email.parser import BytesParser
import json
import os
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# HF_HUB_ENABLE_HF_TRANSFER est déprécié (huggingface_hub a basculé son
# transfert accéléré sur le backend Xet) : s'il traîne dans l'environnement
# (une image de base pourrait le définir ; la nôtre ne le fait plus), il ne
# fait plus rien à part déclencher un FutureWarning à chaque import. On le
# retire et on active son remplaçant à la place, avant tout import de
# faster_whisper/huggingface_hub — le `pop` reste inoffensif si la variable
# est déjà absente.
os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

VALID_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
VOLUME_ROOT = "/runpod-volume"
_model_cache: dict[str, object] = {}
_whisperx_model_cache: dict[tuple[str, str | None, str | None], object] = {}
_align_model_cache: dict[str, tuple[object, object]] = {}
_diarizer_cache: dict[tuple[str, str], object] = {}
_model_cache_lock = threading.Lock()


def _configure_model_cache_environment() -> None:
    """Place tous les caches de modèles sur le volume réseau persistant."""
    if not os.path.isdir(VOLUME_ROOT):
        return
    hf_home = os.path.join(VOLUME_ROOT, "huggingface-cache")
    os.environ.setdefault("HF_HOME", hf_home)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(hf_home, "hub"))
    os.environ.setdefault("TORCH_HOME", os.path.join(VOLUME_ROOT, "torch-cache"))
    os.environ.setdefault("XDG_CACHE_HOME", os.path.join(VOLUME_ROOT, "cache"))


# Les bibliothèques lisent ces variables pendant leur import : configurer les
# caches avant le premier ``import whisperx``.
_configure_model_cache_environment()

# Sous-bibliothèques de cuDNN 9, dans l'ordre de leurs dépendances (graph ←
# ops ← cnn/adv ; les moteurs et l'heuristique s'appuient sur les trois
# premières). Voir _preload_cudnn.
CUDNN_SUBLIBS = (
    "libcudnn_graph.so.9",
    "libcudnn_ops.so.9",
    "libcudnn_cnn.so.9",
    "libcudnn_adv.so.9",
    "libcudnn_engines_precompiled.so.9",
    "libcudnn_engines_runtime_compiled.so.9",
    "libcudnn_heuristic.so.9",
)
_cudnn_preloaded = False


def _preload_cudnn() -> None:
    """Charge en RTLD_GLOBAL les sous-bibliothèques de cuDNN 9 du wheel pip.

    cuDNN 9 est découpé en une bibliothèque principale (``libcudnn.so.9``,
    que torch précharge) et des sous-bibliothèques que la principale ouvre
    elle-même par ``dlopen`` sur leur seul nom (``libcudnn_cnn.so.9``...) au
    moment de la première convolution. Le wheel ``nvidia-cudnn-cu12`` les
    dépose dans ``site-packages/nvidia/cudnn/lib``, un dossier que le
    chargeur dynamique ne parcourt pas : ctranslate2 (faster-whisper) meurt
    alors d'un « Unable to load any of {libcudnn_cnn.so.9.1.0, ...} » puis
    « Invalid handle. Cannot load symbol cudnnCreateConvolutionDescriptor »,
    un abort natif qui tue le processus sans exception Python — le pod
    redémarre et l'application ne voit que des 502/404 du proxy.

    Une fois une bibliothèque chargée ici par son chemin complet, un
    ``dlopen`` ultérieur sur son ``SONAME`` la retrouve sans parcourir le
    moindre dossier. Le ``Dockerfile`` ajoute aussi le dossier à
    ``LD_LIBRARY_PATH`` ; ce préchargement rend le serveur robuste même
    lancé hors de cette image (variable absente ou écrasée par RunPod).
    À appeler après ``import torch`` (qui a déjà chargé cuBLAS et
    ``libcudnn.so.9`` en global) et avant toute inférence ctranslate2.
    """
    global _cudnn_preloaded
    if _cudnn_preloaded:
        return
    _cudnn_preloaded = True
    try:
        import nvidia.cudnn
    except ImportError:
        return
    lib_dir = os.path.join(os.path.dirname(nvidia.cudnn.__file__), "lib")
    for name in CUDNN_SUBLIBS:
        path = os.path.join(lib_dir, name)
        if not os.path.exists(path):
            continue
        try:
            ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            print(f"[pod_server] Préchargement de {name} impossible : {exc}")


def _confidence_from_logprob(avg_logprob: float | None) -> float | None:
    """Mappage indicatif de avg_logprob (~[-1.5, 0]) vers [0, 1].

    Même formule que app/engines/local.py et handler.py — purement
    informatif pour la coloration de relecture côté client, jamais utilisé
    pour modifier le texte transcrit.
    """
    if avg_logprob is None:
        return None
    return round(max(0.0, min(1.0, 1.0 + avg_logprob / 1.5)), 3)


def _model_cache_dir():
    """Cache Hugging Face sur le volume reseau RunPod, s'il est monte.

    Voir app/engines/runpod_pod.py (VOLUME_MOUNT_PATH) pour le cote creation
    du pod : c'est lui qui demande a RunPod de monter le volume a ce meme
    chemin. Un volume survit a la recreation d'un pod : le modele n'y est
    telecharge qu'une fois, sans avoir a l'embarquer dans l'image Docker
    (ce qui la rendrait lourde a tirer). Sans volume configure, on retombe
    sur le cache Hugging Face par defaut de l'image.
    """
    if os.path.isdir(VOLUME_ROOT):
        return os.path.join(VOLUME_ROOT, "huggingface-cache", "hub")
    return None


def get_model(model_size):
    if model_size not in VALID_MODELS:
        model_size = "large-v3"
    if model_size not in _model_cache:
        # cuDNN 9 vient du wheel torch (nvidia-cudnn-cu12) ; ctranslate2 ne
        # le trouve que si torch a déjà été importé (il précharge la
        # bibliothèque en RTLD_GLOBAL). Le chemin WhisperX importe déjà
        # torch en premier ; ce chemin de secours faster-whisper direct ne
        # le faisait pas.
        import torch  # noqa: F401
        from faster_whisper import WhisperModel

        _preload_cudnn()
        print(f"[pod_server] Chargement du modele '{model_size}' sur GPU (float16)...")
        _model_cache[model_size] = WhisperModel(
            model_size, device="cuda", compute_type="float16",
            download_root=_model_cache_dir(),
        )
        print(f"[pod_server] Modele '{model_size}' pret.")
    return _model_cache[model_size]


def _transcribe_with_whisperx(
    audio_path: str, model_size: str, language: str | None,
    initial_prompt: str | None, diarize: bool,
) -> dict:
    """WhisperX : transcription, alignement mot à mot et diarisation optionnelle."""
    _configure_model_cache_environment()
    import whisperx

    # whisperx importe torch ; les sous-bibliothèques cuDNN doivent être
    # chargées avant la première inférence ctranslate2 (model.transcribe).
    _preload_cudnn()
    if model_size not in VALID_MODELS:
        model_size = "large-v3"
    device = "cuda"
    language = None if language in (None, "", "auto") else language
    prompt = initial_prompt or None
    model_key = (model_size, language, prompt)
    with _model_cache_lock:
        model = _whisperx_model_cache.get(model_key)
        if model is None:
            # Ne conserver qu'un pipeline ASR pour éviter d'accumuler plusieurs
            # modèles large-v3 en VRAM quand la langue ou le lexique change.
            _whisperx_model_cache.clear()
            print(f"[pod_server] Chargement WhisperX '{model_size}' sur GPU…")
            started = time.monotonic()
            model = whisperx.load_model(
                model_size, device, compute_type="float16", language=language,
                download_root=_model_cache_dir(),
                # FasterWhisperPipeline.transcribe() ne reçoit pas d'options
                # ASR personnalisées. WhisperX les fixe au chargement.
                asr_options={"initial_prompt": prompt},
            )
            _whisperx_model_cache[model_key] = model
            print(f"[pod_server] WhisperX prêt en {time.monotonic() - started:.1f}s.")
    result = model.transcribe(audio_path, batch_size=16, language=language)
    detected_language = result.get("language") or language or ""
    with _model_cache_lock:
        cached_alignment = _align_model_cache.get(detected_language)
        if cached_alignment is None:
            print(f"[pod_server] Chargement du modèle d'alignement '{detected_language}'…")
            started = time.monotonic()
            cached_alignment = whisperx.load_align_model(
                language_code=detected_language, device=device,
            )
            _align_model_cache[detected_language] = cached_alignment
            print(
                f"[pod_server] Alignement '{detected_language}' prêt en "
                f"{time.monotonic() - started:.1f}s."
            )
    align_model, metadata = cached_alignment
    result = whisperx.align(result["segments"], align_model, metadata, audio_path, device)
    token = os.environ.get("HF_TOKEN")
    speakers: list[str] = []
    if diarize and token:
        diarizer_key = (device, token)
        with _model_cache_lock:
            diarizer = _diarizer_cache.get(diarizer_key)
            if diarizer is None:
                print("[pod_server] Chargement du modèle de diarisation…")
                started = time.monotonic()
                diarizer = whisperx.DiarizationPipeline(
                    use_auth_token=token, device=device
                )
                _diarizer_cache.clear()
                _diarizer_cache[diarizer_key] = diarizer
                print(
                    f"[pod_server] Diarisation prête en "
                    f"{time.monotonic() - started:.1f}s."
                )
        diarized = diarizer(audio_path)
        result = whisperx.assign_word_speakers(diarized, result)
        speakers = sorted({str(segment["speaker"]) for segment in result["segments"] if segment.get("speaker")})

    segments = []
    for segment in result.get("segments") or []:
        words = [
            {
                "start": round(float(word["start"]), 3),
                "end": round(float(word["end"]), 3),
                "text": str(word.get("word") or word.get("text") or "").strip(),
                "confidence": word.get("score"),
            }
            for word in segment.get("words") or []
            if word.get("start") is not None and word.get("end") is not None
        ]
        segments.append({
            "start": round(float(segment.get("start") or 0), 3),
            "end": round(float(segment.get("end") or 0), 3),
            "text": str(segment.get("text") or ""),
            "confidence": segment.get("score"),
            "speaker": segment.get("speaker"),
            "words": words,
        })
    return {"text": "".join(segment["text"] for segment in segments).strip(),
            "segments": segments, "language": detected_language, "speakers": speakers}


def transcribe(
    audio_b64: str, model_size: str, language: str | None, initial_prompt: str | None = None,
    diarize: bool = False,
) -> dict:
    if not audio_b64:
        return {"error": "audio_base64 manquant dans la requête."}

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception as e:
        return {"error": f"audio_base64 invalide : {e}"}

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # L'image de production contient WhisperX. Le fallback conserve le
        # contrat pour un ancien pod pendant la phase de déploiement et rend
        # les tests sans GPU autonomes.
        try:
            return _transcribe_with_whisperx(tmp_path, model_size, language, initial_prompt, diarize)
        except ModuleNotFoundError as exc:
            if exc.name != "whisperx":
                raise

        model = get_model(model_size)
        # vad_filter=False pour la même raison que sur handler.py et le
        # moteur local : le filtre de détection de voix a déjà classé un
        # fichier entier comme « silence » sur ce projet, sans la moindre
        # erreur. Mieux vaut traiter un peu de silence que perdre du contenu.
        segments_iter, info = model.transcribe(
            tmp_path,
            language=None if language == "auto" else language,
            vad_filter=False,
            beam_size=5,
            initial_prompt=initial_prompt or None,
        )

        segments = []
        text_parts = []
        for seg in segments_iter:
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "confidence": _confidence_from_logprob(
                    getattr(seg, "avg_logprob", None)
                ),
            })
            text_parts.append(seg.text)

        return {
            "text": "".join(text_parts).strip(),
            "segments": segments,
            "language": getattr(info, "language", language),
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class _RequeteInvalide(ValueError):
    """Corps de requête illisible (multipart ou JSON) : réponse 400."""


def _parse_transcription_request(content_type: str, raw: bytes) -> tuple:
    """Extrait ``(audio_b64, model, language, initial_prompt, diarize)`` du
    corps d'une requête, en multipart (fichier entier) ou en JSON (base64).

    Partagé par ``/transcribe`` (synchrone) et ``/jobs`` (asynchrone) : les
    deux acceptent exactement les mêmes corps, seule la façon de rendre le
    résultat diffère.
    """
    if content_type.startswith("multipart/form-data"):
        try:
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
                + raw
            )
            fields = {
                part.get_param("name", header="content-disposition"): part
                for part in message.iter_parts()
            }
            audio = fields["audio"].get_payload(decode=True) or b""

            def text_field(name: str) -> str | None:
                # ``get_content()`` décode une partie sans ``charset`` en
                # ASCII avec remplacement : « médical » devenait
                # « m\ufffd\ufffddical » dans l'amorce du lexique, et Whisper
                # recrachait ensuite cette amorce corrompue en boucle. Les
                # clients (httpx, navigateurs) envoient les champs en UTF-8 :
                # on décode strictement, une erreur rejette la requête.
                part = fields.get(name)
                if part is None:
                    return None
                return (part.get_payload(decode=True) or b"").decode("utf-8")

            model = text_field("model")
            language = text_field("language")
            prompt = text_field("initial_prompt")
            diarize = text_field("diarize")
            return (
                base64.b64encode(audio).decode("ascii"),
                model if model is not None else "large-v3",
                language if language is not None else "auto",
                prompt or None,
                (diarize.strip().lower() == "true") if diarize is not None else False,
            )
        except UnicodeDecodeError as exc:
            raise _RequeteInvalide(f"champ multipart non UTF-8 : {exc}") from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise _RequeteInvalide(f"multipart invalide : {exc}") from exc
    try:
        job_input = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise _RequeteInvalide(f"JSON invalide : {exc}") from exc
    if not isinstance(job_input, dict):
        raise _RequeteInvalide("JSON invalide : un objet est attendu.")
    return (
        job_input.get("audio_base64"),
        job_input.get("model", "large-v3"),
        job_input.get("language") or None,
        job_input.get("initial_prompt") or None,
        bool(job_input.get("diarize")),
    )


# --------------------------------------------------------- travaux asynchrones
#
# Un gros fichier (plusieurs heures d'audio) se transcrit en bien plus que
# les ~100 s au bout desquelles le proxy HTTP de RunPod abandonne une
# réponse qui n'arrive pas — et bien plus que le délai de lecture que peut
# raisonnablement tenir l'application. Un POST synchrone sur /transcribe ne
# peut donc réussir que sur des fichiers courts. Pour les autres, /jobs
# accepte le fichier, répond aussitôt avec un identifiant, et calcule dans un
# thread ; l'application sonde ensuite /jobs/{id} par de petites requêtes
# rapides, chacune bien en deçà du délai du proxy.
#
# Un seul travail à la fois sur le GPU (verrou) : l'application n'en envoie
# de toute façon qu'un seul (voir app/pipeline.py). Les travaux terminés
# restent en mémoire le temps d'être relevés, puis les plus anciens sont
# oubliés au-delà de JOBS_MAX_KEPT — il n'y a pas de disque à remplir.

JOBS_MAX_KEPT = 20
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_gpu_lock = threading.Lock()


def _run_job(job_id: str, args: tuple) -> None:
    with _gpu_lock:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["started_at"] = time.time()
        try:
            result = transcribe(*args)
        except Exception as exc:  # transcribe() attrape déjà tout ; ceinture et bretelles
            result = {"error": str(exc)}
        with _jobs_lock:
            job = _jobs[job_id]
            job["finished_at"] = time.time()
            if "error" in result:
                job["status"] = "error"
                job["error"] = result["error"]
            else:
                job["status"] = "done"
                job["result"] = result
            statut = job["status"]
    print(f"[pod_server] Travail {job_id} terminé ({statut}).")


def submit_job(args: tuple) -> str:
    """Enregistre un travail et lance son calcul dans un thread."""
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "created_at": time.time()}
        # Oublie les travaux terminés les plus anciens au-delà du plafond.
        finis = [
            (job["finished_at"], key)
            for key, job in _jobs.items()
            if job["status"] in ("done", "error") and key != job_id
        ]
        for _, key in sorted(finis)[: max(0, len(_jobs) - JOBS_MAX_KEPT)]:
            _jobs.pop(key, None)
    thread = threading.Thread(target=_run_job, args=(job_id, args), daemon=True)
    thread.start()
    print(f"[pod_server] Travail {job_id} accepté.")
    return job_id


def job_status(job_id: str) -> dict | None:
    """État public d'un travail : ``status`` (queued/running/done/error),
    ``result`` une fois terminé, ``error`` en cas d'échec."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        payload = {"job_id": job_id, "status": job["status"]}
        if job["status"] == "done":
            payload["result"] = job["result"]
        elif job["status"] == "error":
            payload["error"] = job["error"]
        return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "TranscriptionPod/1.1"

    def log_message(self, format, *args):  # noqa: A002 - signature imposée
        print(f"[pod_server] {self.address_string()} - {format % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path.startswith("/jobs/"):
            job_id = self.path[len("/jobs/"):].split("?", 1)[0].strip("/")
            payload = job_status(job_id) if job_id else None
            if payload is None:
                self._send_json(404, {"error": f"travail inconnu : {job_id}"})
                return
            self._send_json(200, payload)
            return
        self._send_json(404, {"error": "introuvable"})

    def _read_request(self) -> tuple:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        return _parse_transcription_request(self.headers.get("Content-Type", ""), raw)

    def do_POST(self):
        if self.path == "/transcribe":
            try:
                args = self._read_request()
            except _RequeteInvalide as exc:
                self._send_json(400, {"error": str(exc)})
                return
            result = transcribe(*args)
            self._send_json(400 if "error" in result else 200, result)
            return
        if self.path == "/jobs":
            try:
                args = self._read_request()
            except _RequeteInvalide as exc:
                self._send_json(400, {"error": str(exc)})
                return
            if not args[0]:
                self._send_json(400, {"error": "audio manquant dans la requête."})
                return
            job_id = submit_job(args)
            self._send_json(202, {"job_id": job_id, "status": "queued"})
            return
        self._send_json(404, {"error": "introuvable"})


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    print(f"[pod_server] HF_TOKEN présent : {'oui' if os.environ.get('HF_TOKEN') else 'non'}.")
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[pod_server] En écoute sur le port {port}…")
    server.serve_forever()


if __name__ == "__main__":
    main()
