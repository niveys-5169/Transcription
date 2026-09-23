"""Le lexique fiable coupe le chemin IA sans valider une affirmation entière."""
from app.config import Settings
from app import lexicon
from app.lexicon.resolver import resolve, shield_trusted
from app.proofread import factcheck


def _trusted(monkeypatch):
    terms = [lexicon.Term(
        terme="Agence régionale de santé",
        sigles=["ARS"],
        verifie=True,
    )]
    monkeypatch.setattr("app.lexicon.resolver.load_lexicon", lambda: terms)
    return terms


def test_graphies_validees_masquees_sans_toucher_aux_autres_mots(monkeypatch):
    _trusted(monkeypatch)
    original = "A.R.S. et Agence regionale de sante; mars reste intact."
    masked, recognized = shield_trusted(original)
    assert len(masked) == len(original)
    assert masked.endswith("; mars reste intact.")
    assert [item["terme"] for item in recognized] == [
        "Agence régionale de santé", "Agence régionale de santé"
    ]
    assert resolve("agence regionale de sante") is not None
    assert resolve("L'ARS est compétente") is None


def test_alias_ambigu_ne_vaut_pas_preuve(monkeypatch):
    terms = _trusted(monkeypatch)
    terms.append(lexicon.Term(terme="Autre organisme", sigles=["ARS"], verifie=True))
    masked, recognized = shield_trusted("ARS")
    assert masked == "ARS"
    assert recognized == []
    assert resolve("ARS") is None


def test_apostrophe_et_accent_d_une_institution(monkeypatch):
    monkeypatch.setattr("app.lexicon.resolver.load_lexicon", lambda: [lexicon.Term(
        terme="Direction départementale de l'emploi",
        verifie=True,
    )])
    masked, recognized = shield_trusted("Direction departementale de l’emploi.")
    assert len(recognized) == 1
    assert not any(char.isalnum() for char in masked)


def test_factcheck_de_termes_connus_ne_contacte_pas_ia(monkeypatch):
    _trusted(monkeypatch)
    monkeypatch.setattr(factcheck, "get_backend", lambda settings=None: (_ for _ in ()).throw(
        AssertionError("Le backend ne doit pas être appelé")))
    text = "ARS. Agence régionale de santé."
    result, report, entities = factcheck.factcheck(text, settings=Settings())
    assert result == text
    assert report.claims_checked == 0
    assert len(report.recognized) == 2
    assert entities == []


def test_une_assertion_contenant_un_sigle_n_est_pas_confirmee(monkeypatch):
    _trusted(monkeypatch)
    monkeypatch.setattr(factcheck.db, "factcheck_cache_get", lambda *args, **kwargs: {
        "verdict": "introuvable", "sources": [],
    })
    verdict = factcheck.verify_claim(
        factcheck.Claim(type="organisme", citation="ARS doit financer ce dispositif"),
        settings=Settings(),
    )
    assert verdict.origine != "lexique"
