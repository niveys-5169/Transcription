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
            200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123", "env": []}}}
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
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123", "env": ["HF_TOKEN=hf_test_token"]}}})

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


def test_start_transmet_le_jeton_hugging_face_au_pod():
    entrees = []

    def graphql(request: httpx.Request) -> httpx.Response:
        entrees.append(json.loads(request.content)["variables"]["input"])
        return httpx.Response(200, json={"data": {"podFindAndDeployOnDemand": {"id": "pod123"}}})

    def http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    class _SettingsAvecJeton(_Settings):
        hf_token = "hf_test_token"

    session = _session(_SettingsAvecJeton(), graphql, http)
    session.start()

    assert entrees[0]["env"] == [{"key": "HF_TOKEN", "value": "hf_test_token"}]


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


def test_transcribe_audio_retente_apres_un_404_illisible_du_proxy(monkeypatch):
    """Un 404 *non JSON* vient du proxy (route pas encore propagée) : on
    retente. À ne pas confondre avec le 404 JSON de l'ancienne image, testé
    plus bas."""
    reponses = iter(
        [
            httpx.Response(404, text="404 page not found"),
            httpx.Response(202, json={"job_id": "j1", "status": "queued"}),
            httpx.Response(
                200,
                json={"status": "done", "result": {"segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}]}},
            ),
        ]
    )
    appels = []

    def http(request: httpx.Request) -> httpx.Response:
        appels.append(request.url.path)
        return next(reponses)

    dodo = []
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: dodo.append(s))
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    output = session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")

    assert output["segments"][0]["text"] == "bonjour"
    assert appels == ["/jobs", "/jobs", "/jobs/j1"]
    assert dodo == [3.0]


def test_transcribe_audio_retente_apres_un_502_illisible_du_proxy(monkeypatch):
    reponses = iter(
        [
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(202, json={"job_id": "j1", "status": "queued"}),
            httpx.Response(
                200,
                json={"status": "done", "result": {"segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}]}},
            ),
        ]
    )
    appels = []

    def http(request: httpx.Request) -> httpx.Response:
        appels.append(request.url.path)
        return next(reponses)

    dodo = []
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: dodo.append(s))
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    output = session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")

    assert output["segments"][0]["text"] == "bonjour"
    assert appels == ["/jobs", "/jobs", "/jobs/j1"]
    assert dodo == [3.0]


def test_transcribe_audio_message_apres_502_persistant(monkeypatch):
    """Après trois 502 d'affilée, le message doit orienter vers les Logs du
    pod (plantage probable pendant le chargement du modèle), pas répéter le
    message générique « illisible » qui suggère un problème de propagation
    de route."""
    appels = []

    def http(request: httpx.Request) -> httpx.Response:
        appels.append(1)
        return httpx.Response(502, text="Bad Gateway")

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="ne répond plus derrière le proxy"):
        session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")

    assert len(appels) == 3


def test_transcribe_audio_message_apres_502_puis_404(monkeypatch):
    """Séquence réelle d'un plantage du serveur pod : 502 tant que le port
    est orphelin, puis 404 le temps que le conteneur redémarré se
    réenregistre auprès du proxy. Le dernier statut (404) ne doit pas
    masquer le plantage sous le message générique « illisible »."""
    reponses = iter(
        [
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(404, text="404 page not found"),
        ]
    )
    appels = []

    def http(request: httpx.Request) -> httpx.Response:
        appels.append(1)
        return next(reponses)

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="ne répond plus derrière le proxy.*HTTP 502"):
        session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")

    assert len(appels) == 3


# ------------------------------------------------ transcribe_audio : /jobs
#
# Deux gros fichiers ont été perdus avec l'ancien appel synchrone sur
# /transcribe : « The read operation timed out » après 120 s, alors que le
# pod travaillait encore (ses logs montraient le chargement du modèle
# d'alignement). Le fichier est désormais déposé sur /jobs, et le résultat
# sondé sur /jobs/{id} par de petites requêtes rapides, sans jamais tenir une
# réponse HTTP ouverte pendant toute la transcription.


