"""Moteur local : faster-whisper, sur le processeur (ou le GPU s'il y en a un)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Iterator

from ..config import WHISPER_MODELS
from .base import CancelCheck, ProgressCallback, Segment, TranscriptionError, Word

# Les modèles restent chargés entre deux travaux : recharger « large-v3 »
# prend plusieurs dizaines de secondes.
_MODEL_CACHE: dict[tuple[str, str, str], object] = {}


def _detect_device() -> tuple[str, str]:
    """(device, compute_type) selon le matériel réellement disponible."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


class LocalWhisperEngine:
    name = "local"
    label = "Ordinateur (faster-whisper)"

    def is_available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("faster_whisper") is None:
            return False, (
                "faster-whisper n'est pas installé "
                "(pip install -r requirements-app.txt)."
            )
        device, compute = _detect_device()
        materiel = "GPU (CUDA)" if device == "cuda" else "processeur"
        return True, f"Prêt — calcul sur {materiel} ({compute})."

    def _load(self, model: str):
        import os

        # HF_HUB_ENABLE_HF_TRANSFER est déprécié (huggingface_hub a basculé
        # son transfert accéléré sur le backend Xet) : s'il traîne dans
        # l'environnement, il ne fait plus rien à part déclencher un
        # FutureWarning à chaque import. On le retire et on active son
        # remplaçant à la place.
        os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
        os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

        from faster_whisper import WhisperModel

        if model not in WHISPER_MODELS:
            model = "large-v3"
        device, compute_type = _detect_device()
        key = (model, device, compute_type)
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = WhisperModel(
                model,
                device=device,
                compute_type=compute_type,
                cpu_threads=os.cpu_count() or 4,
            )
        return _MODEL_CACHE[key]

    def transcribe(
        self,
        wav_path: Path,
        *,
        model: str,
        language: str | None,
        duration: float,
        workdir: Path,
        initial_prompt: str | None = None,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> Iterator[Segment]:
        available, detail = self.is_available()
        if not available:
            raise TranscriptionError(detail)

        if on_progress:
            on_progress(0.0, f"Chargement du modèle « {model} »…")
        whisper = self._load(model)

        # vad_filter=False délibérément : le filtre de détection de voix a déjà
        # classé un fichier entier de 10 minutes comme « silence » sur ce
        # projet, renvoyant zéro segment sans la moindre erreur. Mieux vaut
        # traiter un peu de silence que perdre du contenu en silence.
        #
        # initial_prompt : amorce de vocabulaire (sigles, noms propres du
        # domaine — voir app/lexicon). Whisper ne l'applique qu'en début de
        # fichier et l'oublie au fil des tronçons internes ; ça reste la
        # seule prise sur son vocabulaire, pour peu que ça aide.
        segments, info = whisper.transcribe(
            str(wav_path),
            language=language or None,
            vad_filter=False,
            beam_size=5,
            initial_prompt=initial_prompt or None,
            word_timestamps=True,
        )

        total = duration or getattr(info, "duration", 0.0) or 0.0
        for segment in segments:
            if should_cancel is not None and should_cancel():
                raise TranscriptionError("Transcription annulée.")
            text = (segment.text or "").strip()
            if text:
                yield Segment(
                    start=round(segment.start, 3),
                    end=round(segment.end, 3),
                    text=text,
                    confidence=_confidence_from_logprob(
                        getattr(segment, "avg_logprob", None)
                    ),
                    words=[
                        Word(
                            start=round(word.start, 3), end=round(word.end, 3),
                            text=word.word,
                            confidence=_confidence_from_logprob(getattr(word, "probability", None)),
                        )
                        for word in (getattr(segment, "words", None) or [])
                        if getattr(word, "word", "").strip()
                    ] or None,
                )
            if on_progress and total > 0:
                on_progress(
                    min(segment.end / total, 1.0),
                    f"Transcription — {_clock(segment.end)} / {_clock(total)}",
                )


def _confidence_from_logprob(avg_logprob: float | None) -> float | None:
    """Mappage indicatif de avg_logprob (faster-whisper, ~[-1.5, 0]) vers [0, 1].

    Purement informatif pour l'affichage (coloration de relecture) : ne sert
    jamais à modifier le texte transcrit.
    """
    if avg_logprob is None:
        return None
    return round(max(0.0, min(1.0, 1.0 + avg_logprob / 1.5)), 3)


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
