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
import logging
import os
import threading
import time
import uuid

import httpx

from .base import TranscriptionError

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.runpod.io/graphql"
POLL_INTERVAL = 3.0
# Le proxy peut être brièvement en avance sur le conteneur : ces réponses
# HTML/non-JSON ne viennent pas de pod_server.py et peuvent être retentées.
PROXY_RETRY_STATUSES = {404, 502, 503, 504}


def _message_proxy_muet(status_code: int, *, label: str | None = None) -> str:
    """Message après épuisement des tentatives sur un corps illisible.

    Un 404 qui persiste peut simplement venir d'une route pas encore
    propagée par le proxy juste après la création du pod. Un 502/503/504,
    lui, n'a plus cette excuse après trois tentatives espacées : c'est le
    proxy RunPod qui ne trouve plus personne derrière le port du pod, très
    probablement parce que le serveur a planté pendant le chargement du
    modèle (un crash cuDNN natif tue le processus sans lever d'exception
    Python, par exemple) et que le conteneur redémarre.

    ``status_code`` est le statut le plus parlant vu sur l'ensemble des
    tentatives, pas le dernier : pendant un redémarrage du conteneur, le
    proxy passe de 502 (port sans personne derrière) à 404 (route retirée
    le temps que le conteneur se réenregistre), et ce 404 final aurait
    masqué le plantage sous un message de propagation de route.
    """
    suffixe = f" pour le {label}" if label else ""
    if status_code in (502, 503, 504):
        return (
            f"Le serveur du pod ne répond plus derrière le proxy RunPod{suffixe} "
            f"(HTTP {status_code}) — probablement un plantage pendant le "
            f"chargement du modèle. Consultez l'onglet Logs du pod dans la "
            f"console RunPod."
        )
    return f"Réponse du pod{' de secours' if label else ''} illisible{suffixe} (HTTP {status_code})."


def _statut_le_plus_parlant(statuts: list[int]) -> int:
    """Statut à retenir pour le message d'erreur : un 502/503/504 prime sur
    un 404, quel que soit l'ordre d'apparition (voir _message_proxy_muet)."""
    for statut in statuts:
        if statut in (502, 503, 504):
            return statut
    return statuts[-1]


# Chemin de montage d'un volume reseau RunPod, cote pod comme cote
# serverless — meme convention que handler.py/pod_server.py (VOLUME_ROOT) et
# que la fonctionnalite "Model Caching" native de RunPod, pour qu'un seul
# volume beneficie aux deux sans configuration supplementaire.
VOLUME_MOUNT_PATH = "/runpod-volume"


def _hf_token_env(settings=None) -> dict[str, str] | None:
    """Transmet HF_TOKEN au pod créé, s'il est défini côté application.

    Sans jeton, huggingface_hub télécharge le modèle en anonyme et se heurte
    au rate-limit du Hub — plus vite atteint sur un pod fraîchement créé qui
    n'a rien en cache. Contrairement à handler.py (serverless), un pod n'a
    pas de variables d'environnement à lui : elles doivent être fournies à
    la création, d'où ce transfert explicite plutôt qu'un simple `os.getenv`
    côté pod.
    """
    token = getattr(settings, "hf_token", "") or os.environ.get("HF_TOKEN")
    return {"HF_TOKEN": token} if token else None