def _serveur_asynchrone(etats: list[httpx.Response]):
    """Proxy simulé : POST /jobs accepte et rend j1, GET /jobs/j1 déroule
    ``etats`` dans l'ordre. Rend (handler, journal des chemins appelés)."""
    appels: list[str] = []
    suite = iter(etats)

    def http(request: httpx.Request) -> httpx.Response:
        appels.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/jobs":
            assert request.headers["content-type"].startswith("multipart/form-data")
            assert b'name="audio"' in request.content
            assert b"RIFF____WAVE" in request.content
            return httpx.Response(202, json={"job_id": "j1", "status": "queued"})
        assert request.method == "GET" and request.url.path == "/jobs/j1"
        return next(suite)

    return http, appels


def test_transcribe_audio_depose_le_fichier_puis_sonde_le_resultat(monkeypatch):
    http, appels = _serveur_asynchrone([
        httpx.Response(200, json={"status": "queued"}),
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": "done", "result": {
            "text": "bonjour", "segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}],
        }}),
    ])
    dodo = []
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: dodo.append(s))
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"
    avancement = []

    output = session.transcribe_audio(
        b"RIFF____WAVE", "large-v3", "fr",
        on_progress=lambda fraction, message: avancement.append((fraction, message)),
    )

    assert output["segments"][0]["text"] == "bonjour"
    assert appels == ["POST /jobs", "GET /jobs/j1", "GET /jobs/j1", "GET /jobs/j1"]
    assert dodo == [3.0, 3.0]
    assert len(avancement) == 2
    assert all("Transcription en cours sur le pod" in message for _, message in avancement)
    assert all(0.0 <= fraction <= 1.0 for fraction, _ in avancement)


def test_transcribe_audio_ne_tient_pas_de_longue_lecture_sur_le_depot(monkeypatch):
    """Le dépôt sur /jobs doit répondre vite : son délai de lecture reste
    court (ce n'est plus lui qui attend la transcription), et les sondes
    aussi. Aucune requête ne doit dépasser le délai du proxy RunPod."""
    delais = []

    class _Client(httpx.Client):
        def send(self, request, **kwargs):
            delais.append((request.method, request.url.path, request.extensions.get("timeout")))
            return super().send(request, **kwargs)

    http, _ = _serveur_asynchrone([
        httpx.Response(200, json={"status": "done", "result": {"segments": []}}),
    ])
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session._http = _Client(transport=httpx.MockTransport(http))
    session.pod_id = "pod123"

    session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")

    assert [(m, p) for m, p, _ in delais] == [("POST", "/jobs"), ("GET", "/jobs/j1")]
    assert all(t["read"] is not None and t["read"] <= 120.0 for _, _, t in delais)


def test_transcribe_audio_rend_l_erreur_du_travail(monkeypatch):
    http, _ = _serveur_asynchrone([
        httpx.Response(200, json={"status": "error", "error": "plus de mémoire GPU"}),
    ])
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="plus de mémoire GPU"):
        session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")


def test_transcribe_audio_tolere_des_sondes_ratees_ponctuelles(monkeypatch):
    """Un proxy qui bafouille une seconde (502 HTML, coupure réseau) pendant
    une transcription d'une heure ne doit pas faire perdre le travail."""
    http, appels = _serveur_asynchrone([
        httpx.Response(502, text="Bad Gateway"),
        httpx.Response(504, text="Gateway Timeout"),
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": "done", "result": {"segments": []}}),
    ])

    def http_avec_coupure(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and len(appels) == 1:
            appels.append("coupure")
            raise httpx.ReadTimeout("The read operation timed out")
        return http(request)

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http_avec_coupure)
    session.pod_id = "pod123"

    output = session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")

    assert output == {"segments": []}
    assert appels.count("GET /jobs/j1") == 4


