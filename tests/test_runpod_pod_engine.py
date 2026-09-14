"""Tests du client de pods RunPod (``app/engines/runpod_pod.py``).

Aucun réseau réel : l'API GraphQL des pods et le proxy HTTP du pod sont
simulés via ``httpx.MockTransport``. Le point sensible, comme pour le
moteur, est que ``PodFallbackSession.close()`` détruit bien le pod dès qu'un
``pod_id`` existe — y compris quand ``start()`` échoue en cours de route.
"""
from __future__ import annotations

import base64
import json
import types

import httpx
import pytest

from app.engines import runpod_pod as runpod_pod_module
from app.engines.base import TranscriptionError
from app.engines.runpod_pod import (
    GRAPHQL_URL,
    VOLUME_MOUNT_PATH,
    PodFallbackSession,
    RunPodPodClient,
    pod_pool,
)


class _Settings:
    runpod_api_key = "rpa_test"
    runpod_pod_image = "repo/transcription-pod:latest"
    runpod_pod_gpu_type_id = "NVIDIA L4"
    runpod_pod_container_disk_gb = 20
    runpod_pod_port = 8000
    runpod_pod_boot_timeout_seconds = 5
    runpod_pod_network_volume_id = ""


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


def test_create_n_envoie_pas_de_volume_reseau_par_defaut():
    def handler(request: httpx.Request) -> httpx.Response:
        entree = json.loads(request.content)["variables"]["input"]
        assert "networkVolumeId" not in entree
        assert "volumeMountPath" not in entree
        return httpx.Response(
            200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}}
        )

    client = _mock_client(handler)
    client.create(
        name="test", image="img", gpu_type_id="NVIDIA L4",
        container_disk_gb=20, port=8000, start_command="cmd",
    )


def test_create_attache_le_volume_reseau_quand_configure():
    def handler(request: httpx.Request) -> httpx.Response:
        entree = json.loads(request.content)["variables"]["input"]
        assert entree["networkVolumeId"] == "vol123"
        assert entree["volumeMountPath"] == VOLUME_MOUNT_PATH
        return httpx.Response(
            200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}}
        )

    client = _mock_client(handler)
    client.create(
        name="test", image="img", gpu_type_id="NVIDIA L4",
        container_disk_gb=20, port=8000, start_command="cmd",
        network_volume_id="vol123",
    )


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


def test_start_transmet_le_volume_reseau_configure(monkeypatch):
    entrees = []

    def graphql(request: httpx.Request) -> httpx.Response:
        entrees.append(json.loads(request.content)["variables"]["input"])
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}})

    def http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    class _SettingsAvecVolume(_Settings):
        runpod_pod_network_volume_id = "vol123"

    session = _session(_SettingsAvecVolume(), graphql, http)
    session.start()

    assert entrees[0]["networkVolumeId"] == "vol123"
    assert entrees[0]["volumeMountPath"] == VOLUME_MOUNT_PATH


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


def test_transcribe_chunk_retente_apres_un_404_illisible_du_proxy(monkeypatch):
    """Le proxy RunPod peut renvoyer un 404 non-JSON juste après la création
    du pod, le temps que sa route se propage — même si /health avait déjà
    répondu. pod_server.py, lui, répond toujours en JSON : un corps illisible
    vient donc forcément du proxy, et mérite une nouvelle tentative."""
    reponses = iter(
        [
            httpx.Response(404, text="404 page not found"),
            httpx.Response(
                200,
                json={
                    "segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}],
                    "language": "fr",
                },
            ),
        ]
    )
    appels = []

    def http(request: httpx.Request) -> httpx.Response:
        appels.append(1)
        return next(reponses)

    dodo = []
    monkeypatch.setattr(
        "app.engines.runpod_pod.time.sleep", lambda s: dodo.append(s)
    )

    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    output = session.transcribe_chunk(b"x", "large-v3", "fr", label="tronçon 1/3")

    assert output["segments"][0]["text"] == "bonjour"
    assert len(appels) == 2
    assert dodo == [3.0]


def test_transcribe_chunk_abandonne_apres_plusieurs_404_illisibles(monkeypatch):
    appels = []

    def http(request: httpx.Request) -> httpx.Response:
        appels.append(1)
        return httpx.Response(404, text="404 page not found")

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)

    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="illisible"):
        session.transcribe_chunk(b"x", "large-v3", "fr", label="tronçon 1/3")

    assert len(appels) == 3


def test_transcribe_chunk_leve_une_erreur_si_le_pod_en_renvoie_une():
    def http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "plus de mémoire GPU"})

    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="plus de mémoire GPU"):
        session.transcribe_chunk(b"x", "large-v3", "fr", label="tronçon 1/1")


