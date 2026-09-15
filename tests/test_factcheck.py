"""Tests du fact-check : extraction, vérification, application des verdicts.

Le point le plus important de ces tests : le garde-fou mécanique qui empêche
un verdict rendu de mémoire, sans recherche web effective, de passer pour une
confirmation (voir test_garde_fou_*). C'est le cœur de la demande — flaguer
l'incertitude résiduelle plutôt que lisser le texte en une version fluide
mais faussement définitive.
"""
import pytest

from app.config import Settings
from app.proofread.backends.base import BackendResult
from app.proofread.base import ProofreadError
from app.proofread.backends.cli import QuotaExhausted
from app.proofread import factcheck as fc


def claim(citation, type="nom_propre", question="", start=0.0):
    return fc.Claim(type=type, citation=citation, question=question, start=start)


def verdict(citation, **kw):
    kw.setdefault("verdict", "confirme")
    kw.setdefault("confiance", "haute")
    return fc.Verdict(claim=claim(citation), **kw)


# ------------------------------------------------------------- apply_verdicts


def test_une_correction_haute_confiance_est_appliquee():
    text = "Le rapport Charpin de 2024 est une référence."
    v = verdict(
        "rapport Charpin de 2024",
        verdict="corrige",
        forme_correcte="rapport Charpin de 2011",
        sources=[fc.Source(titre="Source", url="https://example.org/x")],
        confiance="haute",
    )
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    body = new_text.split("\n\n")[0]
    assert "rapport Charpin de 2011[^v1]" in body
    assert "rapport Charpin de 2024" not in body  # le corps ne garde pas l'ancienne forme
    assert "[^v1]:" in new_text
    assert findings == []  # une correction appliquée n'est pas un point à vérifier
    assert pending == []


def test_une_correction_sans_source_n_est_pas_appliquee():
    text = "Le rapport Charpin de 2024 est une référence."
    v = verdict(
        "rapport Charpin de 2024",
        verdict="corrige",
        forme_correcte="rapport Charpin de 2011",
        sources=[],  # pas de source : la correction ne doit pas passer
        confiance="haute",
    )
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    # Le texte transcrit est conservé caractère pour caractère (marqueur en plus).
    assert "Le rapport Charpin de 2024[^v1] est une référence." == new_text.split("\n\n")[0]
    assert len(findings) == 1
    assert pending == []  # pas de source : pas de proposition à valider non plus


def test_une_correction_confiance_moindre_attend_une_validation_manuelle():
    # « Peu probable » : la confiance n'est pas haute. Ni appliquée seule, ni
    # simplement écartée — elle attend une décision humaine explicite.
    text = "Le rapport Charpin de 2024 est une référence."
    v = verdict(
        "rapport Charpin de 2024",
        verdict="corrige",
        forme_correcte="rapport Charpin de 2011",
        sources=[fc.Source(titre="Source", url="https://example.org/x")],
        confiance="basse",
    )
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    assert "rapport Charpin de 2024[^v1]" in new_text
    assert "rapport Charpin de 2011" not in new_text.split("\n\n")[0]
    assert len(findings) == 1
    assert findings[0].severity == "haute"  # confiance basse -> gravité haute
    assert len(pending) == 1
    assert pending[0].status == "attente"
    assert pending[0].citation == "rapport Charpin de 2024"
    assert pending[0].proposition == "rapport Charpin de 2011"
    assert pending[0].marker == "[^v1]"
    assert "en attente de validation manuelle" in new_text


def test_une_correction_de_categorie_sensible_attend_une_validation_meme_a_confiance_haute():
    # « Importante » : catégorie sensible, même si la confiance est haute.
    text = "La loi du 12 mars 2020 encadre ce dispositif."
    v = fc.Verdict(
        claim=claim("loi du 12 mars 2020", type="reference_juridique"),
        verdict="corrige",
        forme_correcte="loi du 12 mars 2021",
        sources=[fc.Source(titre="Source", url="https://example.org/x")],
        confiance="haute",
    )
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    assert "loi du 12 mars 2020[^v1]" in new_text
    assert "loi du 12 mars 2021" not in new_text.split("\n\n")[0]
    assert len(pending) == 1
    assert pending[0].claim_type == "reference_juridique"