def test_transcribe_audio_abandonne_apres_trop_de_sondes_ratees(monkeypatch):
    def http(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "j1"})
        raise httpx.ConnectError("connexion refusée")

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="injoignable pendant le travail j1.*connexion refusée"):
        session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")


def test_transcribe_audio_signale_un_travail_oublie_par_le_pod(monkeypatch):
    """Un conteneur redémarré (plantage) repart avec une mémoire vide : son
    404 JSON « travail inconnu » doit être expliqué, pas retenté à
    l'infini."""
    http, _ = _serveur_asynchrone([
        httpx.Response(404, json={"error": "travail inconnu : j1"}),
    ])
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="oublié le travail j1"):
        session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")


def test_transcribe_audio_abandonne_au_dela_du_delai_du_travail(monkeypatch):
    http, _ = _serveur_asynchrone([
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": "running"}),
    ])
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    horloges = iter([0.0, 0.0, 5.0, 100.0])
    monkeypatch.setattr("app.engines.runpod_pod.time.monotonic", lambda: next(horloges, 999.0))

    class _SettingsCourt(_Settings):
        runpod_pod_job_timeout_seconds = 60

    session = _session(_SettingsCourt(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="délai imparti.*60s"):
        session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")


def test_transcribe_audio_s_arrete_sur_annulation(monkeypatch):
    http, appels = _serveur_asynchrone([
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": "running"}),
    ])
    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"
    sondes = iter([False, True])

    with pytest.raises(TranscriptionError, match="annulée"):
        session.transcribe_audio(
            b"RIFF____WAVE", "large-v3", "fr", should_cancel=lambda: next(sondes),
        )

    assert appels.count("GET /jobs/j1") == 1


def test_transcribe_audio_retombe_sur_transcribe_avec_une_ancienne_image(monkeypatch, caplog):
    """Un pod encore sur l'image précédente ne connaît pas /jobs : son
    propre 404 *JSON* (« introuvable ») n'est pas un raté du proxy mais le
    signe de l'ancienne image. On garde l'appel synchrone d'avant, avec un
    délai de lecture égal au délai du travail, et on prévient."""
    appels = []

    def http(request: httpx.Request) -> httpx.Response:
        appels.append((request.url.path, request.extensions.get("timeout", {}).get("read")))
        if request.url.path == "/jobs":
            return httpx.Response(404, json={"error": "introuvable"})
        assert request.url.path == "/transcribe"
        return httpx.Response(200, json={"segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}]})

    class _SettingsCourt(_Settings):
        runpod_pod_job_timeout_seconds = 1234

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_SettingsCourt(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with caplog.at_level("WARNING", logger="app.engines.runpod_pod"):
        output = session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")

    assert output["segments"][0]["text"] == "bonjour"
    assert appels == [("/jobs", 120.0), ("/transcribe", 1234.0)]
    assert "reconstruisez l'image" in caplog.text


def test_transcribe_audio_ancienne_image_rend_l_erreur_du_pod(monkeypatch):
    def http(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs":
            return httpx.Response(404, json={"error": "introuvable"})
        return httpx.Response(400, json={"error": "audio_base64 manquant"})

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), http)
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="audio_base64 manquant"):
        session.transcribe_audio(b"RIFF____WAVE", "large-v3", "fr")


def test_transcribe_chunk_message_apres_502_puis_404(monkeypatch):
    reponses = iter(
        [
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(404, text="404 page not found"),
            httpx.Response(404, text="404 page not found"),
        ]
    )

    monkeypatch.setattr("app.engines.runpod_pod.time.sleep", lambda s: None)
    session = _session(_Settings(), lambda r: httpx.Response(200), lambda r: next(reponses))
    session.pod_id = "pod123"

    with pytest.raises(TranscriptionError, match="ne répond plus derrière le proxy.*tronçon 1/3.*HTTP 502"):
        session.transcribe_chunk(b"x", "large-v3", "fr", label="tronçon 1/3")


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
