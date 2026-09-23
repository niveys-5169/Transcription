"""Tests de la relecture par Claude, sans appel réseau.

Le back-end (CLI ou API) est remplacé par une doublure de ``backend.complete`` :
ce qui est testé ici, c'est la logique autour de l'appel — découpage, contexte,
garde-fou anti-résumé, sommaire — pas le back-end lui-même (voir
``test_cli_backend.py`` et ``test_api_backend.py`` pour ça).
"""
import json
import re
import threading

import pytest

from app.config import Settings
from app.proofread.backends.base import BackendResult
from app.proofread.claude import ClaudeProofreader
from app.proofread.base import ProofreadError, TextPair

from conftest import segment


@pytest.fixture
def relecteur(monkeypatch):
    # claude_backend="api" : on isole le test du CLI réel éventuellement
    # présent sur la machine qui fait tourner la suite.
    settings = Settings(
        anthropic_api_key="sk-ant-test",
        claude_backend="api",
        proofread_model="claude-sonnet-5",
        proofread_chunk_chars=600,
    )
    instance = ClaudeProofreader(settings)
    monkeypatch.setattr(instance, "is_available", lambda: (True, "simulé"))
    return instance


def _segments(count=6):
    phrase = "Le premier principe de la thermodynamique énonce la conservation. "
    return [segment(i * 10.0, i * 10.0 + 10.0, phrase * 3) for i in range(count)]


def test_relecture_assemble_les_blocs(relecteur, monkeypatch):
    appels = []

    def faux_complete(*, system, user, max_tokens, schema=None, web_search=False):
        appels.append(user)
        # Les blocs partent de front : le numéro vient du prompt, pas de
        # l'ordre d'arrivée des appels.
        numero = re.search(r"partie (\d+) sur", user).group(1)
        # Réponse de longueur comparable à l'entrée, sinon le garde-fou
        # anti-résumé la remplace par le nettoyage mécanique.
        text = f"BLOC{numero}. " + "Une phrase relue de bonne longueur. " * 30
        return BackendResult(text=text)

    monkeypatch.setattr(relecteur.backend, "complete", faux_complete)
    resultat = relecteur.proofread(_segments(), structure=False)

    assert len(appels) > 1
    assert resultat.mode == "claude"
    assert resultat.text.startswith("BLOC1.")
    assert f"BLOC{len(appels)}." in resultat.text
    # Un bloc par appel, séparés par une ligne blanche.
    assert resultat.text.count("\n\n") == len(appels) - 1


def test_le_contexte_du_bloc_precedent_est_transmis(relecteur, monkeypatch):
    recus = []

    def faux_complete(*, system, user, max_tokens, schema=None, web_search=False):
        recus.append(user)
        text = "Un texte relu de longueur tout à fait comparable à l'entrée. " * 6
        return BackendResult(text=text)

    monkeypatch.setattr(relecteur.backend, "complete", faux_complete)
    relecteur.proofread(_segments(), structure=False)

    assert "[CONTEXTE" not in recus[0]
    assert "[CONTEXTE" in recus[1]


def test_une_reponse_trop_courte_declenche_le_repli_mecanique(relecteur, monkeypatch):
    monkeypatch.setattr(
        relecteur.backend,
        "complete",
        lambda **k: BackendResult(text="En résumé : c'est la thermodynamique."),
    )
    resultat = relecteur.proofread(
        [segment(0, 10, "alors euh " + "on parle de conservation de l'énergie. " * 20)],
        structure=False,
    )
    # Le contenu complet est conservé, malgré le « résumé » renvoyé.
    assert resultat.text.count("conservation") > 5
    assert "euh" not in resultat.text


def test_le_sommaire_ajoute_titre_resume_et_intertitres(relecteur, monkeypatch):
    corps = (
        "Nous ouvrons ce cours par un rappel de vocabulaire indispensable.\n\n"
        "Le second principe introduit la notion d'entropie dans les systèmes."
    )
    reponses = [
        corps,
        json.dumps(
            {
                "title": "Thermodynamique — séance 1",
                "summary": ["Rappel de vocabulaire", "Entropie"],
                "sections": [
                    {
                        "heading": "L'entropie",
                        "quote": "Le second principe introduit la notion d'entropie",
                    }
                ],
            }
        ),
    ]
    monkeypatch.setattr(
        relecteur.backend, "complete", lambda **k: BackendResult(text=reponses.pop(0))
    )

    resultat = relecteur.proofread([segment(0, 10, corps)], structure=True)
    assert resultat.title == "Thermodynamique — séance 1"
    assert resultat.summary == ["Rappel de vocabulaire", "Entropie"]
    assert "## L'entropie" in resultat.text
    assert resultat.as_markdown().startswith("# Thermodynamique — séance 1")


def test_un_sommaire_en_echec_ne_perd_pas_la_relecture(relecteur, monkeypatch):
    corps = "Un contenu relu parfaitement correct et suffisamment long pour passer."
    appels = {"n": 0}

    def faux_complete(*, system, user, max_tokens, schema=None, web_search=False):
        appels["n"] += 1
        if appels["n"] == 1:
            return BackendResult(text=corps)
        raise ProofreadError("API indisponible")

    monkeypatch.setattr(relecteur.backend, "complete", faux_complete)
    resultat = relecteur.proofread([segment(0, 10, corps)], structure=True)

    assert resultat.text == corps
    assert resultat.title == ""