def test_confirme_ne_touche_pas_le_texte_et_alimente_les_entites():
    text = "Le DIPM a été remis à la personne protégée."
    v = verdict("DIPM", forme_correcte="DIPM", explication="Terme du métier.")
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    assert new_text == text  # inchangé, aucun marqueur
    assert findings == []
    assert pending == []
    assert len(entities) == 1
    assert entities[0]["nom"] == "DIPM"


def test_infirme_est_signale_gravite_haute():
    text = "L'intervenant est Jean Dupontier."
    v = verdict("Jean Dupontier", verdict="infirme", explication="Personne inexistante.")
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    assert "Jean Dupontier[^v1]" in new_text
    assert len(findings) == 1
    assert findings[0].severity == "haute"
    assert findings[0].kind == "fait"
    assert pending == []


def test_citation_introuvable_est_ignoree_silencieusement():
    text = "Un texte quelconque."
    v = verdict("Ceci n'apparaît nulle part dans le texte", verdict="infirme")
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    assert new_text == text
    assert findings == []
    assert pending == []


def test_citation_vide_est_ignoree():
    text = "Un texte quelconque."
    v = fc.Verdict(claim=claim(""), verdict="infirme")
    new_text, findings, entities, pending = fc.apply_verdicts(text, [v])
    assert new_text == text
    assert findings == []
    assert pending == []


def test_horodatage_derive_de_la_position_et_de_la_duree():
    text = "Début. " * 20 + "Jean Dupontier au milieu. " + "Fin. " * 20
    position = text.find("Jean Dupontier") / len(text)
    v = fc.Verdict(
        claim=claim("Jean Dupontier", start=position), verdict="infirme"
    )
    _, findings, _, _ = fc.apply_verdicts(text, [v], duration=1000.0)
    assert findings[0].start == pytest.approx(position * 1000.0, abs=1.0)


# --------------------------------------------------- accept_pending / reject_pending


def _one_pending(text, **kw):
    kw.setdefault("verdict", "corrige")
    kw.setdefault("confiance", "basse")
    kw.setdefault("sources", [fc.Source(titre="Source", url="https://example.org/x")])
    v = verdict(**kw)
    new_text, _findings, _entities, pending = fc.apply_verdicts(text, [v])
    assert len(pending) == 1
    return new_text, pending[0]


def test_accept_pending_applique_la_correction_et_met_a_jour_la_note():
    text = "Le rapport Charpin de 2024 est une référence."
    new_text, correction = _one_pending(
        text, citation="rapport Charpin de 2024", forme_correcte="rapport Charpin de 2011"
    )
    resultat = fc.accept_pending(new_text, correction)
    assert resultat is not None
    assert "rapport Charpin de 2011[^v1]" in resultat.split("\n\n")[0]
    assert "rapport Charpin de 2024" not in resultat.split("\n\n")[0]
    assert "validé manuellement" in resultat
    assert "en attente de validation manuelle" not in resultat


def test_accept_pending_echoue_si_la_citation_a_disparu():
    text = "Le rapport Charpin de 2024 est une référence."
    new_text, correction = _one_pending(
        text, citation="rapport Charpin de 2024", forme_correcte="rapport Charpin de 2011"
    )
    # Le texte a changé entre-temps : la citation n'existe plus.
    texte_modifie = new_text.replace("rapport Charpin de 2024", "un autre passage")
    assert fc.accept_pending(texte_modifie, correction) is None


def test_reject_pending_ne_touche_pas_le_texte_mais_met_a_jour_la_note():
    text = "Le rapport Charpin de 2024 est une référence."
    new_text, correction = _one_pending(
        text, citation="rapport Charpin de 2024", forme_correcte="rapport Charpin de 2011"
    )
    resultat = fc.reject_pending(new_text, correction)
    assert "rapport Charpin de 2024[^v1]" in resultat.split("\n\n")[0]
    assert "rejetée" in resultat
    assert "en attente de validation manuelle" not in resultat


