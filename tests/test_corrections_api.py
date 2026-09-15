"""Tests des routes de validation manuelle des corrections du fact-check."""
import pytest
from fastapi.testclient import TestClient

from app import db, server


@pytest.fixture
def client():
    with TestClient(server.app) as test_client:
        yield test_client


def _job_avec_correction_en_attente(*, status="attente", texte="Le rapport Charpin de 2024 est une référence."):
    job_id = db.create_job(
        filename="cours.mp4",
        media_path="/dev/null",
        size_bytes=10,
        engine="local",
        model="tiny",
        language="fr",
        proofread="claude",
        structure=False,
    )
    correction = {
        "id": "corr1",
        "claim_type": "nom_propre",
        "citation": "rapport Charpin de 2024",
        "proposition": "rapport Charpin de 2011",
        "marker": "[^v1]",
        "explication": "Confusion avec un rapport homonyme plus récent.",
        "sources": [{"titre": "Source", "url": "https://example.org/x"}],
        "confiance": "basse",
        "start": 0.0,
        "status": status,
    }
    texte_annote = (
        f"{texte.replace('rapport Charpin de 2024', 'rapport Charpin de 2024[^v1]')}\n\n"
        "[^v1]: **« rapport Charpin de 2024 »** — correction proposée : "
        "en attente de validation manuelle. Confusion avec un rapport homonyme plus récent."
    )
    db.update_job(
        job_id,
        status="checked",
        clean_text=texte_annote,
        factcheck_report={
            "claims_checked": 1,
            "corrections": 0,
            "findings": [],
            "pending": [correction],
        },
    )
    return job_id


def test_valider_applique_la_correction_et_marque_le_statut(client):
    job_id = _job_avec_correction_en_attente()

    reponse = client.post(f"/api/jobs/{job_id}/corrections/corr1/valider")
    assert reponse.status_code == 200

    job = db.get_job(job_id)
    assert "rapport Charpin de 2011[^v1]" in job["clean_text"].split("\n\n")[0]
    assert "rapport Charpin de 2024" not in job["clean_text"].split("\n\n")[0]
    assert job["factcheck_report"]["pending"][0]["status"] == "validee"
    assert job["factcheck_report"]["corrections"] == 1


def test_rejeter_ne_modifie_pas_le_texte_mais_marque_le_statut(client):
    job_id = _job_avec_correction_en_attente()

    reponse = client.post(f"/api/jobs/{job_id}/corrections/corr1/rejeter")
    assert reponse.status_code == 200

    job = db.get_job(job_id)
    assert "rapport Charpin de 2024[^v1]" in job["clean_text"].split("\n\n")[0]
    assert job["factcheck_report"]["pending"][0]["status"] == "rejetee"
    assert "rejetée" in job["clean_text"]


def test_valider_une_correction_deja_traitee_est_refuse(client):
    job_id = _job_avec_correction_en_attente(status="validee")
    reponse = client.post(f"/api/jobs/{job_id}/corrections/corr1/valider")
    assert reponse.status_code == 409


def test_valider_une_correction_inconnue_est_404(client):
    job_id = _job_avec_correction_en_attente()
    reponse = client.post(f"/api/jobs/{job_id}/corrections/inconnue/valider")
    assert reponse.status_code == 404


def test_valider_sur_un_travail_inexistant_est_404(client):
    reponse = client.post("/api/jobs/absent/corrections/corr1/valider")
    assert reponse.status_code == 404


def test_valider_echoue_si_la_citation_a_disparu_du_texte(client):
    job_id = _job_avec_correction_en_attente()
    job = db.get_job(job_id)
    db.update_job(job_id, clean_text=job["clean_text"].replace("rapport Charpin de 2024", "autre chose"))

    reponse = client.post(f"/api/jobs/{job_id}/corrections/corr1/valider")
    assert reponse.status_code == 409

    job = db.get_job(job_id)
    assert job["factcheck_report"]["pending"][0]["status"] == "attente"  # inchangé


def test_travail_sans_rapport_factcheck_est_refuse(client):
    job_id = db.create_job(
        filename="cours.mp4", media_path="/dev/null", size_bytes=10,
        engine="local", model="tiny", language="fr", proofread="claude", structure=False,
    )
    reponse = client.post(f"/api/jobs/{job_id}/corrections/corr1/valider")
    assert reponse.status_code == 409