# ------------------------------------------------------------------ PodPool
#
# Le pool garde un pod chaud entre plusieurs travaux de la file (voir
# pipeline.py, un seul thread traite les travaux l'un après l'autre) pour ne
# pas recréer un pod — donc retélécharger l'image et recharger le modèle —
# à chaque fichier. Ces tests remplacent PodFallbackSession et
# threading.Timer par des doublures : pas de réseau, pas d'attente réelle du
# délai d'inactivité (le "timer" se déclenche à la demande via .fire()).


class _FakeSession:
    """Doublure de PodFallbackSession pour tester PodPool isolément."""

    instances: list["_FakeSession"] = []

    def __init__(self, settings):
        self.settings = settings
        self.started = False
        self.closed = 0
        self.healthy = True
        _FakeSession.instances.append(self)

    def start(self):
        if getattr(self.settings, "_fail_start", False):
            raise TranscriptionError("le pod n'a jamais répondu")
        self.started = True

    def is_healthy(self):
        return self.healthy

    def close(self):
        self.closed += 1


class _FakeTimer:
    """Doublure de threading.Timer : ne programme rien sur un vrai thread,
    se déclenche à la demande via ``fire()``."""

    instances: list["_FakeTimer"] = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.cancelled = False
        self.daemon = False
        _FakeTimer.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.function()


def _pod_settings(**overrides):
    base = dict(
        runpod_pod_image="repo/transcription-pod:latest",
        runpod_pod_gpu_type_id="NVIDIA L4",
        runpod_pod_container_disk_gb=20,
        runpod_pod_port=8000,
        runpod_pod_idle_timeout_seconds=300,
        runpod_pod_network_volume_id="",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _isoler_pod_pool(monkeypatch):
    monkeypatch.setattr(runpod_pod_module, "PodFallbackSession", _FakeSession)
    monkeypatch.setattr(runpod_pod_module.threading, "Timer", _FakeTimer)
    pod_pool.shutdown()  # au cas où un test précédent aurait laissé un pod
    _FakeSession.instances.clear()
    _FakeTimer.instances.clear()
    yield
    pod_pool.shutdown()
    _FakeSession.instances.clear()
    _FakeTimer.instances.clear()


def test_acquire_reutilise_le_pod_si_rien_n_a_change():
    settings = _pod_settings()
    session1 = pod_pool.acquire(settings)
    pod_pool.release(settings)
    session2 = pod_pool.acquire(settings)

    assert session1 is session2
    assert len(_FakeSession.instances) == 1
    assert session1.closed == 0


def test_acquire_recree_le_pod_si_les_reglages_ont_change():
    settings1 = _pod_settings(runpod_pod_image="repo/a:latest")
    settings2 = _pod_settings(runpod_pod_image="repo/b:latest")

    session1 = pod_pool.acquire(settings1)
    pod_pool.release(settings1)
    session2 = pod_pool.acquire(settings2)

    assert session1 is not session2
    assert session1.closed == 1  # l'ancien pod, incompatible, est fermé
    assert len(_FakeSession.instances) == 2


def test_acquire_recree_le_pod_si_le_volume_reseau_change():
    settings1 = _pod_settings(runpod_pod_network_volume_id="")
    settings2 = _pod_settings(runpod_pod_network_volume_id="vol123")

    session1 = pod_pool.acquire(settings1)
    pod_pool.release(settings1)
    session2 = pod_pool.acquire(settings2)

    assert session1 is not session2
    assert session1.closed == 1
    assert len(_FakeSession.instances) == 2


def test_acquire_recree_le_pod_si_l_ancien_ne_repond_plus():
    settings = _pod_settings()
    session1 = pod_pool.acquire(settings)
    pod_pool.release(settings)
    session1.healthy = False

    session2 = pod_pool.acquire(settings)

    assert session1 is not session2
    assert session1.closed == 1
    assert len(_FakeSession.instances) == 2


def test_acquire_ferme_le_pod_si_le_demarrage_echoue():
    settings = _pod_settings()
    settings._fail_start = True

    with pytest.raises(TranscriptionError):
        pod_pool.acquire(settings)

    assert _FakeSession.instances[0].closed == 1


def test_release_arme_un_delai_puis_ferme_le_pod_a_l_echeance():
    settings = _pod_settings(runpod_pod_idle_timeout_seconds=42)
    session = pod_pool.acquire(settings)
    pod_pool.release(settings)

    assert len(_FakeTimer.instances) == 1
    timer = _FakeTimer.instances[0]
    assert timer.interval == 42
    assert session.closed == 0

    timer.fire()

    assert session.closed == 1


def test_acquire_annule_le_delai_d_inactivite_en_cours():
    settings = _pod_settings()
    session = pod_pool.acquire(settings)
    pod_pool.release(settings)
    timer = _FakeTimer.instances[0]

    pod_pool.acquire(settings)  # un nouveau travail arrive avant l'échéance

    assert timer.cancelled
    assert session.closed == 0


def test_shutdown_ferme_le_pod_sans_attendre_le_delai():
    settings = _pod_settings()
    session = pod_pool.acquire(settings)
    pod_pool.release(settings)

    pod_pool.shutdown()

    assert session.closed == 1
