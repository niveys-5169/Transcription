"""Pod RunPod de secours, utilisé quand le serverless ne démarre aucun worker.

Le serverless RunPod (``runpod.py``) loue le GPU à la requête et descend à
zéro worker entre deux usages : rien ne tourne, rien n'est facturé. Un pod
RunPod est l'inverse — une machine louée à la minute dès sa création — donc
ce module ne doit jamais laisser un pod vivre plus longtemps que nécessaire.
Toute la logique de ce fichier est organisée autour de cette contrainte :
``PodFallbackSession.close()`` termine inconditionnellement le pod (pas un
simple « stop », qui laisserait le disque facturé) dès qu'un ``pod_id``
existe, succès ou échec.

``PodPool`` (plus bas) décide *quand* fermer : le pipeline ne traite qu'un
travail à la fois (voir ``pipeline.py``), donc quand plusieurs fichiers
s'enchaînent, recréer un pod pour chacun rechargerait l'image et le modèle
Whisper à chaque fois pour rien. Le pool garde le pod du travail précédent
chaud pour le suivant, et ne le ferme (via ``close()``) que si personne n'en
a redemandé un pendant ``runpod_pod_idle_timeout_seconds`` — c'est
``PodPool``, pas l'appelant, qui décide du moment de fermeture.

Contrairement au serverless, RunPod ne construit pas cette image
automatiquement depuis le dépôt : il faut la construire et la pousser vers
un registre soi-même (voir le README), puis renseigner sa référence dans les
réglages (``runpod_pod_image``). L'image est la même que celle du worker
serverless (même ``Dockerfile``) ; seule la commande de démarrage change,
pour lancer ``pod_server.py`` (un petit serveur HTTP) plutôt que
``handler.py`` (une boucle de jobs RunPod).
"""
from __future__ import annotations

import atexit
import base64
import threading
import time
import uuid

import httpx

from .base import TranscriptionError

GRAPHQL_URL = "https://api.runpod.io/graphql"
POLL_INTERVAL = 3.0


class RunPodPodClient:
    """Fine couche autour de l'API GraphQL RunPod pour les pods à la demande."""

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _graphql(self, query: str, variables: dict) -> dict:
        try:
            response = self._client.post(
                GRAPHQL_URL,
                params={"api_key": self.api_key},
                json={"query": query, "variables": variables},
            )
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"API RunPod (pods) injoignable : {exc}") from exc

        if response.status_code == 401:
            raise TranscriptionError("Clé API RunPod refusée (401) pour le pod de secours.")
        if response.status_code >= 400:
            raise TranscriptionError(
                f"API RunPod (pods) en erreur (HTTP {response.status_code}) : "
                f"{response.text[:300]}"
            )

        body = response.json()
        if body.get("errors"):
            messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
            raise TranscriptionError(f"API RunPod (pods) : {messages}")
        return body.get("data") or {}

    def create(
        self,
        *,
        name: str,
        image: str,
        gpu_type_id: str,
        container_disk_gb: int,
        port: int,
        start_command: str,
    ) -> str:
        query = """
        mutation PodCreate($input: PodFindAndDeployOnDemandInput!) {
          podFindAndDeployOnDemand(input: $input) { id }
        }
        """
        variables = {
            "input": {
                "cloudType": "ALL",
                "gpuCount": 1,
                "gpuTypeId": gpu_type_id,
                "name": name,
                "imageName": image,
                "dockerArgs": start_command,
                "ports": f"{port}/http",
                "containerDiskInGb": container_disk_gb,
                "volumeInGb": 0,
            }
        }
        data = self._graphql(query, variables)
        pod_id = (data.get("podFindAndDeployOnDemand") or {}).get("id")
        if not pod_id:
            raise TranscriptionError(
                "RunPod n'a pas pu déployer de pod de secours (aucun GPU "
                "disponible pour ce type, ou quota atteint)."
            )
        return pod_id

    def terminate(self, pod_id: str) -> None:
        """Détruit le pod. Best-effort : on ne relance jamais après un échec
        ici, RunPod facture au pire quelques minutes de plus qu'attendu, ce
        qui reste sans commune mesure avec un pod laissé vivant."""
        try:
            self._graphql(
                "mutation PodTerminate($input: PodTerminateInput!) { podTerminate(input: $input) }",
                {"input": {"podId": pod_id}},
            )
        except TranscriptionError:
            pass

    def proxy_url(self, pod_id: str, port: int) -> str:
        return f"https://{pod_id}-{port}.proxy.runpod.net"


