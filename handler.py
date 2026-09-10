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

VALID_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
_model_cache = {}


def get_model(model_size):
    if model_size not in VALID_MODELS:
        model_size = "large-v3"
    if model_size not in _model_cache:
        from faster_whisper import WhisperModel
        print(f"[handler] Chargement du modele '{model_size}' sur GPU (float16)...")
        _model_cache[model_size] = WhisperModel(
            model_size, device="cuda", compute_type="float16"
        )
        print(f"[handler] Modele '{model_size}' pret.")
    return _model_cache[model_size]


def handler(job):
    job_input = job.get("input", {}) or {}
    audio_b64 = job_input.get("audio_base64")
    model_size = job_input.get("model", "large-v3")
    language = job_input.get("language", "fr") or None

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


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
