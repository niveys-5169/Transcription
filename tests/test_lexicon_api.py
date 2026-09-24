"""Routes de vérification et de validation du lexique utilisateur."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

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
        verification._executor = None
        verification._state.update({
            "en_cours": False, "faits": 0, "total": 0, "terme_courant": None,
            "en_file": [], "en_verification": [],
        })
    yield
    verification.cancel()
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


def _unverified(count):
    return [term.terme for term in lex.load_lexicon() if not term.verifie][:count]


def _confirmed(term, *, settings):
    return Verdict(
        claim=Claim(type="reference_juridique", citation=term.terme),
        verdict="confirme", forme_correcte=term.terme, explication="Confirmé",
        sources=[Source(titre="Source", url="https://example.org/source")],
        confiance="haute", origine="web",
    )


def _wait_idle(client):
    deadline = time.monotonic() + 2
    state = {}
    while time.monotonic() < deadline:
        state = client.get("/api/lexicon/verification").json()
        if not state["en_cours"]:
            break
        time.sleep(0.01)
    return state


def _blocking_verify(monkeypatch, *, workers):
    gate = threading.Event()

    def fake_verify(term, *, settings):
        gate.wait(2)
        return _confirmed(term, settings=settings)

    monkeypatch.setattr(verification, "verify_term", fake_verify)
    monkeypatch.setattr(verification.config, "load_settings", lambda: SimpleNamespace(factcheck_workers=workers))
    return gate


def test_des_termes_rejoignent_une_verification_en_cours(client, monkeypatch):
    gate = _blocking_verify(monkeypatch, workers=4)
    first, second = _unverified(2)
    assert client.post("/api/lexicon/verification", json={"termes": [first]}).status_code == 200
    response = client.post("/api/lexicon/verification", json={"termes": [second]})
    assert response.status_code == 200
    assert set(response.json()["en_file"]) == {first, second}
    assert response.json()["total"] == 2

    # Un terme déjà en file n'est pas relancé une seconde fois.
    again = client.post("/api/lexicon/verification", json={"termes": [first]}).json()
    assert again["total"] == 2

    gate.set()
    state = _wait_idle(client)
    assert state["faits"] == 2
    assert state["en_file"] == []
    assert {item["terme"] for item in state["propositions"]} == {first, second}
    assert all(item["status"] == "attente" for item in state["propositions"])


def test_annulation_retire_les_termes_en_attente(client, monkeypatch):
    gate = _blocking_verify(monkeypatch, workers=1)
    first, second = _unverified(2)
    client.post("/api/lexicon/verification", json={"termes": [first, second]})
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not verification.status()["en_verification"]:
        time.sleep(0.01)
    running = verification.status()["en_verification"]
    assert len(running) == 1

    state = client.delete("/api/lexicon/verification").json()
    assert state["en_file"] == running
    assert state["total"] == 1

    gate.set()
    state = _wait_idle(client)
    assert state["faits"] == 1
    assert [item["terme"] for item in state["propositions"]] == running


def test_validation_en_lot_des_seules_propositions_confirmees(client):
    confirmed, corrected, weak = _unverified(3)
    source = [{"titre": "Source", "url": "https://example.org/source"}]
    base = {"forme_correcte": "", "explication": "", "origine": "web", "status": "attente"}
    verification._save_proposals([
        {**base, "terme": confirmed, "verdict": "confirme", "confiance": "haute", "sources": source},
        {**base, "terme": corrected, "verdict": "corrige", "confiance": "haute", "sources": source},
        {**base, "terme": weak, "verdict": "confirme", "confiance": "basse", "sources": source},
    ])
    response = client.post("/api/lexicon/verification/valider", json={})
    assert response.status_code == 200
    assert response.json()["valides"] == 1
    by_name = {item["terme"]: item for item in response.json()["terms"]}
    assert by_name[confirmed]["verifie"] is True
    assert by_name[corrected]["verifie"] is False
    assert by_name[weak]["verifie"] is False
    assert verification.find_proposal(confirmed)["status"] == "validee"
    assert verification.find_proposal(corrected)["status"] == "attente"


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


def test_ajout_confirme_conserve_source_et_date(client):
    response = client.post("/api/lexicon", json={
        "terme": "Institution vérifiée",
        "verifie": True,
        "sources": [{"titre": "Source officielle", "url": "https://example.org/institution"}],
    })
    assert response.status_code == 200
    term = next(item for item in response.json()["terms"] if item["terme"] == "Institution vérifiée")
    assert term["verifie"] is True
    assert term["sources"][0]["url"] == "https://example.org/institution"
    assert term["verifie_le"]


def _failed_proposal():
    verification._save_proposals([{
        "terme": _term().terme, "verdict": "erreur", "forme_correcte": "",
        "explication": "Le CLI « claude » n'a produit aucun résultat.", "sources": [],
        "confiance": "basse", "origine": "erreur", "status": "attente",
    }])


def test_un_echec_de_verification_ne_se_valide_pas(client):
    _failed_proposal()
    response = client.post(f"/api/lexicon/{_term().terme}/valider")
    assert response.status_code == 409
    assert verification.find_proposal(_term().terme)["status"] == "attente"


def test_correction_manuelle_apres_echec_reste_non_verifiee(client):
    _failed_proposal()
    definition = "Mesure où le délégué préfère protéger l'œuvre à la façon d'un tuteur, même à l'écart."
    response = client.post(f"/api/lexicon/{_term().terme}/valider", json={"definition": definition})
    assert response.status_code == 200
    updated = next(item for item in response.json()["terms"] if item["terme"] == _term().terme)
    assert updated["definition"] == definition
    assert updated["verifie"] is False
    assert verification.find_proposal(_term().terme)["status"] == "attente"


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
