"""Moteur RunPod : faster-whisper sur GPU, en serverless.

Le point de terminaison est celui déployé depuis ce dépôt (``handler.py`` +
``Dockerfile``). L'API ``/run`` de RunPod plafonne la charge utile d'un appel
à environ 10 Mo : un WAV 16 kHz mono 16 bits encodé en base64 pèse ~42 ko par
seconde d'audio, donc un seul appel ne peut porter que ~4 minutes de cours.
Le fichier est donc découpé sur des silences, chaque tronçon est transcrit
séparément, et les horodatages sont recalés sur le fichier d'origine.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Iterator

import httpx

from ..config import WHISPER_MODELS, load_settings
from .. import media
from .base import CancelCheck, ProgressCallback, Segment, TranscriptionError

API_ROOT = "https://api.runpod.ai/v2"
POLL_INTERVAL = 2.0
# Un tronçon de 4 min sur un L4 prend quelques secondes ; cette limite ne sert
# qu'à ne pas attendre indéfiniment un worker qui ne démarre jamais.
MAX_WAIT_PER_CHUNK = 30 * 60


class RunPodEngine:
    name = "runpod"
    label = "RunPod (GPU, cloud)"

    def is_available(self) -> tuple[bool, str]:
        settings = load_settings()
        if not settings.runpod_api_key or not settings.runpod_endpoint_id:
            return False, (
                "Clé API et identifiant de endpoint RunPod à renseigner dans "
                "les réglages."
            )
        return True, f"Endpoint {settings.runpod_endpoint_id} configuré."

    def transcribe(
        self,
        wav_path: Path,
        *,
        model: str,
        language: str | None,
        duration: float,
        workdir: Path,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> Iterator[Segment]:
        available, detail = self.is_available()
        if not available:
            raise TranscriptionError(detail)

        settings = load_settings()
        if model not in WHISPER_MODELS:
            model = "large-v3"

        chunk_dir = workdir / "runpod-chunks"
        chunks = media.split_wav(
            wav_path, float(settings.runpod_chunk_seconds), chunk_dir
        )
        total = duration or sum(chunk.duration for chunk in chunks)

        headers = {
            "Authorization": f"Bearer {settings.runpod_api_key}",
            "Content-Type": "application/json",
        }
        base_url = f"{API_ROOT}/{settings.runpod_endpoint_id}"

        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, read=120.0)) as client:
                for index, chunk in enumerate(chunks, start=1):
                    if should_cancel is not None and should_cancel():
                        raise TranscriptionError("Transcription annulée.")
                    if on_progress:
                        on_progress(
                            chunk.offset / total if total else 0.0,
                            f"Envoi du tronçon {index}/{len(chunks)} vers le GPU…",
                        )

                    payload = {
                        "input": {
                            "audio_base64": base64.b64encode(
                                chunk.path.read_bytes()
                            ).decode("ascii"),
                            "model": model,
                            "language": language or None,
                        }
                    }
                    output = self._run_job(
                        client,
                        base_url,
                        headers,
                        payload,
                        label=f"tronçon {index}/{len(chunks)}",
                        should_cancel=should_cancel,
                    )

                    for raw in output.get("segments") or []:
                        text = (raw.get("text") or "").strip()
                        if not text:
                            continue
                        yield Segment(
                            start=float(raw.get("start", 0.0)),
                            end=float(raw.get("end", 0.0)),
                            text=text,
                        ).shifted(chunk.offset)

                    if on_progress and total:
                        done = chunk.offset + chunk.duration
                        on_progress(
                            min(done / total, 1.0),
                            f"Tronçon {index}/{len(chunks)} transcrit.",
                        )
        finally:
            self._cleanup(chunks, wav_path, chunk_dir)

    def _run_job(
        self,
        client: httpx.Client,
        base_url: str,
        headers: dict,
        payload: dict,
        *,
        label: str,
        should_cancel: CancelCheck | None,
    ) -> dict:
        try:
            response = client.post(f"{base_url}/run", headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"RunPod injoignable ({label}) : {exc}") from exc

        if response.status_code == 401:
            raise TranscriptionError("Clé API RunPod refusée (401).")
        if response.status_code == 404:
            raise TranscriptionError(
                "Endpoint RunPod introuvable (404) : vérifiez l'identifiant."
            )
        if response.status_code >= 400:
            raise TranscriptionError(
                f"RunPod a refusé le {label} (HTTP {response.status_code}) : "
                f"{response.text[:300]}"
            )

        job_id = response.json().get("id")
        if not job_id:
            raise TranscriptionError(
                f"Réponse RunPod inattendue pour le {label} : {response.text[:300]}"
            )

        deadline = time.monotonic() + MAX_WAIT_PER_CHUNK
        while True:
            if should_cancel is not None and should_cancel():
                self._cancel_job(client, base_url, headers, job_id)
                raise TranscriptionError("Transcription annulée.")
            if time.monotonic() > deadline:
                self._cancel_job(client, base_url, headers, job_id)
                raise TranscriptionError(
                    f"Le {label} n'a pas abouti dans le temps imparti "
                    f"({MAX_WAIT_PER_CHUNK // 60} min)."
                )

            time.sleep(POLL_INTERVAL)
            try:
                status = client.get(f"{base_url}/status/{job_id}", headers=headers)
                body = status.json()
            except httpx.HTTPError as exc:
                raise TranscriptionError(
                    f"Suivi du {label} impossible : {exc}"
                ) from exc

            state = body.get("status")
            if state == "COMPLETED":
                output = body.get("output") or {}
                if isinstance(output, dict) and output.get("error"):
                    raise TranscriptionError(
                        f"Le worker RunPod a échoué sur le {label} : "
                        f"{output['error']}"
                    )
                return output if isinstance(output, dict) else {}
            if state in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                raise TranscriptionError(
                    f"Le {label} s'est terminé en échec côté RunPod ({state}) : "
                    f"{str(body.get('error'))[:300]}"
                )

    @staticmethod
    def _cancel_job(
        client: httpx.Client, base_url: str, headers: dict, job_id: str
    ) -> None:
        try:
            client.post(f"{base_url}/cancel/{job_id}", headers=headers)
        except httpx.HTTPError:
            pass  # L'annulation est un « best effort ».

    @staticmethod
    def _cleanup(chunks, wav_path: Path, chunk_dir: Path) -> None:
        for chunk in chunks:
            if chunk.path != wav_path:
                chunk.path.unlink(missing_ok=True)
        try:
            chunk_dir.rmdir()
        except OSError:
            pass
