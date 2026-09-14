"""``runpod_pod_mode`` du moteur RunPod : "off" (serverless seul), "fallback"
(bascule sur un pod si le serverless ne démarre aucun worker) ou "always"
(pod dès le premier tronçon, sans jamais essayer le serverless).

Le point sensible n'est pas le calcul (c'est le pod qui transcrit, pas ce
moteur) mais le cycle de vie : un pod RunPod est facturé à la minute dès sa
création, sans redescendre à zéro entre deux usages comme le serverless. Le
moteur ne le ferme donc plus lui-même à la fin de chaque transcription — il
le rend au ``PodPool`` partagé (voir ``test_runpod_pod_engine.py`` pour le
pool lui-même), qui le garde chaud pour le travail suivant de la file et ne
le ferme qu'après un délai d'inactivité. Ces tests vérifient donc surtout :
que le pod est bien créé quand il le faut, rendu au pool (pas fermé) à la
fin d'une transcription réussie, mais tout de même fermé immédiatement si sa
création ou son démarrage échoue (rien d'utilisable à garder chaud).

Aucun appel réseau réel : ``_run_job`` (l'appel serverless) et
``PodFallbackSession`` (le pod) sont remplacés par des doublures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.engines import runpod as runpod_module
from app.engines import runpod_pod as runpod_pod_module
from app.engines.base import TranscriptionError
from app.engines.runpod import RunPodEngine, _ServerlessLaunchTimeout
from app.engines.runpod_pod import pod_pool
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

    def is_healthy(self):
        return True

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
    pod_pool.shutdown()  # au cas où un test précédent aurait laissé un pod chaud
    _FausseSessionPod.instances.clear()
    yield
    pod_pool.shutdown()
    _FausseSessionPod.instances.clear()
    config.save_settings(
        {
            "runpod_api_key": "__clear__",
            "runpod_endpoint_id": "",
            "runpod_pod_mode": "off",
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
    _configurer(runpod_pod_mode="off")
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="bascule sur pod"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances == []  # jamais créé : pas configuré


def test_bascule_sur_le_pod_pour_tous_les_troncons_et_le_garde_chaud_ensuite(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    monkeypatch.setattr(runpod_pod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_mode="fallback", runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    segments = _transcrire(engine, tmp_path)

    assert len(segments) == 3  # les 3 tronçons, y compris le 1er, via le pod
    assert len(_FausseSessionPod.instances) == 1  # un seul pod pour toute la transcription
    session = _FausseSessionPod.instances[0]
    assert session.started
    assert len(session.chunks_transcrits) == 3
    # Rendu au pool, pas fermé : un éventuel travail suivant dans la file le
    # retrouve chaud plutôt que d'en attendre un nouveau.
    assert session.closed == 0


def test_seul_le_premier_troncon_peut_declencher_la_bascule(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    chunks, appels_run_job = moteur_bloque_au_premier_troncon
    monkeypatch.setattr(runpod_pod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_mode="fallback", runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    _transcrire(engine, tmp_path)

    # Une fois basculé au 1er tronçon, les suivants vont droit au pod : le
    # serverless n'est retenté ni observé une deuxième fois.
    assert len(appels_run_job) == 1
    label, fail_fast_after = appels_run_job[0]
    assert "1/3" in label
    assert fail_fast_after == config.load_settings().runpod_launch_timeout_seconds


def test_le_pod_reste_chaud_si_seule_la_transcription_echoue(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    """Un tronçon en échec (audio corrompu, OOM ponctuel…) ne veut pas dire
    que le pod lui-même est mort : inutile de le fermer, le pool le
    revalidera (``is_healthy``) au prochain travail qui en a besoin."""
    monkeypatch.setattr(
        runpod_pod_module,
        "PodFallbackSession",
        lambda settings: _FausseSessionPod(settings, echoue_transcription=True),
    )
    _configurer(runpod_pod_mode="fallback", runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="planté"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances[0].closed == 0


def test_le_pod_est_ferme_meme_si_son_demarrage_echoue(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runpod_pod_module,
        "PodFallbackSession",
        lambda settings: _FausseSessionPod(settings, echoue_au_demarrage=True),
    )
    _configurer(runpod_pod_mode="fallback", runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="jamais répondu"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances[0].closed == 1


def test_le_pod_reste_chaud_meme_si_l_annulation_survient_apres_la_bascule(
    moteur_bloque_au_premier_troncon, tmp_path, monkeypatch
):
    monkeypatch.setattr(runpod_pod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_mode="fallback", runpod_pod_image="repo/image:tag")
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

    # Une annulation vient de l'utilisateur, pas d'un pod défaillant : le
    # prochain travail de la file le retrouve chaud comme les autres.
    assert _FausseSessionPod.instances[0].closed == 0


# ------------------------------------------------------ mode "always" (direct)


@pytest.fixture
def moteur_avec_serverless_jamais_appele(monkeypatch, tmp_path):
    """Comme ``moteur_bloque_au_premier_troncon``, mais ``_run_job`` échoue le
    test s'il est appelé : en mode « always », le serverless ne doit même
    pas être tenté."""
    chunks = _preparer_troncons(tmp_path, 3)
    monkeypatch.setattr(runpod_module.media, "split_wav", lambda *a, **k: chunks)

    def _run_job_jamais(self, *a, **k):
        raise AssertionError("_run_job appelé alors que le mode est « always »")

    monkeypatch.setattr(RunPodEngine, "_run_job", _run_job_jamais)
    return chunks


def test_le_mode_always_va_droit_au_pod_sans_tenter_le_serverless(
    moteur_avec_serverless_jamais_appele, tmp_path, monkeypatch
):
    monkeypatch.setattr(runpod_pod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_mode="always", runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    segments = _transcrire(engine, tmp_path)

    assert len(segments) == 3
    assert len(_FausseSessionPod.instances) == 1
    session = _FausseSessionPod.instances[0]
    assert session.started
    assert len(session.chunks_transcrits) == 3
    assert session.closed == 0  # gardé chaud pour le prochain fichier de la file


def test_le_mode_always_reutilise_le_meme_pod_pour_plusieurs_fichiers(
    tmp_path, monkeypatch
):
    """Le cas visé par le pool : plusieurs fichiers traités à la suite (un
    seul thread dépile la file, voir pipeline.py) partagent le même pod
    plutôt que d'en recréer un — et retélécharger l'image, recharger le
    modèle — à chaque fichier.

    Ne réutilise pas ``moteur_avec_serverless_jamais_appele`` : ses tronçons
    factices sont supprimés par ``_cleanup`` à la fin de chaque appel à
    ``transcribe()``, donc chaque fichier a besoin des siens, régénérés à
    chaque appel à ``split_wav``.
    """
    monkeypatch.setattr(runpod_pod_module, "PodFallbackSession", _FausseSessionPod)
    monkeypatch.setattr(
        runpod_module.media,
        "split_wav",
        lambda *a, **k: _preparer_troncons(tmp_path, 3),
    )

    def _run_job_jamais(self, *a, **k):
        raise AssertionError("_run_job appelé alors que le mode est « always »")

    monkeypatch.setattr(RunPodEngine, "_run_job", _run_job_jamais)

    _configurer(runpod_pod_mode="always", runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    _transcrire(engine, tmp_path)
    _transcrire(engine, tmp_path)
    _transcrire(engine, tmp_path)

    assert len(_FausseSessionPod.instances) == 1  # le même pod pour les 3 fichiers
    session = _FausseSessionPod.instances[0]
    assert len(session.chunks_transcrits) == 9  # 3 tronçons × 3 fichiers
    assert session.closed == 0

    # Personne d'autre n'a besoin du pod : la fin de session (ou l'échéance
    # d'inactivité) le ferme bel et bien.
    pod_pool.shutdown()
    assert session.closed == 1


def test_le_mode_always_sans_image_echoue_avant_toute_creation_de_pod(
    moteur_avec_serverless_jamais_appele, tmp_path, monkeypatch
):
    monkeypatch.setattr(runpod_pod_module, "PodFallbackSession", _FausseSessionPod)
    _configurer(runpod_pod_mode="always", runpod_pod_image="")
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="[Dd]émarrage direct"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances == []


def test_le_pod_est_ferme_meme_si_son_demarrage_echoue_en_mode_always(
    moteur_avec_serverless_jamais_appele, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runpod_pod_module,
        "PodFallbackSession",
        lambda settings: _FausseSessionPod(settings, echoue_au_demarrage=True),
    )
    _configurer(runpod_pod_mode="always", runpod_pod_image="repo/image:tag")
    engine = RunPodEngine()

    with pytest.raises(TranscriptionError, match="jamais répondu"):
        _transcrire(engine, tmp_path)

    assert _FausseSessionPod.instances[0].closed == 1


def test_le_mode_always_n_exige_pas_d_endpoint_serverless():
    """Le endpoint serverless ne sert à rien en mode « always » : is_available()
    ne doit pas le réclamer, contrairement aux modes "off"/"fallback"."""
    _configurer(runpod_pod_mode="always", runpod_pod_image="repo/image:tag", runpod_endpoint_id="")
    engine = RunPodEngine()

    available, detail = engine.is_available()

    assert available, detail
