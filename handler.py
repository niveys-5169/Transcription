"""
RunPod Serverless handler for Transcription Locale.

Reçoit un job {"input": {"audio_base64": "...", "model": "large-v3",
"language": "fr"}}, transcrit avec faster-whisper sur GPU (CUDA + float16),
et renvoie {"text": ..., "segments": [...], "language": ...} — le même
format que le serveur local (serveur.py), pour que la page web puisse
utiliser l'un ou l'autre de façon interchangeable.
"""
import base64
import os
import tempfile
import runpod

# HF_HUB_ENABLE_HF_TRANSFER est déprécié (huggingface_hub a basculé son
# transfert accéléré sur le backend Xet) : s'il traîne dans l'environnement
# (image de base RunPod notamment), il ne fait plus rien à part déclencher
# un FutureWarning à chaque import. On le retire et on active son
# remplaçant à la place, avant tout import de faster_whisper/huggingface_hub.
os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

VALID_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
VOLUME_ROOT = "/runpod-volume"
_model_cache = {}


def _model_cache_dir():
    """Cache Hugging Face sur le volume reseau RunPod, s'il est monte.

    Un volume reseau (attache a l'endpoint serverless, voir Reglages ->
    Network Volumes) survit a la recreation d'un worker : le modele n'y est
    telecharge qu'une fois, sans avoir a l'embarquer dans l'image Docker
    (ce qui la rendrait lourde a tirer). Meme convention de chemin que la
    fonctionnalite "Model Caching" native de RunPod. Sans volume attache,
    on retombe sur le cache Hugging Face par defaut de l'image.
    """
    if os.path.isdir(VOLUME_ROOT):
        return os.path.join(VOLUME_ROOT, "huggingface-cache", "hub")
    return None


def get_model(model_size):
    if model_size not in VALID_MODELS:
        model_size = "large-v3"
    if model_size not in _model_cache:
        from faster_whisper import WhisperModel
        print(f"[handler] Chargement du modele '{model_size}' sur GPU (float16)...")
        _model_cache[model_size] = WhisperModel(
            model_size, device="cuda", compute_type="float16",
            download_root=_model_cache_dir(),
        )
        print(f"[handler] Modele '{model_size}' pret.")
    return _model_cache[model_size]


def handler(job):
    job_input = job.get("input", {}) or {}
    audio_b64 = job_input.get("audio_base64")
    model_size = job_input.get("model", "large-v3")
    language = job_input.get("language", "fr") or None
    # Champ optionnel : un worker plus ancien qui ne le connaît pas
    # l'ignorerait de toute façon (job_input.get renvoie None ci-dessous).
    initial_prompt = job_input.get("initial_prompt") or None

    if not audio_b64:
        return {"error": "audio_base64 manquant dans l'entree du job."}

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
        # vad_filter=False pour la meme raison que sur le serveur local :
        # eviter de perdre silencieusement du contenu reel sur de l'audio
        # avec musique de fond / bruit ambiant / parole discrete.
        segments_iter, info = model.transcribe(
            tmp_path,
            language=language,
            vad_filter=False,
            beam_size=5,
            initial_prompt=initial_prompt,
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


runpod.serverless.start({"handler": handler})
