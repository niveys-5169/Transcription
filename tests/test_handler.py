"""Tests du worker RunPod (`handler.py`), sans GPU ni réseau.

Le point sensible est l'appel à ``runpod.serverless.start()`` : RunPod le
cherche dans le dépôt au moment de créer le endpoint, et son scanner ne le
voit pas s'il est enfermé dans un ``if __name__ == "__main__":``. Le bug a
déjà eu lieu sur ce projet ; ce test empêche qu'il revienne sans qu'on s'en
aperçoive.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
HANDLER = RACINE / "handler.py"


class _FauxSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _FauxInfo:
    language = "fr"


@pytest.fixture
def worker(monkeypatch):
    """Importe handler.py avec runpod et faster-whisper simulés."""
    appels = {"start": [], "modele": []}

    faux_runpod = types.ModuleType("runpod")
    faux_runpod.serverless = types.SimpleNamespace(
        start=lambda config: appels["start"].append(config)
    )

    class FauxWhisperModel:
        def __init__(self, taille, **kwargs):
            appels["modele"].append({"taille": taille, **kwargs})

        def transcribe(self, chemin, **kwargs):
            appels["transcribe"] = {"chemin": chemin, **kwargs}
            return (
                iter(
                    [
                        _FauxSegment(0.0, 2.0, " Bonjour à tous."),
                        _FauxSegment(2.0, 4.5, " On commence le cours."),
                    ]
                ),
                _FauxInfo(),
            )

    faux_fw = types.ModuleType("faster_whisper")
    faux_fw.WhisperModel = FauxWhisperModel

    monkeypatch.setitem(sys.modules, "runpod", faux_runpod)
    monkeypatch.setitem(sys.modules, "faster_whisper", faux_fw)
    monkeypatch.delitem(sys.modules, "handler", raising=False)

    spec = importlib.util.spec_from_file_location("handler", HANDLER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["handler"] = module
    spec.loader.exec_module(module)

    module._appels = appels
    module._cache_modele = getattr(module, "_model_cache", {})
    module._cache_modele.clear()
    yield module
    sys.modules.pop("handler", None)


# ------------------------------------------------------ détection par RunPod


def test_serverless_start_est_appele_au_niveau_module(worker):
    """L'appel doit avoir lieu à l'import, pas sous un garde __main__."""
    assert len(worker._appels["start"]) == 1
    assert worker._appels["start"][0]["handler"] is worker.handler


def test_l_appel_n_est_pas_enferme_dans_un_garde_main():
    """Le scanner de RunPod lit le fichier : la ligne doit être en clair.

    Ce contrôle est textuel à dessein — c'est exactement ce que fait RunPod,
    et un appel indenté sous ``if __name__`` passerait le test précédent tout
    en restant invisible à ses yeux.
    """
    lignes = HANDLER.read_text(encoding="utf-8").splitlines()
    appels = [l for l in lignes if "runpod.serverless.start(" in l]

    assert appels, "aucun appel à runpod.serverless.start() dans handler.py"
    for ligne in appels:
        assert not ligne.startswith((" ", "\t")), (
            "runpod.serverless.start() est indenté : RunPod ne le détectera "
            f"pas → {ligne!r}"
        )
    assert '__name__ == "__main__"' not in HANDLER.read_text(encoding="utf-8")


def test_le_handler_est_a_la_racine_du_depot():
    """RunPod cherche le handler à la racine, à côté du Dockerfile."""
    assert HANDLER.exists()
    assert (RACINE / "Dockerfile").exists()
    assert "runpod" in (RACINE / "requirements.txt").read_text(encoding="utf-8")


def test_le_dockerignore_n_exclut_pas_le_worker():
    """Une exclusion malheureuse casserait la construction de l'image."""
    motifs = [
        ligne.strip()
        for ligne in (RACINE / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.startswith("#")
    ]
    for indispensable in ("handler.py", "requirements.txt", "Dockerfile"):
        assert indispensable not in motifs
    assert "*.py" not in motifs


def test_le_dockerfile_installe_via_python3_et_verifie_l_import():
    """Un pod de secours a échoué en production avec « No module named
    'faster_whisper' » alors que le `pip install` du build avait pourtant
    réussi — signe possible d'un `pip` et d'un `python3` résolus vers des
    environnements différents dans l'image de base. `python3 -m pip` force
    la cohérence, et l'import de vérification fait échouer le build tout de
    suite si ça se reproduit, plutôt que de livrer une image cassée."""
    contenu = (RACINE / "Dockerfile").read_text(encoding="utf-8")
    assert "python3 -m pip install" in contenu
    assert 'python3 -c "import faster_whisper' in contenu


# ------------------------------------------------------------ contrat d'API


def _job(**champs):
    import base64

    entree = {"audio_base64": base64.b64encode(b"RIFF____WAVE").decode()}
    entree.update(champs)
    return {"input": entree}


def test_transcription_rend_le_contrat_attendu(worker):
    sortie = worker.handler(_job(model="large-v3", language="fr"))

    assert sortie["text"] == "Bonjour à tous. On commence le cours."
    assert sortie["language"] == "fr"
    assert sortie["segments"][0] == {"start": 0.0, "end": 2.0, "text": " Bonjour à tous."}
    assert "error" not in sortie


def test_le_worker_tourne_sur_gpu_en_float16(worker):
    worker.handler(_job(model="medium"))
    charge = worker._appels["modele"][-1]
    assert charge["taille"] == "medium"
    assert charge["device"] == "cuda"
    assert charge["compute_type"] == "float16"


def test_le_filtre_de_voix_reste_desactive(worker):
    """vad_filter=True a déjà fait disparaître un fichier entier de 10 min."""
    worker.handler(_job())
    assert worker._appels["transcribe"]["vad_filter"] is False


def test_un_modele_inconnu_retombe_sur_large_v3(worker):
    worker.handler(_job(model="gigantesque"))
    assert worker._appels["modele"][-1]["taille"] == "large-v3"


def test_le_modele_est_garde_en_cache_entre_deux_jobs(worker):
    worker.handler(_job(model="small"))
    worker.handler(_job(model="small"))
    tailles = [charge["taille"] for charge in worker._appels["modele"]]
    assert tailles == ["small"], "le modèle a été rechargé inutilement"


def test_audio_manquant(worker):
    sortie = worker.handler({"input": {}})
    assert "error" in sortie
    assert "audio_base64" in sortie["error"]


def test_audio_base64_invalide(worker):
    sortie = worker.handler({"input": {"audio_base64": "pas du base64 !!"}})
    assert "error" in sortie


def test_entree_absente(worker):
    assert "error" in worker.handler({})


def test_une_erreur_de_transcription_est_renvoyee_proprement(worker, monkeypatch):
    def explose(self, chemin, **kwargs):
        raise RuntimeError("plus de mémoire GPU")

    monkeypatch.setattr(
        sys.modules["faster_whisper"].WhisperModel, "transcribe", explose
    )
    sortie = worker.handler(_job())
    assert sortie["error"] == "plus de mémoire GPU"