def _multipart_fields(audio_bytes: bytes, **fields: str) -> dict:
    """Parties du multipart envoyé au pod, texte déclaré en UTF-8.

    Un champ de formulaire ordinaire part sans ``Content-Type`` : le
    parseur ``email`` du pod le lit alors en ASCII et remplace chaque octet
    accentué par U+FFFD. L'amorce du lexique (« Stéphane », « peut-être »…)
    arrivait ainsi truffée de « � », et Whisper recopiait ce caractère dans
    la transcription. Le charset explicite suffit, y compris pour une image
    de pod antérieure à la correction du parseur.
    """
    parts: dict = {
        name: (None, value.encode("utf-8"), "text/plain; charset=utf-8")
        for name, value in fields.items()
    }
    parts["audio"] = ("audio.wav", audio_bytes, "audio/wav")
    return parts


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
        network_volume_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        query = """
        mutation PodCreate($input: PodFindAndDeployOnDemandInput!) {
          podFindAndDeployOnDemand(input: $input) { id env }
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
        if network_volume_id:
            # Epingle le pod au datacenter du volume : RunPod l'exige, au
            # prix d'une disponibilite GPU restreinte a ce seul datacenter
            # (voir le commentaire sur runpod_pod_network_volume_id).
            variables["input"]["networkVolumeId"] = network_volume_id
            variables["input"]["volumeMountPath"] = VOLUME_MOUNT_PATH
        if env:
            variables["input"]["env"] = [
                {"key": key, "value": value} for key, value in env.items()
            ]
        data = self._graphql(query, variables)
        pod_data = data.get("podFindAndDeployOnDemand") or {}
        pod_id = pod_data.get("id")
        if not pod_id:
            raise TranscriptionError(
                "RunPod n'a pas pu déployer de pod de secours (aucun GPU "
                "disponible pour ce type, ou quota atteint)."
            )
        if env:
            returned_env = pod_data.get("env") or []
            token_present = any(str(item).startswith("HF_TOKEN=") for item in returned_env)
            logger.info("Pod RunPod %s créé : HF_TOKEN transmis=%s.", pod_id, token_present)
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
        token_env = _hf_token_env(settings)
        logger.info("Création d'un pod RunPod : HF_TOKEN configuré=%s.", bool(token_env))
        self.pod_id = self._client.create(
            name=name,
            image=settings.runpod_pod_image,
            gpu_type_id=settings.runpod_pod_gpu_type_id,
            container_disk_gb=settings.runpod_pod_container_disk_gb,
            port=settings.runpod_pod_port,
            start_command="python3 -u /pod_server.py",
            network_volume_id=getattr(settings, "runpod_pod_network_volume_id", "") or None,
            env=token_env,
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
        statuts_muets: list[int] = []
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
                statuts_muets.append(response.status_code)
                if response.status_code in PROXY_RETRY_STATUSES and attempt < attempts:
                    time.sleep(POLL_INTERVAL)
                    continue
                raise TranscriptionError(
                    _message_proxy_muet(_statut_le_plus_parlant(statuts_muets), label=label)
                )
            break

        if isinstance(output, dict) and output.get("error"):
            raise TranscriptionError(
                f"Le pod de secours a échoué sur le {label} : {output['error']}"
            )
        return output if isinstance(output, dict) else {}

    def _post_audio(
        self, path: str, audio_bytes: bytes, model: str, language: str | None,
        initial_prompt: str | None, diarize: bool, *, read_timeout: float,
    ) -> tuple[httpx.Response, dict]:
        """Envoie le média entier (multipart) sur ``path`` et rend la réponse
        JSON, en retentant les corps illisibles du proxy (voir
        PROXY_RETRY_STATUSES). Lève sur erreur réseau ou proxy muet."""
        url = f"{self._client.proxy_url(self.pod_id, self.settings.runpod_pod_port)}{path}"
        attempts = 3
        statuts_muets: list[int] = []
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.post(
                    url,
                    files=_multipart_fields(
                        audio_bytes,
                        model=model, language=language or "auto",
                        initial_prompt=initial_prompt or "",
                        diarize=str(bool(diarize)).lower(),
                    ),
                    timeout=httpx.Timeout(120.0, read=read_timeout),
                )
            except httpx.HTTPError as exc:
                raise TranscriptionError(f"Pod RunPod injoignable : {exc}") from exc
            if response.status_code == 413:
                raise TranscriptionError("Le pod a refusé le fichier complet (HTTP 413).")
            try:
                output = response.json()
            except ValueError as exc:
                # Le health-check peut réussir un instant avant que le proxy
                # publie complètement la route POST du pod, ou pendant que
                # le conteneur finit d'initialiser WhisperX.
                statuts_muets.append(response.status_code)
                if response.status_code in PROXY_RETRY_STATUSES and attempt < attempts:
                    time.sleep(POLL_INTERVAL)
                    continue
                raise TranscriptionError(
                    _message_proxy_muet(_statut_le_plus_parlant(statuts_muets))
                ) from exc
            return response, output if isinstance(output, dict) else {}
        raise AssertionError("boucle de tentatives sortie sans réponse")  # pragma: no cover

    def transcribe_audio(
        self, audio_bytes: bytes, model: str, language: str | None, *,
        initial_prompt: str | None = None, diarize: bool = True,
        on_progress=None, should_cancel=None,
    ) -> dict:
        """Envoie le média entier au pod et attend le résultat.

        Le fichier est déposé sur ``/jobs`` (réponse immédiate avec un
        identifiant) puis le résultat est sondé sur ``/jobs/{id}`` : une
        transcription longue ne tient jamais dans une seule réponse HTTP —
        le proxy RunPod abandonne une réponse qui tarde (~100 s), et deux
        gros fichiers ont été perdus ainsi avec l'ancien appel synchrone,
        alors que le pod travaillait toujours (« The read operation timed
        out » après 120 s). Un pod encore sur l'ancienne image (sans
        ``/jobs``) retombe sur ``/transcribe``, avec un délai de lecture
        égal au délai du travail — ce qui ne suffit pas pour un gros
        fichier : il faut alors laisser le pool recréer le pod sur l'image
        à jour.

        ``on_progress(fraction, message)`` et ``should_cancel()`` sont
        optionnels ; l'annulation abandonne l'attente (le pod finit son
        calcul pour rien, mais l'application n'attend plus).
        """
        job_timeout = float(getattr(self.settings, "runpod_pod_job_timeout_seconds", 4 * 3600))
        response, output = self._post_audio(
            "/jobs", audio_bytes, model, language, initial_prompt, diarize,
            read_timeout=120.0,
        )
        if response.status_code == 404 and "job_id" not in output:
            # Ancienne image : pod_server.py ne connaît pas /jobs et répond
            # son propre 404 JSON. On garde l'appel synchrone d'avant.
            logger.warning(
                "Le pod %s tourne sur une image sans /jobs : appel synchrone, "
                "qui échoue sur les gros fichiers — reconstruisez l'image du pod.",
                self.pod_id,
            )
            response, output = self._post_audio(
                "/transcribe", audio_bytes, model, language, initial_prompt, diarize,
                read_timeout=job_timeout,
            )
            if response.status_code >= 400 or output.get("error"):
                raise TranscriptionError(
                    f"Le pod a échoué : {output.get('error') or response.status_code}"
                )
            return output
        if response.status_code >= 400 or output.get("error") or not output.get("job_id"):
            raise TranscriptionError(
                f"Le pod a refusé le travail : {output.get('error') or response.status_code}"
            )
        return self._wait_job(
            str(output["job_id"]), job_timeout,
            on_progress=on_progress, should_cancel=should_cancel,
        )

    def _wait_job(
        self, job_id: str, job_timeout: float, *, on_progress=None, should_cancel=None,
    ) -> dict:
        """Sonde ``/jobs/{id}`` jusqu'au résultat, à l'erreur ou au délai.

        Chaque sonde est une petite requête rapide, loin sous le délai du
        proxy. Un raté ponctuel (réseau, proxy 502/504 pendant une
        seconde) ne fait pas échouer le travail : on retente, et on
        n'abandonne qu'après plusieurs ratés consécutifs — un pod planté
        (conteneur redémarré) répond alors 404 JSON « travail inconnu »,
        signalé comme tel.
        """
        url = f"{self._client.proxy_url(self.pod_id, self.settings.runpod_pod_port)}/jobs/{job_id}"
        debut = time.monotonic()
        deadline = debut + job_timeout
        rates_consecutifs = 0
        max_rates = 10
        derniere_erreur = ""
        while True:
            if should_cancel and should_cancel():
                raise TranscriptionError("Transcription annulée.")
            if time.monotonic() >= deadline:
                raise TranscriptionError(
                    f"Le pod n'a pas rendu le travail {job_id} dans le délai imparti "
                    f"({int(job_timeout)}s, réglage runpod_pod_job_timeout_seconds)."
                )
            try:
                response = self._http.get(url, timeout=httpx.Timeout(30.0, read=30.0))
                statut = response.json()
                if not isinstance(statut, dict):
                    raise ValueError("réponse inattendue")
            except (httpx.HTTPError, ValueError) as exc:
                rates_consecutifs += 1
                derniere_erreur = str(exc) or type(exc).__name__
                if rates_consecutifs >= max_rates:
                    raise TranscriptionError(
                        f"Pod RunPod injoignable pendant le travail {job_id} "
                        f"({rates_consecutifs} sondes ratées d'affilée) : {derniere_erreur}"
                    ) from exc
                time.sleep(POLL_INTERVAL)
                continue
            rates_consecutifs = 0
            if response.status_code == 404:
                raise TranscriptionError(
                    f"Le pod a oublié le travail {job_id} (serveur redémarré ? "
                    f"consultez l'onglet Logs du pod dans la console RunPod) : "
                    f"{statut.get('error') or 'travail inconnu'}"
                )
            if response.status_code >= 400:
                raise TranscriptionError(
                    f"Le pod a échoué : {statut.get('error') or response.status_code}"
                )
            etat = statut.get("status")
            if etat == "done":
                result = statut.get("result")
                if not isinstance(result, dict):
                    raise TranscriptionError(f"Le pod a rendu un résultat illisible pour {job_id}.")
                return result
            if etat == "error":
                raise TranscriptionError(f"Le pod a échoué : {statut.get('error') or 'erreur inconnue'}")
            if on_progress:
                ecoule = int(time.monotonic() - debut)
                # Le pod ne rend pas d'avancement : fraction fixe, seul le
                # message bouge, pour montrer que l'attente est vivante.
                on_progress(
                    0.1,
                    f"Transcription en cours sur le pod ({ecoule // 60} min {ecoule % 60:02d} s)…",
                )
            time.sleep(POLL_INTERVAL)

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
    "runpod_pod_network_volume_id",
)


def _pod_settings_snapshot(settings) -> tuple:
    return tuple(getattr(settings, name, None) for name in _POD_SETTINGS_FIELDS)


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
