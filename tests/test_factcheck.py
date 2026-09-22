"""Tests du fact-check : extraction, vérification, application des verdicts.

Le point le plus important de ces tests : le garde-fou mécanique qui empêche
un verdict rendu de mémoire, sans recherche web effective, de passer pour une
confirmation (voir test_garde_fou_*). C'est le cœur de la demande — flaguer
l'incertitude résiduelle plutôt que lisser le texte en une version fluide
mais faussement définitive.
"""
import pytest

from app import config, db
from app.config import Settings
from app.proofread.backends.base import BackendResult
from app.proofread.base import ProofreadError
from app.proofread.backends.cli import QuotaExhausted
from app.proofread import factcheck as fc


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # verify_claim() consulte désormais le cache de verdicts à chaque appel :
    # une base isolée par test évite toute contamination entre eux (voir la
    # note sur les tests de cache en tête de fichier).
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.sqlite3")
    db.init_db()


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


# --------------------------------------------------------------- périmètre


def test_is_in_scope_type_hors_categories_prioritaires():
    settings = Settings(factcheck_priority_types="reference_juridique,date,organisme")
    assert not fc.is_in_scope(claim("Jean Dupont", type="nom_propre"), settings)


def test_is_in_scope_reference_juridique_toujours_dans_le_perimetre():
    settings = Settings(factcheck_priority_types="reference_juridique,date,organisme")
    assert fc.is_in_scope(claim("article 440 du code civil", type="reference_juridique"), settings)


def test_is_in_scope_organisme_reconnu_par_le_lexique(monkeypatch):
    term = type("T", (), {"categorie": "acteur"})()
    monkeypatch.setattr(fc, "lexicon_lookup", lambda citation, verified_only=True: term if not verified_only else None)
    settings = Settings(factcheck_priority_types="reference_juridique,date,organisme", lexicon_enabled=True)
    assert fc.is_in_scope(claim("MDPH", type="organisme"), settings)


def test_is_in_scope_organisme_quelconque_hors_lexique_et_hors_legalref(monkeypatch):
    monkeypatch.setattr(fc, "lexicon_lookup", lambda citation, verified_only=True: None)
    settings = Settings(factcheck_priority_types="reference_juridique,date,organisme", lexicon_enabled=True)
    assert not fc.is_in_scope(claim("Société Dupont SARL", type="organisme"), settings)


class _Backend:
    """Faux back-end distinguant l'appel d'extraction (schéma CLAIMS_SCHEMA)
    de l'appel de verdict (VERDICT_SCHEMA), pour les tests bout en bout de
    ``factcheck()``."""

    def __init__(self, claims_parsed, verdict_parsed=None, web_searches=1):
        self.claims_parsed = claims_parsed
        self.verdict_parsed = verdict_parsed or {
            "verdict": "confirme",
            "confiance": "haute",
            "sources": [{"titre": "Source", "url": "https://example.org/ref"}],
        }
        self.web_searches = web_searches
        self.calls: list[dict] = []

    def is_available(self):
        return True, "ok"

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("schema") is fc.CLAIMS_SCHEMA:
            return BackendResult(text="", web_searches=0, sources=[], parsed=self.claims_parsed)
        return BackendResult(text="", web_searches=self.web_searches, sources=[], parsed=self.verdict_parsed)

    def verdict_calls(self):
        return [c for c in self.calls if c.get("schema") is fc.VERDICT_SCHEMA]


def test_nom_propre_ne_declenche_aucun_appel_et_va_dans_skipped(monkeypatch):
    backend = _Backend(claims_parsed=[{"citation": "Jean Dupont", "type": "nom_propre"}])
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    text = "Jean Dupont est intervenu."
    new_text, report, entities = fc.factcheck(text, settings=Settings(lexicon_enabled=False))
    assert backend.verdict_calls() == []
    assert report.skipped == [{"type": "nom_propre", "citation": "Jean Dupont"}]
    assert new_text == text  # aucune note ajoutée
    assert report.claims_checked == 0


def test_organisme_reconnu_verifie_organisme_quelconque_non(monkeypatch):
    def fake_lookup(citation, verified_only=True):
        if verified_only:
            return None
        if "MDPH" in citation:
            return type("T", (), {"categorie": "acteur"})()
        return None

    monkeypatch.setattr(fc, "lexicon_lookup", fake_lookup)
    backend = _Backend(
        claims_parsed=[
            {"citation": "MDPH", "type": "organisme"},
            {"citation": "Société Dupont SARL", "type": "organisme"},
        ],
    )
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    text = "La MDPH et la Société Dupont SARL sont intervenues."
    new_text, report, entities = fc.factcheck(text, settings=Settings(lexicon_enabled=True))
    assert len(backend.verdict_calls()) == 1
    assert {"type": "organisme", "citation": "Société Dupont SARL"} in report.skipped
    assert report.claims_checked == 1


def test_reference_juridique_est_toujours_verifiee(monkeypatch):
    backend = _Backend(claims_parsed=[{"citation": "article 440 du code civil", "type": "reference_juridique"}])
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    text = "L'article 440 du code civil encadre la mesure."
    new_text, report, entities = fc.factcheck(text, settings=Settings(lexicon_enabled=False))
    assert len(backend.verdict_calls()) == 1
    assert report.skipped == []
    assert report.claims_checked == 1


