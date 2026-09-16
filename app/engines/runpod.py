"""Moteur RunPod : faster-whisper sur un pod GPU à la demande."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from ..config import WHISPER_MODELS, load_settings
from .base import CancelCheck, ProgressCallback, Segment, TranscriptionError, Word
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
        session = None
        try:
            if on_progress:
                on_progress(0.0, "Préparation du pod GPU…")
            session = pod_pool.acquire(settings)
            if should_cancel and should_cancel():
                raise TranscriptionError("Transcription annulée.")
            if on_progress:
                on_progress(0.05, "Envoi du fichier complet au pod…")
            output = session.transcribe_audio(
                wav_path.read_bytes(), model, language, initial_prompt=initial_prompt,
                diarize=bool(settings.diarization_enabled),
            )
            for raw in output.get("segments") or []:
                text = (raw.get("text") or "").strip()
                if text:
                    yield Segment(
                        start=float(raw.get("start", 0.0)), end=float(raw.get("end", 0.0)),
                        text=text, confidence=raw.get("confidence"), words=[
                            Word(start=float(word.get("start", 0)), end=float(word.get("end", 0)),
                                 text=str(word.get("text") or ""), confidence=word.get("confidence"))
                            for word in (raw.get("words") or []) if str(word.get("text") or "").strip()
                        ] or None,
                        speaker=raw.get("speaker"),
                    )
            if on_progress:
                on_progress(1.0, "Transcription terminée.")
        finally:
            if session is not None:
                pod_pool.release(settings)
