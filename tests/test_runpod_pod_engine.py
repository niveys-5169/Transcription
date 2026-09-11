"""Tests du client de pods RunPod (``app/engines/runpod_pod.py``).

Aucun réseau réel : l'API GraphQL des pods et le proxy HTTP du pod sont
simulés via ``httpx.MockTransport``. Le point sensible, comme pour le
moteur, est que ``PodFallbackSession.close()`` détruit bien le pod dès qu'un
``pod_id`` existe — y compris quand ``start()`` échoue en cours de route.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.engines.base import TranscriptionError
from app.engines.runpod_pod import GRAPHQL_URL, PodFallbackSession, RunPodPodClient


class _Settings:
    runpod_api_key = "rpa_test"
    runpod_pod_image = "repo/transcription-pod:latest"
    runpod_pod_gpu_type_id = "NVIDIA L4"
    runpod_pod_container_disk_gb = 20
    runpod_pod_port = 8000
    runpod_pod_boot_timeout_seconds = 5


def _mock_client(handler) -> RunPodPodClient:
    client = RunPodPodClient("rpa_test")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


# ---------------------------------------------------------- RunPodPodClient


def test_create_renvoie_l_identifiant_du_pod():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(GRAPHQL_URL)
        body = json.loads(request.content)
        assert "podFindAndDeployOnDemand" in body["query"]
        entree = body["variables"]["input"]
        assert entree["imageName"] == "repo/transcription-pod:latest"
        assert entree["dockerArgs"] == "python3 -u /pod_server.py"
        assert entree["ports"] == "8000/http"
        return httpx.Response(
            200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}}
        )

    client = _mock_client(handler)
    pod_id = client.create(
        name="test", image="repo/transcription-pod:latest",
        gpu_type_id="NVIDIA L4", container_disk_gb=20, port=8000,
        start_command="python3 -u /pod_server.py",
    )
    assert pod_id == "pod123"


def test_create_leve_une_erreur_si_aucun_gpu_disponible():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": None}})

    client = _mock_client(handler)
    with pytest.raises(TranscriptionError, match="aucun GPU"):
        client.create(
            name="test", image="img", gpu_type_id="NVIDIA L4",
            container_disk_gb=20, port=8000, start_command="cmd",
        )


def test_create_leve_une_erreur_sur_reponse_graphql_en_erreur():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "quota atteint"}]})

    client = _mock_client(handler)
    with pytest.raises(TranscriptionError, match="quota atteint"):
        client.create(
            name="test", image="img", gpu_type_id="NVIDIA L4",
            container_disk_gb=20, port=8000, start_command="cmd",
        )


def test_terminate_est_best_effort_et_avale_les_erreurs():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oups")

    client = _mock_client(handler)
    client.terminate("pod123")  # ne doit pas lever


def test_terminate_envoie_bien_le_bon_pod_id():
    appels = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        appels.append(body["variables"]["input"]["podId"])
        return httpx.Response(200, json={"data": {"podTerminate": True}})

    client = _mock_client(handler)
    client.terminate("pod123")
    assert appels == ["pod123"]


def test_proxy_url_suit_la_convention_runpod():
    client = RunPodPodClient("rpa_test")
    assert client.proxy_url("pod123", 8000) == "https://pod123-8000.proxy.runpod.net"


# -------------------------------------------------------- PodFallbackSession


def _session(settings, handler_graphql, handler_http) -> PodFallbackSession:
    pod_client = _mock_client(handler_graphql)
    session = PodFallbackSession(settings, client=pod_client)
    session._http = httpx.Client(transport=httpx.MockTransport(handler_http))
    return session


def test_start_attend_que_le_health_check_reponde(monkeypatch):
    creations = []

    def graphql(request: httpx.Request) -> httpx.Response:
        creations.append(1)
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}})

    reponses = iter([httpx.Response(503), httpx.Response(200, json={"status": "ok"})])

    def http(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return next(reponses)

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)

    session = _session(_Settings(), graphql, http)
    session.start()

    assert session.pod_id == "pod123"
    assert creations == [1]


def test_start_leve_une_erreur_si_le_pod_ne_repond_jamais(monkeypatch):
    def graphql(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}})

    def http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    # Horloge qui dépasse tout de suite le délai imparti (5s ici).
    horloges = iter([0.0, 10.0])
    monkeypatch.setattr(
        "app.engines.runpod_pod.time.monotonic", lambda: next(horloges, 999.0)
    )

    settings = _Settings()
    session = _session(settings, graphql, http)

    with pytest.raises(TranscriptionError, match="pod123"):
        session.start()

    # Le pod a bien été créé malgré l'échec du démarrage : il doit donc être
    # détruit par close(), pas laissé à tourner.
    assert session.pod_id == "pod123"


def test_close_detruit_le_pod_meme_si_start_a_echoue(monkeypatch):
    termines = []

    def graphql(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "podTerminate" in body["query"]:
            termines.append(body["variables"]["input"]["podId"])
            return httpx.Response(200, json={"data": {"podTerminate": True}})
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}})

    def http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    horloges = iter([0.0, 10.0])
    monkeypatch.setattr(
        "app.engines.runpod_pod.time.monotonic", lambda: next(horloges, 999.0)
    )

    session = _session(_Settings(), graphql, http)
    with pytest.raises(TranscriptionError):
        session.start()
    session.close()

    assert termines == ["pod123"]


def test_close_est_sans_effet_si_le_pod_n_a_jamais_ete_cree():
    def graphql(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("aucun appel GraphQL attendu")

    session = _session(_Settings(), graphql, lambda r: httpx.Response(200))
    session.close()  # ne doit lever ni appeler l'API


def test_close_ne_termine_le_pod_qu_une_seule_fois():
    appels = []

    def graphql(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "podTerminate" in body["query"]:
            appels.append(1)
            return httpx.Response(200, json={"data": {"podTerminate": True}})
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}})

    session = _session(_Settings(), graphql, lambda r: httpx.Response(200))
    session.pod_id = "pod123"  # simule un pod déjà créé
    session.close()
    session.close()

    assert len(appels) == 1


def test_transcribe_chunk_envoie_l_audio_encode_et_rend_le_resultat():
    def http(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/transcribe"
        payload = json.loads(request.content)
        assert base64.b64decode(payload["audio_base64"]) == b"les octets audio"
        assert payload["model"] == "large-v3"
        assert payload["language"] == "fr"
        return httpx.Response(
            200,
            json={"segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}], "language": "fr"},
        )

    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    output = session.transcribe_chunk(b"les octets audio", "large-v3", "fr", label="tronçon 1/1")
    assert output["segments"][0]["text"] == "bonjour"


def test_transcribe_chunk_leve_une_erreur_si_le_pod_en_renvoie_une():
    def http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "plus de mémoire GPU"})

    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="plus de mémoire GPU"):
        session.transcribe_chunk(b"x", "large-v3", "fr", label="tronçon 1/1")
