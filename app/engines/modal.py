"""Moteur Modal : faster-whisper / WhisperX sur conteneur GPU serverless Modal.com."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Iterator

import httpx

from ..config import WHISPER_MODELS, load_settings
from .base import CancelCheck, ProgressCallback, Segment, TranscriptionError, Word
from .runpod_pod import POLL_INTERVAL, PROXY_RETRY_STATUSES, _multipart_fields

logger = logging.getLogger(__name__)

LogCallback = Callable[[str], None]
# Lignes du journal serveur recopiées à la fin d'un message d'échec.
ERROR_LOG_TAIL = 15


def _progress_logger(on_progress) -> LogCallback | None:
    """Canal « journal en direct » offert par le pipeline sur son rappel
    d'avancement (attribut ``log``), s'il existe."""
    log = getattr(on_progress, "log", None)
    return log if callable(log) else None


def _normalize_endpoint_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def _decode_json_strict(response: httpx.Response) -> dict:
    """Décode la réponse HTTP explicitement en UTF-8 strict puis parse le JSON."""
    try:
        text = response.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptionError(
            f"Réponse Modal non valide en UTF-8 (HTTP {response.status_code}) : {exc}"
        ) from exc
    if "\ufffd" in text:
        raise TranscriptionError(
            "La réponse du serveur Modal contient le caractère de remplacement "
            "Unicode (U+FFFD) : un caractère accentué a été corrompu."
        )
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"JSON invalide : {exc}") from exc
    return data if isinstance(data, dict) else {}


def _check_no_replacement_char(output: dict) -> None:
    """Vérifie qu'aucun segment ou mot ne contient U+FFFD avant de persister."""
    if "\ufffd" in str(output.get("text") or ""):
        raise TranscriptionError(
            "La transcription Modal contient le caractère de remplacement Unicode (U+FFFD)."
        )
    for idx, seg in enumerate(output.get("segments") or [], start=1):
        if "\ufffd" in str(seg.get("text") or ""):
            raise TranscriptionError(
                f"La transcription Modal contient le caractère de remplacement Unicode "
                f"(U+FFFD, segment {idx})."
            )
        for word in seg.get("words") or []:
            if "\ufffd" in str(word.get("text") or word.get("word") or ""):
                raise TranscriptionError(
                    f"La transcription Modal contient le caractère de remplacement Unicode "
                    f"(U+FFFD, mot du segment {idx})."
                )


