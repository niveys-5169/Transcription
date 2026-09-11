"""Pod RunPod de secours, utilisé quand le serverless ne démarre aucun worker.

Le serverless RunPod (``runpod.py``) loue le GPU à la requête et descend à
zéro worker entre deux usages : rien ne tourne, rien n'est facturé. Un pod
RunPod est l'inverse — une machine louée à la minute dès sa création — donc
ce module ne doit jamais laisser un pod vivre plus longtemps que la
transcription qui en a eu besoin. Toute la logique de ce fichier est
organisée autour de cette contrainte : ``PodFallbackSession.close()`` est
appelé par l'appelant dans un ``finally``, et ``close()`` elle-même termine
inconditionnellement le pod (pas un simple « stop », qui laisserait le
disque facturé) dès qu'un ``pod_id`` existe, succès ou échec.

Contrairement au serverless, RunPod ne construit pas cette image
automatiquement depuis le dépôt : il faut la construire et la pousser vers
un registre soi-même (voir le README), puis renseigner sa référence dans les
réglages (``runpod_pod_image``). L'image est la même que celle du worker
serverless (même ``Dockerfile``) ; seule la commande de démarrage change,
pour lancer ``pod_server.py`` (un petit serveur HTTP) plutôt que
``handler.py`` (une boucle de jobs RunPod).
"""
from __future__ import annotations

import base64
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

    def transcribe_chunk(
        self,
        audio_bytes: bytes,
        model: str,
        language: str | None,
        *,
        label: str,
    ) -> dict:
        url = f"{self._client.proxy_url(self.pod_id, self.settings.runpod_pod_port)}/transcribe"
        payload = {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "model": model,
            "language": language or None,
        }
        try:
            response = self._http.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                f"Pod de secours injoignable ({label}) : {exc}"
            ) from exc

        try:
            output = response.json()
        except ValueError:
            raise TranscriptionError(
                f"Réponse du pod de secours illisible pour le {label} "
                f"(HTTP {response.status_code})."
            )

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
