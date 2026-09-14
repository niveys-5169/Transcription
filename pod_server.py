"""
Serveur HTTP pour le pod RunPod de secours.

Même image Docker, même calcul que ``handler.py`` — faster-whisper sur GPU,
même contrat d'entrée/sortie — mais exposé en HTTP continu plutôt qu'en
job serverless, puisqu'un pod RunPod n'a pas de file de jobs intégrée : on
lui parle directement via son URL de proxy
(``https://{pod_id}-{port}.proxy.runpod.net``).

Ce script n'est jamais lancé par défaut : l'image RunPod Serverless démarre
toujours ``handler.py`` (voir le ``CMD`` du Dockerfile). C'est l'application
qui, en créant le pod de secours, remplace la commande de démarrage pour
lancer celui-ci à la place (voir ``app/engines/runpod_pod.py``).
"""
from __future__ import annotations

import base64
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


def transcribe(
    audio_b64: str, model_size: str, language: str | None, initial_prompt: str | None = None
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

        model = get_model(model_size)
        # vad_filter=False pour la même raison que sur handler.py et le
        # moteur local : le filtre de détection de voix a déjà classé un
        # fichier entier comme « silence » sur ce projet, sans la moindre
        # erreur. Mieux vaut traiter un peu de silence que perdre du contenu.
        segments_iter, info = model.transcribe(
            tmp_path,
            language=language,
            vad_filter=False,
            beam_size=5,
            initial_prompt=initial_prompt or None,
        )

        segments = []
        text_parts = []
        for seg in segments_iter:
            segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
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
        )
        self._send_json(400 if "error" in result else 200, result)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[pod_server] En écoute sur le port {port}…")
    server.serve_forever()


if __name__ == "__main__":
    main()
