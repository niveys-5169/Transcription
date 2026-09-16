"""Moteur RunPod : faster-whisper sur un pod GPU à la demande."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .. import media
from ..config import WHISPER_MODELS, load_settings
from .base import CancelCheck, ProgressCallback, Segment, TranscriptionError
from .runpod_pod import pod_pool


class RunPodEngine:
    name = "runpod"
    label = "RunPod (GPU, cloud)"

    def is_available(self) -> tuple[bool, str]:
        settings = load_settings()
        if not settings.runpod_api_key:
            return False, "Clé API RunPod à renseigner dans les réglages."
        if not settings.runpod_pod_image:
            return False, "Image Docker du pod RunPod à renseigner dans les réglages."
        return True, f"Pod RunPod configuré : {settings.runpod_pod_image}."

    def transcribe(self, wav_path: Path, *, model: str, language: str | None,
                   duration: float, workdir: Path, initial_prompt: str | None = None,
                   on_progress: ProgressCallback | None = None,
                   should_cancel: CancelCheck | None = None) -> Iterator[Segment]:
        available, detail = self.is_available()
        if not available:
            raise TranscriptionError(detail)
        settings = load_settings()
        if model not in WHISPER_MODELS:
            model = "large-v3"
        chunk_dir = workdir / "runpod-chunks"
        chunks = media.split_wav(wav_path, float(settings.runpod_chunk_seconds), chunk_dir)
        total = duration or sum(chunk.duration for chunk in chunks)
        session = None
        try:
            if on_progress:
                on_progress(0.0, "Préparation du pod GPU…")
            session = pod_pool.acquire(settings)
            for index, chunk in enumerate(chunks, start=1):
                if should_cancel and should_cancel():
                    raise TranscriptionError("Transcription annulée.")
                label = f"tronçon {index}/{len(chunks)}"
                if on_progress:
                    on_progress(chunk.offset / total if total else 0.0, f"Envoi du {label} au pod…")
                output = session.transcribe_chunk(chunk.path.read_bytes(), model, language,
                                                  label=label, initial_prompt=initial_prompt)
                for raw in output.get("segments") or []:
                    text = (raw.get("text") or "").strip()
                    if text:
                        yield Segment(start=float(raw.get("start", 0.0)), end=float(raw.get("end", 0.0)),
                                      text=text, confidence=raw.get("confidence")).shifted(chunk.offset)
                if on_progress and total:
                    on_progress(min((chunk.offset + chunk.duration) / total, 1.0), f"{label} transcrit.")
        finally:
            if session is not None:
                pod_pool.release(settings)
            for chunk in chunks:
                if chunk.path != wav_path:
                    chunk.path.unlink(missing_ok=True)
            try:
                chunk_dir.rmdir()
            except OSError:
                pass