# -------------------------------------------------------------- garde-fou


class _FakeBackend:
    def __init__(self, parsed, web_searches=0, sources=None, text=""):
        self.parsed = parsed
        self.web_searches = web_searches
        self.sources = sources or []
        self.text = text

    def is_available(self):
        return True, "ok"

    def complete(self, **kwargs):
        return BackendResult(
            text=self.text,
            web_searches=self.web_searches,
            sources=self.sources,
            parsed=self.parsed,
        )


def test_garde_fou_aucune_recherche_constatee_force_introuvable(monkeypatch):
    # Le modèle répond « confirmé » avec une confiance haute, mais rien ne
    # prouve qu'une recherche ait eu lieu : le verdict est requalifié.
    backend = _FakeBackend(
        parsed={"verdict": "confirme", "confiance": "haute", "sources": []},
        web_searches=0,
    )
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    v = fc.verify_claim(claim("Fait quelconque"), settings=Settings(lexicon_enabled=False))
    assert v.verdict == "introuvable"
    assert v.confiance == "basse"
    assert v.origine == "coerce"


def test_garde_fou_recherche_constatee_laisse_passer_le_verdict(monkeypatch):
    backend = _FakeBackend(
        parsed={
            "verdict": "confirme",
            "confiance": "haute",
            "sources": [{"titre": "Source", "url": "https://example.org"}],
        },
        web_searches=1,
    )
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    v = fc.verify_claim(claim("Fait quelconque"), settings=Settings(lexicon_enabled=False))
    assert v.verdict == "confirme"
    assert v.origine == "web"


def test_quota_atteint_ne_marque_rien_comme_verifie(monkeypatch):
    class _QuotaBackend:
        def is_available(self):
            return True, "ok"

        def complete(self, **kwargs):
            raise QuotaExhausted("limite atteinte")

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: _QuotaBackend())

    v = fc.verify_claim(claim("Fait quelconque"), settings=Settings(lexicon_enabled=False))
    assert v.verdict == "introuvable"
    assert v.confiance == "basse"
    assert v.origine == "quota"


def test_lexique_verifie_resout_sans_recherche(monkeypatch):
    term = type(
        "T",
        (),
        {"terme": "DIPM", "categorie": "acte", "sources": [], "wikilink": "DIPM"},
    )()
    monkeypatch.setattr(fc, "lexicon_lookup", lambda citation, verified_only=True: term)

    def explose(**kwargs):
        raise AssertionError("le back-end n'aurait pas dû être appelé")

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: type("B", (), {"complete": staticmethod(explose)})())

    v = fc.verify_claim(claim("le DIPM"), settings=Settings(lexicon_enabled=True))
    assert v.verdict == "confirme"
    assert v.origine == "lexique"
    assert v.confiance == "haute"


# ----------------------------------------------------------------- extraction


def test_extract_claims_ignore_les_citations_vides(monkeypatch):
    backend = _FakeBackend(parsed=[{"citation": "", "type": "nom_propre"}, {"citation": "Jean Dupont", "type": "nom_propre"}])
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    claims = fc.extract_claims("Un texte avec Jean Dupont dedans.", settings=Settings(lexicon_enabled=False))
    assert len(claims) == 1
    assert claims[0].citation == "Jean Dupont"


def test_extract_claims_annulation(monkeypatch):
    backend = _FakeBackend(parsed=[])
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    with pytest.raises(ProofreadError, match="annulé"):
        fc.extract_claims(
            "Un texte assez long pour former un bloc. " * 50,
            settings=Settings(lexicon_enabled=False),
            should_cancel=lambda: True,
        )


def test_texte_sans_affirmation_est_inchange(monkeypatch):
    backend = _FakeBackend(parsed=[])
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    text = "Un cours sans rien de vérifiable."
    new_text, report, entities = fc.factcheck(text, settings=Settings(lexicon_enabled=False))
    assert new_text == text
    assert report.claims_checked == 0
    assert entities == []