def test_deux_occurrences_meme_citation_un_seul_appel_deux_notes(monkeypatch):
    backend = _Backend(
        claims_parsed=[
            {"citation": "juge des tutelles", "type": "organisme"},
            {"citation": "juge des tutelles", "type": "organisme"},
        ],
        verdict_parsed={
            "verdict": "infirme",
            "confiance": "haute",
            "sources": [{"titre": "Source", "url": "https://example.org/ref"}],
        },
    )
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    text = "Le juge des tutelles a validé la mesure. Le juge des tutelles a validé aussi."
    new_text, report, entities = fc.factcheck(text, settings=Settings(lexicon_enabled=False))
    assert len(backend.verdict_calls()) == 1  # une seule vérification pour les deux occurrences
    assert new_text.count("[^v1]:") == 1
    assert new_text.count("[^v2]:") == 1  # mais bien deux notes, une par occurrence


# --------------------------------------------------------------- cache


def test_cache_premier_appel_reel_second_appel_depuis_le_cache(monkeypatch):
    calls = {"n": 0}

    class _CountingBackend:
        def is_available(self):
            return True, "ok"

        def complete(self, **kwargs):
            calls["n"] += 1
            return BackendResult(
                text="",
                web_searches=1,
                sources=[],
                parsed={
                    "verdict": "confirme",
                    "confiance": "haute",
                    "sources": [{"titre": "Source", "url": "https://example.org/ref"}],
                },
            )

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: _CountingBackend())
    settings = Settings(lexicon_enabled=False)
    c = claim("Code civil, article 999", type="reference_juridique")

    v1 = fc.verify_claim(c, settings=settings)
    assert v1.origine == "web"
    assert calls["n"] == 1

    v2 = fc.verify_claim(c, settings=settings)
    assert v2.origine == "cache"
    assert calls["n"] == 1  # pas de second appel réseau
    assert v2.sources and v2.sources[0].url == "https://example.org/ref"


def test_quota_et_coerce_ne_sont_jamais_mis_en_cache(monkeypatch):
    class _QuotaBackend:
        def is_available(self):
            return True, "ok"

        def complete(self, **kwargs):
            raise QuotaExhausted("limite atteinte")

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: _QuotaBackend())
    settings = Settings(lexicon_enabled=False)
    c_quota = claim("Une citation en quota", type="reference_juridique")
    v_quota = fc.verify_claim(c_quota, settings=settings)
    assert v_quota.origine == "quota"
    assert db.factcheck_cache_get(fc.cache_key(c_quota.type, c_quota.citation), max_age_days=90) is None

    coerce_backend = _FakeBackend(
        parsed={"verdict": "confirme", "confiance": "haute", "sources": []}, web_searches=0
    )
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: coerce_backend)
    c_coerce = claim("Une autre citation sans recherche", type="reference_juridique")
    v_coerce = fc.verify_claim(c_coerce, settings=settings)
    assert v_coerce.origine == "coerce"
    assert db.factcheck_cache_get(fc.cache_key(c_coerce.type, c_coerce.citation), max_age_days=90) is None


# ------------------------------------------------ court-circuit lexique (référence)


def test_lexique_reference_court_circuite_une_reference_juridique(monkeypatch):
    term = type(
        "T",
        (),
        {
            "terme": "Article 440 du code civil",
            "categorie": "texte",
            "reference": "article 440",
            "sources": [],
            "verifie": True,
        },
    )()
    monkeypatch.setattr(fc, "lexicon_lookup", lambda citation, verified_only=True: None)
    monkeypatch.setattr(fc, "lexicon_load_all", lambda: [term])

    def explose(**kwargs):
        raise AssertionError("le back-end n'aurait pas dû être appelé")

    monkeypatch.setattr(
        fc, "get_backend", lambda settings=None: type("B", (), {"complete": staticmethod(explose)})()
    )

    c = claim("L'article 440 du Code civil", type="reference_juridique")
    v = fc.verify_claim(c, settings=Settings(lexicon_enabled=True))
    assert v.verdict == "confirme"
    assert v.origine == "lexique"
    assert v.confiance == "haute"


# ------------------------------------------------------ parallélisme des verdicts


def test_numerotation_des_notes_identique_a_1_ou_4_workers(monkeypatch):
    citations = [{"citation": f"Référence numéro {i}", "type": "reference_juridique"} for i in range(5)]
    text = " ".join(f"Référence numéro {i} est citée dans ce cours." for i in range(5))
    verdict_parsed = {
        "verdict": "infirme",
        "confiance": "haute",
        "sources": [{"titre": "Source", "url": "https://example.org/ref"}],
    }

    monkeypatch.setattr(
        fc, "get_backend", lambda settings=None: _Backend(claims_parsed=citations, verdict_parsed=verdict_parsed)
    )
    text_1, report_1, _ = fc.factcheck(text, settings=Settings(lexicon_enabled=False, factcheck_workers=1))

    monkeypatch.setattr(
        fc, "get_backend", lambda settings=None: _Backend(claims_parsed=citations, verdict_parsed=verdict_parsed)
    )
    text_4, report_4, _ = fc.factcheck(text, settings=Settings(lexicon_enabled=False, factcheck_workers=4))

    assert text_1 == text_4
    assert report_1.claims_checked == report_4.claims_checked == 5


def test_annulation_pendant_les_verdicts_leve_proofread_error(monkeypatch):
    backend = _Backend(claims_parsed=[{"citation": "Un fait cité", "type": "reference_juridique"}])
    monkeypatch.setattr(fc, "get_backend", lambda settings=None: backend)

    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] > 1  # laisse passer le repérage, annule à la vérification

    with pytest.raises(ProofreadError, match="annulé"):
        fc.factcheck(
            "Un fait cité est mentionné ici.",
            settings=Settings(lexicon_enabled=False),
            should_cancel=should_cancel,
        )