class ModalSession:
    """Session HTTP vers un point de terminaison web Modal (``modal_server.py``)."""

    def __init__(self, settings, *, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.base_url = _normalize_endpoint_url(getattr(settings, "modal_endpoint_url", ""))
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(120.0, read=120.0))

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        api_token = (getattr(self.settings, "modal_api_token", "") or "").strip()
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        hf_token = (getattr(self.settings, "hf_token", "") or "").strip()
        if hf_token:
            headers["X-HF-Token"] = hf_token
        return headers

    def wait_ready(self, *, on_progress: ProgressCallback | None = None, should_cancel: CancelCheck | None = None) -> None:
        """Attend que le conteneur GPU Modal réponde sur ``/health`` (cold start inclus)."""
        if not self.base_url:
            raise TranscriptionError("URL du serveur Modal non configurée.")
        url = f"{self.base_url}/health"
        boot_timeout = float(getattr(self.settings, "modal_boot_timeout_seconds", 600))
        started = time.monotonic()
        deadline = started + boot_timeout
        last_error = ""
        first_attempt = True
        log = _progress_logger(on_progress)
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                raise TranscriptionError("Transcription annulée.")
            try:
                response = self._http.get(url, headers=self._headers(), timeout=15.0)
                if response.status_code == 200:
                    if log:
                        log(f"Conteneur Modal prêt ({int(time.monotonic() - started)} s).")
                    return
                if response.status_code in (401, 403):
                    raise TranscriptionError(
                        f"Jeton d'authentification Modal refusé (HTTP {response.status_code})."
                    )
                last_error = f"HTTP {response.status_code}"
            except TranscriptionError:
                raise
            except httpx.HTTPError as exc:
                last_error = str(exc) or type(exc).__name__
            if first_attempt:
                if on_progress:
                    on_progress(0.02, "Démarrage du conteneur GPU Modal (cold start)…")
                if log:
                    log(f"Conteneur pas encore prêt ({last_error}) : démarrage à froid en cours…")
                first_attempt = False
            time.sleep(POLL_INTERVAL)
        raise TranscriptionError(
            f"Le serveur Modal ({self.base_url}) n'a pas répondu dans le délai imparti "
            f"({int(boot_timeout)}s)"
            + (f" — dernière erreur : {last_error}" if last_error else "")
            + "."
        )

    def _post_audio(
        self,
        path: str,
        audio_bytes: bytes,
        model: str,
        language: str | None,
        initial_prompt: str | None,
        diarize: bool,
        *,
        read_timeout: float,
    ) -> tuple[httpx.Response, dict]:
        url = f"{self.base_url}{path}"
        attempts = 3
        statuts_muets: list[int] = []
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.post(
                    url,
                    headers=self._headers(),
                    files=_multipart_fields(
                        audio_bytes,
                        model=model,
                        language=language or "auto",
                        initial_prompt=initial_prompt or "",
                        diarize=str(bool(diarize)).lower(),
                    ),
                    timeout=httpx.Timeout(120.0, read=read_timeout),
                )
            except httpx.TransportError as exc:
                if attempt < attempts:
                    time.sleep(POLL_INTERVAL)
                    continue
                raise TranscriptionError(f"Serveur Modal injoignable : {exc}") from exc
            except httpx.HTTPError as exc:
                raise TranscriptionError(f"Serveur Modal injoignable : {exc}") from exc

            if response.status_code in (401, 403):
                raise TranscriptionError(
                    f"Jeton d'authentification Modal refusé (HTTP {response.status_code})."
                )
            if response.status_code == 413:
                raise TranscriptionError("Le serveur Modal a refusé le fichier complet (HTTP 413).")

            try:
                output = _decode_json_strict(response)
            except ValueError as exc:
                statuts_muets.append(response.status_code)
                if response.status_code in PROXY_RETRY_STATUSES and attempt < attempts:
                    time.sleep(POLL_INTERVAL)
                    continue
                raise TranscriptionError(
                    f"Réponse du serveur Modal illisible (HTTP {statuts_muets[-1]})."
                ) from exc
            return response, output
        raise AssertionError("boucle de tentatives sortie sans réponse")  # pragma: no cover

    def transcribe_audio(
        self,
        audio_bytes: bytes,
        model: str,
        language: str | None,
        *,
        initial_prompt: str | None = None,
        diarize: bool = True,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> dict:
        job_timeout = float(getattr(self.settings, "modal_job_timeout_seconds", 4 * 3600))
        response, output = self._post_audio(
            "/jobs",
            audio_bytes,
            model,
            language,
            initial_prompt,
            diarize,
            read_timeout=120.0,
        )
        if response.status_code == 404 and "job_id" not in output:
            response, output = self._post_audio(
                "/transcribe",
                audio_bytes,
                model,
                language,
                initial_prompt,
                diarize,
                read_timeout=job_timeout,
            )
            if response.status_code >= 400 or output.get("error"):
                raise TranscriptionError(
                    f"Le serveur Modal a échoué : {output.get('error') or response.status_code}"
                )
            _check_no_replacement_char(output)
            return output
        if response.status_code >= 400 or output.get("error") or not output.get("job_id"):
            raise TranscriptionError(
                f"Le serveur Modal a refusé le travail : {output.get('error') or response.status_code}"
            )
        log = _progress_logger(on_progress)
        if log:
            log(f"Travail Modal {output['job_id']} accepté.")
        result = self._wait_job(
            str(output["job_id"]),
            job_timeout,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
        _check_no_replacement_char(result)
        return result

    def _wait_job(
        self,
        job_id: str,
        job_timeout: float,
        *,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> dict:
        url = f"{self.base_url}/jobs/{job_id}"
        debut = time.monotonic()
        deadline = debut + job_timeout
        rates_consecutifs = 0
        max_rates = 10
        derniere_erreur = ""
        log = _progress_logger(on_progress)
        journal: list[str] = []
        lignes_lues = 0

        def avec_journal(message: str) -> str:
            if not journal:
                return message
            fin = journal[-ERROR_LOG_TAIL:]
            return message + "\n\nDernières lignes du serveur Modal :\n" + "\n".join(fin)

        while True:
            if should_cancel and should_cancel():
                raise TranscriptionError("Transcription annulée.")
            if time.monotonic() >= deadline:
                raise TranscriptionError(
                    f"Le serveur Modal n'a pas rendu le travail {job_id} dans le délai imparti "
                    f"({int(job_timeout)}s)."
                )
            try:
                response = self._http.get(
                    url,
                    headers=self._headers(),
                    params={"since": lignes_lues},
                    timeout=httpx.Timeout(30.0, read=30.0),
                )
                statut = _decode_json_strict(response)
            except TranscriptionError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                rates_consecutifs += 1
                derniere_erreur = str(exc) or type(exc).__name__
                if rates_consecutifs >= max_rates:
                    raise TranscriptionError(
                        f"Serveur Modal injoignable pendant le travail {job_id} "
                        f"({rates_consecutifs} sondes ratées d'affilée) : {derniere_erreur}"
                    ) from exc
                time.sleep(POLL_INTERVAL)
                continue
            rates_consecutifs = 0
            for entree in statut.get("logs") or []:
                message = str(entree.get("msg") if isinstance(entree, dict) else entree)
                journal.append(message)
                logger.info("Modal %s : %s", job_id, message)
                if log:
                    log(message)
            if isinstance(statut.get("log_total"), int):
                lignes_lues = statut["log_total"]
            if response.status_code == 404:
                raise TranscriptionError(
                    f"Le serveur Modal a oublié le travail {job_id} (conteneur redémarré ?) : "
                    f"{statut.get('error') or 'travail inconnu'}"
                )
            if response.status_code >= 400:
                raise TranscriptionError(avec_journal(
                    f"Le serveur Modal a échoué : {statut.get('error') or response.status_code}"
                ))
            etat = statut.get("status")
            if etat == "done":
                result = statut.get("result")
                if not isinstance(result, dict):
                    raise TranscriptionError(
                        f"Le serveur Modal a rendu un résultat illisible pour {job_id}."
                    )
                return result
            if etat == "error":
                raise TranscriptionError(avec_journal(
                    f"Le serveur Modal a échoué : {statut.get('error') or 'erreur inconnue'}"
                ))
            if on_progress:
                ecoule = int(time.monotonic() - debut)
                etape = statut.get("stage") or (
                    "en attente du GPU" if etat == "queued" else "transcription en cours"
                )
                avancement = statut.get("progress")
                fraction = (
                    0.1 + 0.85 * float(avancement)
                    if isinstance(avancement, (int, float)) else 0.1
                )
                on_progress(
                    fraction,
                    f"Modal · {etape} ({ecoule // 60} min {ecoule % 60:02d} s)…",
                )
            time.sleep(POLL_INTERVAL)


def check_endpoint(settings, *, http_client: httpx.Client | None = None) -> tuple[bool, str]:
    """Vérifie que le serveur Modal répond sur ``/health``."""
    url = _normalize_endpoint_url(getattr(settings, "modal_endpoint_url", ""))
    if not url:
        return False, "Renseignez d'abord l'URL du point de terminaison Modal."
    if not url.startswith(("https://", "http://")):
        return False, "L'URL Modal doit commencer par https:// (ou http://)."
    session = ModalSession(settings, http_client=http_client)
    # Un démarrage à froid peut durer plusieurs minutes quand Modal attend un GPU
    # libre : on réessaie jusqu'à ``boot_timeout`` au lieu d'échouer après 30 s.
    boot_timeout = float(getattr(settings, "modal_check_timeout_seconds", 240))
    deadline = time.monotonic() + boot_timeout
    last_error = ""
    try:
        while True:
            try:
                response = session._http.get(
                    f"{session.base_url}/health",
                    headers=session._headers(),
                    timeout=30.0,
                )
                if response.status_code == 200:
                    return True, f"Serveur Modal opérationnel ({session.base_url})."
                if response.status_code in (401, 403):
                    return False, (
                        f"Jeton d'authentification Modal refusé (HTTP {response.status_code})."
                    )
                if response.status_code not in PROXY_RETRY_STATUSES:
                    return False, f"Le serveur Modal a répondu HTTP {response.status_code}."
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc) or type(exc).__name__
            if time.monotonic() >= deadline:
                return False, (
                    f"Serveur Modal injoignable après {int(boot_timeout)} s "
                    f"(démarrage à froid ou GPU indisponible) : {last_error}"
                )
            time.sleep(POLL_INTERVAL)
    finally:
        session.close()


class ModalEngine:
    name = "modal"
    label = "Modal (GPU, cloud)"

    def is_available(self) -> tuple[bool, str]:
        settings = load_settings()
        url = _normalize_endpoint_url(getattr(settings, "modal_endpoint_url", ""))
        if not url:
            return False, (
                "URL Modal à renseigner dans les réglages "
                "(déployez avec « modal deploy modal_server.py »)."
            )
        if not url.startswith(("https://", "http://")):
            return False, "URL Modal invalide : elle doit commencer par https:// (ou http://)."
        return True, f"Serveur Modal configuré : {url}."

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
        session = ModalSession(settings)
        log = _progress_logger(on_progress)
        try:
            if on_progress:
                on_progress(0.0, "Connexion au conteneur GPU Modal…")
            if log:
                log(f"Connexion à {session.base_url}…")
            session.wait_ready(on_progress=on_progress, should_cancel=should_cancel)
            if should_cancel and should_cancel():
                raise TranscriptionError("Transcription annulée.")
            if on_progress:
                on_progress(0.05, "Envoi du fichier complet à Modal…")
            if log:
                log(f"Envoi du fichier audio ({wav_path.stat().st_size / 1e6:.1f} Mo)…")
            output = session.transcribe_audio(
                wav_path.read_bytes(),
                model,
                language,
                initial_prompt=initial_prompt,
                diarize=bool(settings.diarization_enabled),
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            for raw in output.get("segments") or []:
                text = (raw.get("text") or "").strip()
                if text:
                    yield Segment(
                        start=float(raw.get("start", 0.0)),
                        end=float(raw.get("end", 0.0)),
                        text=text,
                        confidence=raw.get("confidence"),
                        words=[
                            Word(
                                start=float(word.get("start", 0)),
                                end=float(word.get("end", 0)),
                                text=str(word.get("text") or ""),
                                confidence=word.get("confidence"),
                            )
                            for word in (raw.get("words") or [])
                            if str(word.get("text") or "").strip()
                        ]
                        or None,
                        speaker=raw.get("speaker"),
                    )
            if on_progress:
                on_progress(1.0, "Transcription terminée.")
        finally:
            session.close()