class PodFallbackSession:
    """Un pod créé pour la durée d'une seule transcription, puis détruit.

    Usage :
        session = PodFallbackSession(settings)
        try:
            session.start()
            output = session.transcribe_chunk(audio_bytes, model, language, label="…")
        finally:
            session.close()  # termine le pod si `start()` en a créé un,
                              # même si `start()` ou `transcribe_chunk` ont levé
    """

    def __init__(self, settings, *, client: RunPodPodClient | None = None) -> None:
        self.settings = settings
        self._client = client or RunPodPodClient(settings.runpod_api_key)
        self.pod_id: str | None = None
        self._closed = False
        self._http = httpx.Client(timeout=httpx.Timeout(120.0, read=120.0))

    def start(self) -> None:
        settings = self.settings
        if not settings.runpod_pod_image:
            raise TranscriptionError(
                "Pod de secours activé mais aucune image configurée "
                "(réglages → Pod de secours)."
            )

        name = f"transcription-fallback-{uuid.uuid4().hex[:8]}"
        self.pod_id = self._client.create(
            name=name,
            image=settings.runpod_pod_image,
            gpu_type_id=settings.runpod_pod_gpu_type_id,
            container_disk_gb=settings.runpod_pod_container_disk_gb,
            port=settings.runpod_pod_port,
            start_command="python3 -u /pod_server.py",
        )
        self._wait_ready()

    def _wait_ready(self) -> None:
        url = f"{self._client.proxy_url(self.pod_id, self.settings.runpod_pod_port)}/health"
        deadline = time.monotonic() + self.settings.runpod_pod_boot_timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                response = self._http.get(url, timeout=5.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError as exc:
                last_error = str(exc)
            time.sleep(POLL_INTERVAL)
        raise TranscriptionError(
            f"Le pod de secours ({self.pod_id}) n'a pas démarré dans le temps "
            f"imparti ({self.settings.runpod_pod_boot_timeout_seconds}s)"
            + (f" — dernière erreur : {last_error}" if last_error else "") + "."
        )

    def is_healthy(self, *, timeout: float = 5.0) -> bool:
        """Sonde ``/health`` une fois, sans retenter.

        Utilisé par ``PodPool`` avant de réutiliser un pod pour un nouveau
        travail, pour ne pas hériter d'un pod mort entre deux transcriptions
        (terminé manuellement dans la console RunPod, préemption, etc.) —
        auquel cas le pool en recrée un plutôt que d'envoyer le travail dans
        le vide.
        """
        if self.pod_id is None:
            return False
        url = f"{self._client.proxy_url(self.pod_id, self.settings.runpod_pod_port)}/health"
        try:
            response = self._http.get(url, timeout=timeout)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def transcribe_chunk(
        self,
        audio_bytes: bytes,
        model: str,
        language: str | None,
        *,
        label: str,
        initial_prompt: str | None = None,
    ) -> dict:
        url = f"{self._client.proxy_url(self.pod_id, self.settings.runpod_pod_port)}/transcribe"
        payload = {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "model": model,
            "language": language or None,
            "initial_prompt": initial_prompt or None,
        }
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.post(url, json=payload)
            except httpx.HTTPError as exc:
                raise TranscriptionError(
                    f"Pod de secours injoignable ({label}) : {exc}"
                ) from exc

            try:
                output = response.json()
            except ValueError:
                # pod_server.py répond toujours en JSON, y compris pour ses
                # propres 404 (mauvais chemin). Un corps illisible vient donc
                # du proxy RunPod, pas de l'application : juste après la
                # création du pod, sa route peut ne pas être encore
                # entièrement propagée, même si /health avait déjà répondu.
                # On retente avant d'abandonner.
                if response.status_code == 404 and attempt < attempts:
                    time.sleep(POLL_INTERVAL)
                    continue
                raise TranscriptionError(
                    f"Réponse du pod de secours illisible pour le {label} "
                    f"(HTTP {response.status_code})."
                )
            break

        if isinstance(output, dict) and output.get("error"):
            raise TranscriptionError(
                f"Le pod de secours a échoué sur le {label} : {output['error']}"
            )
        return output if isinstance(output, dict) else {}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.pod_id:
                self._client.terminate(self.pod_id)
        finally:
            self._http.close()
            self._client.close()


# Réglages qui définissent « le même pod » : si l'un d'eux change, le pod
# existant ne convient plus pour le travail suivant et doit être recréé.
_POD_SETTINGS_FIELDS = (
    "runpod_pod_image",
    "runpod_pod_gpu_type_id",
    "runpod_pod_container_disk_gb",
    "runpod_pod_port",
)


def _pod_settings_snapshot(settings) -> tuple:
    return tuple(getattr(settings, name) for name in _POD_SETTINGS_FIELDS)


class PodPool:
    """Garde un pod de secours chaud entre plusieurs transcriptions.

    Le pipeline ne traite qu'un travail à la fois (un seul thread dépile la
    file, voir ``pipeline.py``) : un seul pod à la fois suffit donc, pas
    besoin de gérer plusieurs pods en parallèle — seulement leur durée de
    vie. ``acquire()`` rend le pod du travail précédent s'il correspond
    toujours aux réglages actuels et répond encore ; sinon il en crée un
    nouveau. ``release()`` ne ferme rien tout de suite : elle arme un délai
    d'inactivité, pour que le travail suivant dans la file (s'il arrive
    avant l'échéance) retrouve le même pod plutôt que d'en attendre un
    nouveau.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: PodFallbackSession | None = None
        self._snapshot: tuple | None = None
        self._timer: threading.Timer | None = None
        atexit.register(self.shutdown)

    def acquire(self, settings) -> PodFallbackSession:
        with self._lock:
            self._cancel_timer_locked()
            snapshot = _pod_settings_snapshot(settings)
            if self._session is not None:
                if snapshot == self._snapshot and self._session.is_healthy():
                    return self._session
                self._close_locked()

            session = PodFallbackSession(settings)
            try:
                session.start()
            except Exception:
                session.close()
                raise
            self._session = session
            self._snapshot = snapshot
            return session

    def release(self, settings) -> None:
        """Signale que le travail en cours n'a plus besoin du pod.

        Ne le ferme pas : elle programme sa fermeture après
        ``runpod_pod_idle_timeout_seconds``, annulée par le prochain
        ``acquire()`` s'il arrive avant.
        """
        with self._lock:
            if self._session is None:
                return
            self._cancel_timer_locked()
            timeout = max(
                0, int(getattr(settings, "runpod_pod_idle_timeout_seconds", 300))
            )
            timer = threading.Timer(timeout, self._on_idle_timeout)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _on_idle_timeout(self) -> None:
        with self._lock:
            self._timer = None
            self._close_locked()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _close_locked(self) -> None:
        if self._session is not None:
            self._session.close()
        self._session = None
        self._snapshot = None

    def shutdown(self) -> None:
        """Ferme le pod immédiatement, sans attendre le délai d'inactivité.

        Enregistrée via ``atexit`` pour ne pas laisser un pod facturé si le
        processus s'arrête proprement pendant la fenêtre d'inactivité.
        """
        with self._lock:
            self._cancel_timer_locked()
            self._close_locked()


pod_pool = PodPool()
