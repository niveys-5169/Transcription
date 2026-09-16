"""
Serveur HTTP du pod GPU RunPod.

Le pod expose une transcription GPU continue par son URL de proxy
(``https://{pod_id}-{port}.proxy.runpod.net``).

L'image Docker démarre directement ce serveur. RunPod Serverless ne fait plus
partie de l'architecture.
"""
from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# HF_HUB_ENABLE_HF_TRANSFER est déprécié (huggingface_hub a basculé son
# transfert accéléré sur le backend Xet) : s'il traîne dans l'environnement
# (image de base RunPod notamment), il ne fait plus rien à part déclencher
# un FutureWarning à chaque import. On le retire et on active son
# remplaçant à la place, avant tout import de faster_whisper/huggingface_hub.
os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

VALID_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
VOLUME_ROOT = "/runpod-volume"
_model_cache: dict[str, object] = {}


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
        from faster_whisper import WhisperModel

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
    import whisperx

    if model_size not in VALID_MODELS:
        model_size = "large-v3"
    if os.path.isdir(VOLUME_ROOT):
        os.environ.setdefault("HF_HOME", os.path.join(VOLUME_ROOT, "huggingface-cache"))
    device = "cuda"
    language = None if language in (None, "", "auto") else language
    model = whisperx.load_model(
        model_size, device, compute_type="float16", language=language,
        download_root=_model_cache_dir(),
    )
    result = model.transcribe(audio_path, batch_size=16, language=language,
                              initial_prompt=initial_prompt or None)
    detected_language = result.get("language") or language or ""
    align_model, metadata = whisperx.load_align_model(
        language_code=detected_language, device=device,
    )
    result = whisperx.align(result["segments"], align_model, metadata, audio_path, device)

    token = os.environ.get("HF_TOKEN")
    speakers: list[str] = []
    if diarize and token:
        diarizer = whisperx.DiarizationPipeline(use_auth_token=token, device=device)
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


class Handler(BaseHTTPRequestHandler):
    server_version = "TranscriptionPod/1.0"

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
        self._send_json(404, {"error": "introuvable"})

    def do_POST(self):
        if self.path != "/transcribe":
            self._send_json(404, {"error": "introuvable"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        content_type = self.headers.get("Content-Type", "")
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
                result = transcribe(
                    base64.b64encode(audio).decode("ascii"),
                    fields.get("model").get_content() if fields.get("model") else "large-v3",
                    fields.get("language").get_content() if fields.get("language") else "auto",
                    fields.get("initial_prompt").get_content() if fields.get("initial_prompt") else None,
                    (fields.get("diarize").get_content().strip().lower() == "true") if fields.get("diarize") else False,
                )
            except (KeyError, ValueError, TypeError) as exc:
                self._send_json(400, {"error": f"multipart invalide : {exc}"})
                return
            self._send_json(400 if "error" in result else 200, result)
            return
        try:
            job_input = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"JSON invalide : {e}"})
            return

        result = transcribe(
            job_input.get("audio_base64"),
            job_input.get("model", "large-v3"),
            job_input.get("language") or None,
            job_input.get("initial_prompt") or None,
            bool(job_input.get("diarize")),
        )
        self._send_json(400 if "error" in result else 200, result)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[pod_server] En écoute sur le port {port}…")
    server.serve_forever()


if __name__ == "__main__":
    main()
