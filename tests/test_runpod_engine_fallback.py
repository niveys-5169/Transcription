"""Bascule du moteur RunPod sur un pod de secours quand le serverless ne
démarre aucun worker.

Le point sensible n'est pas le calcul (c'est le pod qui transcrit, pas ce
moteur) mais le cycle de vie : un pod RunPod est facturé à la minute dès sa
création, sans redescendre à zéro entre deux usages comme le serverless. Ces
tests vérifient donc surtout une chose, sous toutes ses formes : le pod est
toujours fermé (``close()``), succès ou échec, dès que la transcription qui
en avait besoin se termine.

Aucun appel réseau réel : ``_run_job`` (l'appel serverless) et
``PodFallbackSession`` (le pod) sont remplacés par des doublures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.engines import runpod as runpod_module
from app.engines.base import TranscriptionError
from app.engines.runpod import RunPodEngine, _ServerlessLaunchTimeout
from app.media import AudioChunk


class _FausseSessionPod:
    """Doublure de PodFallbackSession : trace les appels, sans réseau."""

    instances: list["_FausseSessionPod"] = []

    def __init__(self, settings, *, echoue_transcription=False, echoue_au_demarrage=False):
        self.settings = settings
        self.started = False
        self.closed = 0
        self.chunks_transcrits: list[str] = []
        self._echoue_transcription = echoue_transcription
        self._echoue_au_demarrage = echoue_au_demarrage
        _FausseSessionPod.instances.append(self)

    def start(self):
        if self._echoue_au_demarrage:
            raise TranscriptionError("le pod n'a jamais répondu")
        self.started = True

    def transcribe_chunk(self, audio_bytes, model, language, *, label):
        if self._echoue_transcription:
            raise TranscriptionError(f"le pod a planté sur le {label}")
        self.chunks_transcrits.append(label)
        return {"segments": [{"start": 0.0, "end": 1.0, "text": f"texte {label}"}]}

    def close(self):
        self.closed += 1


def _preparer_troncons(tmp_path: Path, n: int) -> list[AudioChunk]:
    chunks = []
    for i in range(n):
        p = tmp_path / f"chunk{i}.wav"
        p.write_bytes(b"RIFF____WAVEfake")
        chunks.append(AudioChunk(path=p, offset=float(i), duration=1.0))
    return chunks


@pytest.fixture(autouse=True)
def _isoler_reglages():
    _FausseSessionPod.instances.clear()
    yield
    _FausseSessionPod.instances.clear()
    config.save_settings(
        {
            "runpod_api_key": "__clear__",
            "runpod_endpoint_id": "",
            "runpod_pod_enabled": False,
            "runpod_pod_image": "",
        }
    )


def _configurer(**overrides):
    config.save_settings(
        {
            "runpod_api_key": "rpa_test",
            "runpod_endpoint_id": "ep_test",
            **overrides,
        }
    )
    return config.load_settings()


@pytest.fixture
def moteur_bloque_au_premier_troncon(monkeypatch, tmp_path):
    """Un moteur RunPod dont le tout premier tronçon échoue toujours au
    lancement du serverless (job resté en file, aucun worker), et dont le
    découpage en tronçons est remplacé par des fichiers factices."""

    chunks = _preparer_troncons(tmp_path, 3)
    monkeypatch.setattr(runpod_module.media, "split_wav", lambda *a, **k: chunks)

    appels_run_job = []

    def _run_job_bloque(
        self, client, base_url, headers, payload, *, label, should_cancel,
        fail_fast_after=None,
    ):
        appels_run_job.append((label, fail_fast_after))
        raise _ServerlessLaunchTimeout(f"aucun worker pour le {label}")

    monkeypatch.setattr(RunPodEngine, "_run_job", _run_job_bloque)
    return chunks, appels_run_job


def _transcrire(engine, tmp_path):
    return list(
        engine.transcribe(
            tmp_path / "in.wav",
            model="large-v3",
            language="fr",
            duration=3.0,
            workdir=tmp_path,
        )
    )


def test_sans_pod_configure_le_message_explique_comment_l_activer(
    moteur_bloque_au_premier_troncon, tmp_path
):
    _configurer(runpod_pod_enabled=False)
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="pod de secours"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances == []  # jamais créé : pas configuré


def test_bascule_sur_le_pod_pour_tous_les_troncons_et_le_ferme_a_la_fin(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    monkeypatch.setattr(runpod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_enabled=True, runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    segments = _transcrire(engine, tmp_path)

    assert len(segments) == 3  # les 3 tronçons, y compris le 1er, via le pod
    assert len(_FausseSessionPod.instances) == 1  # un seul pod pour toute la transcription
    session = _FausseSessionPod.instances[0]
    assert session.started
    assert len(session.chunks_transcrits) == 3
    assert session.closed == 1


def test_seul_le_premier_troncon_peut_declencher_la_bascule(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    chunks, appels_run_job = moteur_bloque_au_premier_troncon
    monkeypatch.setattr(runpod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_enabled=True, runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    _transcrire(engine, tmp_path)

    # Une fois basculé au 1er tronçon, les suivants vont droit au pod : le
    # serverless n'est retenté ni observé une deuxième fois.
    assert len(appels_run_job) == 1
    label, fail_fast_after = appels_run_job[0]
    assert "1/3" in label
    assert fail_fast_after == config.load_settings().runpod_launch_timeout_seconds


def test_le_pod_est_ferme_meme_si_la_transcription_y_echoue(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runpod_module,
        "PodFallbackSession",
        lambda settings: _FausseSessionPod(settings, echoue_transcription=True),
    )
    _configurer(runpod_pod_enabled=True, runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="planté"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances[0].closed == 1


def test_le_pod_est_ferme_meme_si_son_demarrage_echoue(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runpod_module,
        "PodFallbackSession",
        lambda settings: _FausseSessionPod(settings, echoue_au_demarrage=True),
    )
    _configurer(runpod_pod_enabled=True, runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="jamais répondu"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances[0].closed == 1


def test_le_pod_est_ferme_meme_si_l_annulation_survient_apres_la_bascule(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    monkeypatch.setattr(runpod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_enabled=True, runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    compteur = {"n": 0}

    def annuler_apres_le_premier_troncon():
        compteur["n"] += 1
        return compteur["n"] > 1

    with pytest.raises(TranscriptionError, match="annulée"):
        list(
            engine.transcribe(
                tmp_path / "in.wav",
                model="large-v3",
                language="fr",
                duration=3.0,
                workdir=tmp_path,
                should_cancel=annuler_apres_le_premier_troncon,
            )
        )

    assert _FausseSessionPod.instances[0].closed == 1
