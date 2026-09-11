"""Tests de ``RunPodEngine._run_job`` : détection d'un job qui ne démarre
jamais (« aucun worker disponible »), distincte d'un job simplement lent.

Aucun réseau réel : ``httpx.MockTransport`` simule l'API RunPod, et
``time.monotonic``/``time.sleep`` sont remplacés pour ne pas attendre pour de
vrai.
"""
from __future__ import annotations

import itertools

import httpx
import pytest

from app.engines import runpod as runpod_module
from app.engines.base import TranscriptionError
from app.engines.runpod import RunPodEngine, _ServerlessLaunchTimeout

BASE_URL = "https://api.runpod.ai/v2/ep_test"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_leve_launch_timeout_si_le_job_reste_en_file(monkeypatch):
    """Un job jamais sorti de IN_QUEUE doit déclencher le repli — pas une
    simple erreur générique — et annuler le job resté en file côté RunPod."""
    appels = {"cancel": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/v2/ep_test/run":
            return httpx.Response(200, json={"id": "job1"})
        if request.url.path == f"/v2/ep_test/cancel/job1":
            appels["cancel"] += 1
            return httpx.Response(200, json={})
        raise AssertionError(f"appel HTTP inattendu : {request.url}")

    # start() puis un seul contrôle « fail fast » : 0s, puis 100s plus tard.
    horloge = itertools.count(0, 100)
    monkeypatch.setattr(runpod_module.time, "monotonic", lambda: next(horloge))

    engine = RunPodEngine()
    with _client(handler) as client:
        with pytest.raises(_ServerlessLaunchTimeout):
            engine._run_job(
                client, BASE_URL, {}, {"input": {}},
                label="tronçon 1/3", should_cancel=None, fail_fast_after=10.0,
            )

    assert appels["cancel"] == 1


def test_ne_declenche_pas_le_repli_une_fois_le_job_pris_par_un_worker(monkeypatch):
    """Un job passé en IN_PROGRESS a bien démarré : même si le délai de
    « fail fast » est ensuite dépassé, il ne doit plus jamais se déclencher —
    seule l'absence totale de worker doit basculer sur le pod."""
    statuts = iter(["IN_PROGRESS", "COMPLETED"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/ep_test/run":
            return httpx.Response(200, json={"id": "job1"})
        if request.url.path == "/v2/ep_test/status/job1":
            statut = next(statuts)
            body = {"status": statut}
            if statut == "COMPLETED":
                body["output"] = {
                    "segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}]
                }
            return httpx.Response(200, json=body)
        raise AssertionError(f"appel HTTP inattendu : {request.url}")

    horloge = itertools.count(0, 1)
    monkeypatch.setattr(runpod_module.time, "monotonic", lambda: next(horloge))
    monkeypatch.setattr(runpod_module.time, "sleep", lambda s: None)

    engine = RunPodEngine()
    with _client(handler) as client:
        output = engine._run_job(
            client, BASE_URL, {}, {"input": {}},
            label="tronçon 1/3", should_cancel=None, fail_fast_after=2.0,
        )

    assert output["segments"][0]["text"] == "bonjour"


def test_sans_fail_fast_attend_normalement(monkeypatch):
    """Le comportement historique (pas de repli configuré) est inchangé :
    un job en file n'est jamais interrompu prématurément."""
    statuts = iter(["IN_QUEUE", "IN_QUEUE", "COMPLETED"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/ep_test/run":
            return httpx.Response(200, json={"id": "job1"})
        if request.url.path == "/v2/ep_test/status/job1":
            statut = next(statuts)
            body = {"status": statut}
            if statut == "COMPLETED":
                body["output"] = {"segments": []}
            return httpx.Response(200, json=body)
        raise AssertionError(f"appel HTTP inattendu : {request.url}")

    horloge = itertools.count(0, 1)
    monkeypatch.setattr(runpod_module.time, "monotonic", lambda: next(horloge))
    monkeypatch.setattr(runpod_module.time, "sleep", lambda s: None)

    engine = RunPodEngine()
    with _client(handler) as client:
        output = engine._run_job(
            client, BASE_URL, {}, {"input": {}},
            label="tronçon 1/1", should_cancel=None, fail_fast_after=None,
        )

    assert output == {"segments": []}
