"""Cache des verdicts de fact-check (socle du chantier 2) : écriture,
relecture, et péremption — voir ``app.db.factcheck_cache_get/put``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import config, db


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.sqlite3")
    db.init_db()


def _put_with_age(key: str, entry: dict, *, age_days: float) -> None:
    """Écrit une entrée puis recule son ``created_at`` pour simuler l'âge."""
    db.factcheck_cache_put(key, entry)
    created_at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE factcheck_cache SET created_at = ? WHERE key = ?", (created_at, key))


def test_ecriture_puis_relecture():
    entry = {
        "claim_type": "reference_juridique",
        "citation": "Code civil, art. 440",
        "verdict": "confirme",
        "forme_correcte": None,
        "explication": "L'article existe bien tel que cité.",
        "sources": [{"titre": "Légifrance", "url": "https://legifrance.gouv.fr/x"}],
        "confiance": "haute",
    }
    db.factcheck_cache_put("hash-1", entry)

    lu = db.factcheck_cache_get("hash-1", max_age_days=90)

    assert lu is not None
    assert lu["citation"] == "Code civil, art. 440"
    assert lu["verdict"] == "confirme"
    assert lu["sources"] == [{"titre": "Légifrance", "url": "https://legifrance.gouv.fr/x"}]


def test_entree_absente_renvoie_none():
    assert db.factcheck_cache_get("inconnue", max_age_days=90) is None


def test_entree_perimee_par_max_age_days():
    _put_with_age(
        "hash-2",
        {"claim_type": "date", "citation": "5 mars 2007", "verdict": "confirme", "sources": []},
        age_days=100,
    )
    assert db.factcheck_cache_get("hash-2", max_age_days=90) is None
    # Avec une fenêtre plus large, la même entrée redevient valide.
    assert db.factcheck_cache_get("hash-2", max_age_days=120) is not None


@pytest.mark.parametrize("verdict", db.SHORT_LIVED_VERDICTS)
def test_introuvable_et_ambigu_perimes_au_bout_de_7_jours_meme_avec_max_age_90(verdict):
    _put_with_age(
        "hash-3",
        {"claim_type": "organisme", "citation": "MDPH", "verdict": verdict, "sources": []},
        age_days=8,
    )
    # 8 jours dépasse SHORT_CACHE_DAYS (7), même si max_age_days vaut 90.
    assert db.factcheck_cache_get("hash-3", max_age_days=90) is None


@pytest.mark.parametrize("verdict", db.SHORT_LIVED_VERDICTS)
def test_introuvable_et_ambigu_valides_avant_7_jours(verdict):
    _put_with_age(
        "hash-4",
        {"claim_type": "organisme", "citation": "MDPH", "verdict": verdict, "sources": []},
        age_days=3,
    )
    assert db.factcheck_cache_get("hash-4", max_age_days=90) is not None


def test_put_remplace_une_entree_existante():
    db.factcheck_cache_put("hash-5", {"verdict": "ambigu", "sources": []})
    db.factcheck_cache_put("hash-5", {"verdict": "confirme", "sources": []})

    lu = db.factcheck_cache_get("hash-5", max_age_days=90)
    assert lu["verdict"] == "confirme"
