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
from .runpod_pod import PodFallbackSession, pod_pool

API_ROOT = "https://api.runpod.ai/v2"
POLL_INTERVAL = 2.0
# Un tronçon de 4 min sur un L4 prend quelques secondes ; cette limite ne sert
# qu'à ne pas attendre indéfiniment un worker qui ne démarre jamais.
MAX_WAIT_PER_CHUNK = 30 * 60


class _ServerlessLaunchTimeout(TranscriptionError):
    """Le premier job est resté en file sans qu'aucun worker ne le prenne.

    Distinct de TranscriptionError seulement pour être intercepté : c'est le
    signal qui déclenche le pod de secours (voir ``transcribe``). Si rien ne
    l'intercepte, il se comporte comme n'importe quelle TranscriptionError.
    """


class RunPodEngine:
    name = "runpod"
    label = "RunPod (GPU, cloud)"

    def is_available(self) -> tuple[bool, str]:
        settings = load_settings()
        if not settings.runpod_api_key:
            return False, "Clé API RunPod à renseigner dans les réglages."
        if settings.runpod_pod_mode == "always":
            if not settings.runpod_pod_image:
                return False, (
                    "Démarrage direct sur pod activé, mais aucune image "
                    "configurée (réglages → Pod RunPod)."
                )
            return True, f"Pod direct sur {settings.runpod_pod_image}."
        if not settings.runpod_endpoint_id:
            return False, "Identifiant de endpoint RunPod à renseigner dans les réglages."
        return True, f"Endpoint {settings.runpod_endpoint_id} configuré."

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

        settings = load_settings()
        if model not in WHISPER_MODELS:
            model = "large-v3"

        pod_direct = settings.runpod_pod_mode == "always"
        if pod_direct and not settings.runpod_pod_image:
            raise TranscriptionError(
                "Démarrage direct sur pod activé, mais aucune image "
                "configurée (réglages → Pod RunPod)."
            )

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

        pod_session: PodFallbackSession | None = None
        try:
            if pod_direct:
                if on_progress:
                    on_progress(0.0, "Préparation du pod GPU…")
                pod_session = pod_pool.acquire(settings)

            with httpx.Client(timeout=httpx.Timeout(120.0, read=120.0)) as client:
                for index, chunk in enumerate(chunks, start=1):
                    if should_cancel is not None and should_cancel():
                        raise TranscriptionError("Transcription annulée.")
                    label = f"tronçon {index}/{len(chunks)}"
                    if on_progress:
                        on_progress(
                            chunk.offset / total if total else 0.0,
                            f"Envoi du {label} vers le GPU…"
                            if pod_session is None
                            else f"Envoi du {label} au pod…",
                        )

                    audio_bytes = chunk.path.read_bytes()

                    if pod_session is not None:
                        output = pod_session.transcribe_chunk(
                            audio_bytes, model, language, label=label,
                            initial_prompt=initial_prompt,
                        )
                    else:
                        payload = {
                            "input": {
                                "audio_base64": base64.b64encode(audio_bytes).decode(
                                    "ascii"
                                ),
                                "model": model,
                                "language": language or None,
                                # Champ optionnel côté worker (handler.py) :
                                # un worker déployé avant son ajout l'ignore.
                                "initial_prompt": initial_prompt or None,
                            }
                        }
                        try:
                            output = self._run_job(
                                client,
                                base_url,
                                headers,
                                payload,
                                label=label,
                                should_cancel=should_cancel,
                                # Seul le premier tronçon détecte un
                                # serverless qui ne démarre aucun worker :
                                # une fois lancé, on lui fait confiance
                                # jusqu'au bout plutôt que de basculer en
                                # cours de route.
                                fail_fast_after=(
                                    settings.runpod_launch_timeout_seconds
                                    if index == 1
                                    else None
                                ),
                            )
                        except _ServerlessLaunchTimeout as exc:
                            if not (
                                settings.runpod_pod_mode == "fallback"
                                and settings.runpod_pod_image
                            ):
                                raise TranscriptionError(
                                    f"{exc} Activez la bascule sur pod (ou le "
                                    "démarrage direct sur pod) dans les "
                                    "réglages pour continuer automatiquement, "
                                    "ou réessayez plus tard."
                                ) from exc
                            if on_progress:
                                on_progress(
                                    0.0,
                                    "Le GPU serverless ne démarre aucun worker — "
                                    "bascule sur un pod de secours…",
                                )
                            pod_session = pod_pool.acquire(settings)
                            output = pod_session.transcribe_chunk(
                                audio_bytes, model, language, label=label,
                                initial_prompt=initial_prompt,
                            )

                    for raw in output.get("segments") or []:
                        text = (raw.get("text") or "").strip()
                        if not text:
                            continue
                        # "confidence" est absent des réponses d'un worker
                        # pas encore republié avec ce champ (voir
                        # handler.py/pod_server.py) : raw.get() renvoie alors
                        # None, donc pas de coloration plutôt qu'une erreur.
                        yield Segment(
                            start=float(raw.get("start", 0.0)),
                            end=float(raw.get("end", 0.0)),
                            text=text,
                            confidence=raw.get("confidence"),
                        ).shifted(chunk.offset)

                    if on_progress and total:
                        done = chunk.offset + chunk.duration
                        on_progress(
                            min(done / total, 1.0),
                            f"{label} transcrit.",
                        )
        finally:
            # Le pod de secours est facturé à la minute dès sa création,
            # donc on ne le garde jamais oisif indéfiniment — mais rien
            # n'empêche le travail suivant dans la file d'en avoir besoin
            # dans la foulée. On le rend donc au pool plutôt que de le
            # fermer ici : c'est lui qui décide, via le délai d'inactivité,
            # du moment où plus personne n'en veut.
            if pod_session is not None:
                pod_pool.release(settings)
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
        fail_fast_after: float | None = None,
    ) -> dict:
        try:
            response = client.post(f"{base_url}/run", headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"RunPod injoignable ({label}) : {exc}") from exc

        if response.status_code == 401:
            raise TranscriptionError("Clé API RunPod refusée (401).")
        if response.status_code == 404:
            raise TranscriptionError(
                f"Endpoint RunPod introuvable (404) à l'adresse {base_url}/run. "
                "Vérifiez que l'identifiant de endpoint copié dans les réglages "
                "correspond bien à celui affiché sur la page de l'endpoint dans "
                "la console RunPod (pas l'URL complète, pas l'id d'un pod), et "
                "que la clé API utilisée appartient au même compte/organisation "
                "que cet endpoint."
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

        start = time.monotonic()
        deadline = start + MAX_WAIT_PER_CHUNK
        state = None
        while True:
            if should_cancel is not None and should_cancel():
                self._cancel_job(client, base_url, headers, job_id)
                raise TranscriptionError("Transcription annulée.")
            # Tant que le job n'a été pris par aucun worker (état encore
            # IN_QUEUE, ou pas encore observé), un délai qui traîne signale
            # une absence de capacité côté RunPod plutôt qu'un calcul lent —
            # c'est ce cas précis que le pod de secours doit couvrir.
            if (
                fail_fast_after is not None
                and state in (None, "IN_QUEUE")
                and time.monotonic() - start > fail_fast_after
            ):
                self._cancel_job(client, base_url, headers, job_id)
                raise _ServerlessLaunchTimeout(
                    f"Aucun worker RunPod n'a pris en charge le {label} après "
                    f"{fail_fast_after:.0f}s d'attente."
                )
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
