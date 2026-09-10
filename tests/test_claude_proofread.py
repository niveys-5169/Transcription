"""Tests de la relecture par Claude, sans appel réseau.

Les échanges avec l'API sont remplacés par une doublure de ``_call`` : ce qui
est testé ici, c'est la logique autour de l'appel — découpage, contexte,
garde-fou anti-résumé, sommaire.
"""
import json

import pytest

from app.config import Settings
from app.proofread.claude import ClaudeProofreader
from app.proofread.base import ProofreadError

from conftest import segment


@pytest.fixture
def relecteur(monkeypatch):
    settings = Settings(
        anthropic_api_key="sk-ant-test",
        proofread_model="claude-opus-5",
        proofread_chunk_chars=600,
    )
    instance = ClaudeProofreader(settings)
    monkeypatch.setattr(instance, "is_available", lambda: (True, "simulé"))
    monkeypatch.setattr(instance, "_client", lambda: object())
    return instance


def _segments(count=6):
    phrase = "Le premier principe de la thermodynamique énonce la conservation. "
    return [segment(i * 10.0, i * 10.0 + 10.0, phrase * 3) for i in range(count)]


def test_relecture_assemble_les_blocs(relecteur, monkeypatch):
    appels = []

    def faux_call(client, *, system, user, max_tokens):
        appels.append(user)
        # Réponse de longueur comparable à l'entrée, sinon le garde-fou
        # anti-résumé la remplace par le nettoyage mécanique.
        return f"BLOC{len(appels)}. " + "Une phrase relue de bonne longueur. " * 30

    monkeypatch.setattr(relecteur, "_call", faux_call)
    resultat = relecteur.proofread(_segments(), structure=False)

    assert len(appels) > 1
    assert resultat.mode == "claude"
    assert resultat.text.startswith("BLOC1.")
    assert f"BLOC{len(appels)}." in resultat.text
    # Un bloc par appel, séparés par une ligne blanche.
    assert resultat.text.count("\n\n") == len(appels) - 1


def test_le_contexte_du_bloc_precedent_est_transmis(relecteur, monkeypatch):
    recus = []

    def faux_call(client, *, system, user, max_tokens):
        recus.append(user)
        return "Un texte relu de longueur tout à fait comparable à l'entrée. " * 6

    monkeypatch.setattr(relecteur, "_call", faux_call)
    relecteur.proofread(_segments(), structure=False)

    assert "[CONTEXTE" not in recus[0]
    assert "[CONTEXTE" in recus[1]


def test_une_reponse_trop_courte_declenche_le_repli_mecanique(relecteur, monkeypatch):
    monkeypatch.setattr(
        relecteur, "_call", lambda *a, **k: "En résumé : c'est la thermodynamique."
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
    monkeypatch.setattr(relecteur, "_call", lambda *a, **k: reponses.pop(0))

    resultat = relecteur.proofread([segment(0, 10, corps)], structure=True)
    assert resultat.title == "Thermodynamique — séance 1"
    assert resultat.summary == ["Rappel de vocabulaire", "Entropie"]
    assert "## L'entropie" in resultat.text
    assert resultat.as_markdown().startswith("# Thermodynamique — séance 1")


def test_un_sommaire_en_echec_ne_perd_pas_la_relecture(relecteur, monkeypatch):
    corps = "Un contenu relu parfaitement correct et suffisamment long pour passer."
    appels = {"n": 0}

    def faux_call(client, *, system, user, max_tokens):
        appels["n"] += 1
        if appels["n"] == 1:
            return corps
        raise ProofreadError("API indisponible")

    monkeypatch.setattr(relecteur, "_call", faux_call)
    resultat = relecteur.proofread([segment(0, 10, corps)], structure=True)

    assert resultat.text == corps
    assert resultat.title == ""


def test_un_sommaire_illisible_est_ignore(relecteur, monkeypatch):
    corps = "Un contenu relu parfaitement correct et suffisamment long pour passer."
    reponses = [corps, "je n'ai pas compris la demande"]
    monkeypatch.setattr(relecteur, "_call", lambda *a, **k: reponses.pop(0))

    resultat = relecteur.proofread([segment(0, 10, corps)], structure=True)
    assert resultat.text == corps
    assert resultat.summary == []


def test_annulation_pendant_la_relecture(relecteur, monkeypatch):
    monkeypatch.setattr(relecteur, "_call", lambda *a, **k: "texte" * 100)
    with pytest.raises(ProofreadError, match="annulée"):
        relecteur.proofread(_segments(), structure=False, should_cancel=lambda: True)


def test_indisponible_sans_cle():
    relecteur = ClaudeProofreader(Settings(anthropic_api_key=""))
    disponible, detail = relecteur.is_available()
    assert disponible is False
    assert "clé" in detail.lower() or "installé" in detail.lower()


def test_relecture_sans_segments(relecteur):
    assert relecteur.proofread([], structure=True).text == ""
