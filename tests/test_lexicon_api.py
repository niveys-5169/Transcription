"""Routes de vérification et de validation du lexique utilisateur."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import lexicon as lex
from app import server
from app.lexicon import verification
from app.proofread.factcheck import Claim, Source, Verdict


@pytest.fixture(autouse=True)
def isolated_lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(lex, "_user_lexicon_path", lambda: tmp_path / "lexique_utilisateur.json")
    monkeypatch.setattr(verification, "_proposals_path", lambda: tmp_path / "lexique_propositions.json")
    lex._cache = None
    verification._cancel.clear()
    with verification._lock:
        verification._state.update({"en_cours": False, "faits": 0, "total": 0, "terme_courant": None})
    yield
    verification._cancel.set()
    lex._cache = None


@pytest.fixture
def client():
    with TestClient(server.app) as test_client:
        yield test_client


def _term():
    return next(item for item in lex.load_lexicon() if item.terme == "Sauvegarde de justice")


def _proposal(status="attente", sources=None):
    verification._save_proposals([{
        "terme": _term().terme,
        "verdict": "confirme",
        "forme_correcte": _term().terme,
        "explication": "Définition confirmée.",
        "sources": sources if sources is not None else [{"titre": "Service public", "url": "https://example.org/source"}],
        "confiance": "haute",
        "origine": "web",
        "status": status,
    }])


def test_lancement_produit_une_proposition_sans_l_appliquer(client, monkeypatch):
    def fake_verify(term, *, settings):
        return Verdict(
            claim=Claim(type="reference_juridique", citation=term.terme),
            verdict="confirme",
            forme_correcte=term.terme,
            explication="Confirmé",
            sources=[Source(titre="Source", url="https://example.org/source")],
            confiance="haute",
            origine="web",
        )

    monkeypatch.setattr(verification, "verify_term", fake_verify)
    response = client.post("/api/lexicon/verification", json={"termes": [_term().terme]})
    assert response.status_code == 200

    deadline = time.monotonic() + 2
    state = {}
    while time.monotonic() < deadline:
        state = client.get("/api/lexicon/verification").json()
        if not state["en_cours"]:
            break
        time.sleep(0.01)
    assert state["faits"] == 1
    assert state["propositions"][0]["status"] == "attente"
    assert _term().verifie is False


def test_lancement_refuse_si_une_verification_est_deja_en_cours(client):
    with verification._lock:
        verification._state["en_cours"] = True
    assert client.post("/api/lexicon/verification", json={}).status_code == 409


def test_annulation_est_exposee(client):
    with verification._lock:
        verification._state["en_cours"] = True
    response = client.delete("/api/lexicon/verification")
    assert response.status_code == 200
    assert verification._cancel.is_set()


def test_validation_marque_verifie_et_preserve_les_champs_livres(client):
    original = _term()
    _proposal()
    response = client.post(f"/api/lexicon/{original.terme}/valider")
    assert response.status_code == 200

    updated = next(item for item in response.json()["terms"] if item["terme"] == original.terme)
    assert updated["verifie"] is True
    assert updated["sources"]
    assert updated["sigles"] == original.sigles
    assert updated["reference"] == original.reference
    assert verification.find_proposal(original.terme)["status"] == "validee"


def test_proposition_deja_traitee_est_refusee(client):
    _proposal(status="rejetee")
    assert client.post(f"/api/lexicon/{_term().terme}/valider").status_code == 409


def test_une_entree_reste_corrigeable_apres_traitement_de_la_proposition(client):
    _proposal(status="rejetee")
    response = client.post(
        f"/api/lexicon/{_term().terme}/valider",
        json={"definition": "Correction ultérieure"},
    )
    assert response.status_code == 200
    updated = next(item for item in response.json()["terms"] if item["terme"] == _term().terme)
    assert updated["definition"] == "Correction ultérieure"


def test_terme_inconnu_est_404(client):
    assert client.post("/api/lexicon/terme-inconnu/valider", json={}).status_code == 404


def test_correction_sans_source_reste_non_verifiee(client):
    original = _term()
    response = client.post(
        f"/api/lexicon/{original.terme}/valider",
        json={"definition": "Définition corrigée", "reference": original.reference},
    )
    assert response.status_code == 200
    updated = next(item for item in response.json()["terms"] if item["terme"] == original.terme)
    assert updated["definition"] == "Définition corrigée"
    assert updated["verifie"] is False
    assert updated["sources"] == []


def test_ancienne_route_ne_marque_pas_verifie_sans_source(client):
    response = client.post("/api/lexicon", json={"terme": "Terme personnel", "verifie": True})
    assert response.status_code == 200
    created = next(item for item in response.json()["terms"] if item["terme"] == "Terme personnel")
    assert created["verifie"] is False