def test_un_sommaire_illisible_est_ignore(relecteur, monkeypatch):
    corps = "Un contenu relu parfaitement correct et suffisamment long pour passer."
    reponses = [corps, "je n'ai pas compris la demande"]
    monkeypatch.setattr(
        relecteur.backend, "complete", lambda **k: BackendResult(text=reponses.pop(0))
    )

    resultat = relecteur.proofread([segment(0, 10, corps)], structure=True)
    assert resultat.text == corps
    assert resultat.summary == []


def test_annulation_pendant_la_relecture(relecteur, monkeypatch):
    monkeypatch.setattr(
        relecteur.backend, "complete", lambda **k: BackendResult(text="texte" * 100)
    )
    with pytest.raises(ProofreadError, match="annulée"):
        relecteur.proofread(_segments(), structure=False, should_cancel=lambda: True)


def test_indisponible_sans_cle():
    relecteur = ClaudeProofreader(
        Settings(anthropic_api_key="", claude_backend="api")
    )
    disponible, detail = relecteur.is_available()
    assert disponible is False
    assert "clé" in detail.lower() or "installé" in detail.lower()


def test_relecture_sans_segments(relecteur):
    assert relecteur.proofread([], structure=True).text == ""


def test_reprend_les_blocs_checkpointes_sans_rappeler_le_modele(relecteur, monkeypatch):
    raw = "Une phrase source suffisamment longue pour devenir un bloc stable. " * 12
    segments = [segment(0, 10, raw), segment(10, 20, raw)]
    first = segments[0]
    checkpoint = TextPair(
        start=first["start"], end=first["end"], raw=first["text"].strip(), clean="Bloc déjà archivé.",
        block_id="block-1-1", source_segment_ids=["segment-1"],
    )
    calls = []
    monkeypatch.setattr(
        relecteur.backend, "complete",
        lambda **kwargs: calls.append(kwargs) or BackendResult(text="Texte relu suffisamment long. " * 20),
    )

    result = relecteur.proofread(
        segments, structure=False, completed_pairs=[checkpoint],
    )

    assert result.pairs[0] == checkpoint
    assert result.text.startswith("Bloc déjà archivé.")
    assert len(calls) == 1


def test_le_contexte_vient_du_brut_precedent_pas_du_relu(relecteur, monkeypatch):
    recus = []

    def faux_complete(*, system, user, max_tokens, schema=None, web_search=False, fast=False):
        recus.append(user)
        return BackendResult(text="HALLUCINATION du bloc relu, assez longue pour passer. " * 6)

    monkeypatch.setattr(relecteur.backend, "complete", faux_complete)
    relecteur.proofread(_segments(), structure=False)

    avec_contexte = [u for u in recus if "[CONTEXTE" in u]
    assert avec_contexte
    for user in avec_contexte:
        contexte = user.split("[FIN DU CONTEXTE]")[0]
        assert "HALLUCINATION" not in contexte
        assert "thermodynamique" in contexte


def test_les_blocs_partent_de_front(relecteur, monkeypatch):
    relecteur.settings.proofread_workers = 2
    # Deux appels doivent être en vol en même temps pour franchir la barrière ;
    # en séquentiel, le premier attendrait seul jusqu'au délai et échouerait.
    barriere = threading.Barrier(2, timeout=5)

    def faux_complete(*, system, user, max_tokens, schema=None, web_search=False, fast=False):
        if "partie 1 sur" in user or "partie 2 sur" in user:
            barriere.wait()
        return BackendResult(text="Une phrase relue de bonne longueur. " * 30)

    monkeypatch.setattr(relecteur.backend, "complete", faux_complete)
    resultat = relecteur.proofread(_segments(), structure=False)
    assert len(resultat.pairs) >= 2


def test_un_echec_de_bloc_garde_les_blocs_finis_au_checkpoint(relecteur, monkeypatch):
    relecteur.settings.proofread_workers = 1

    def faux_complete(*, system, user, max_tokens, schema=None, web_search=False, fast=False):
        if "partie 2 sur" in user:
            raise ProofreadError("quota")
        return BackendResult(text="Une phrase relue de bonne longueur. " * 30)

    sauvegardes = []
    monkeypatch.setattr(relecteur.backend, "complete", faux_complete)
    with pytest.raises(ProofreadError):
        relecteur.proofread(_segments(), structure=False, on_checkpoint=sauvegardes.append)
    assert [len(pairs) for pairs in sauvegardes] == [1]


def test_le_sommaire_utilise_le_couple_rapide(relecteur, monkeypatch):
    rapides = []

    def faux_complete(*, system, user, max_tokens, schema=None, web_search=False, fast=False):
        if "partie" in user:
            return BackendResult(text="Une phrase relue de bonne longueur. " * 30)
        rapides.append(fast)
        return BackendResult(text=json.dumps({"title": "Titre", "summary": ["Point"]}))

    monkeypatch.setattr(relecteur.backend, "complete", faux_complete)
    resultat = relecteur.proofread(_segments(), structure=True)
    assert rapides == [True]
    assert resultat.title == "Titre"
