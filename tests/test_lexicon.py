"""Tests du lexique MJPM : chargement, amorçage, rapprochements, vérification."""
import json

import pytest

from app import config, db
from app.config import Settings
from app.proofread.backends.base import BackendResult
from app import lexicon as lex


@pytest.fixture(autouse=True)
def _reset_cache():
    """Le lexique est mis en cache au premier chargement : on le vide entre
    les tests pour que ceux qui touchent au lexique utilisateur ne se
    marchent pas dessus."""
    lex._cache = None
    yield
    lex._cache = None


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Base isolée par test : ``verify_claim`` mémorise ses verdicts (voir
    ``db.factcheck_cache_put``). Sans cette isolation, un test qui confirme
    une entrée laisserait son verdict au suivant, qui vérifie justement
    qu'on ne marque rien sans verdict positif — et le lirait depuis le
    cache au lieu de la doublure."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.sqlite3")
    db.init_db()


def test_le_lexique_livre_est_charge():
    terms = lex.load_lexicon()
    assert len(terms) > 20
    assert any(t.terme == "Sauvegarde de justice" for t in terms)


def test_toutes_les_entrees_livrees_sont_non_verifiees():
    # Le lexique livré est un point de départ écrit à la main, jamais une
    # source d'autorité tant qu'il n'est pas passé par `verify_lexicon`.
    assert all(not t.verifie for t in lex.load_lexicon())


def test_lookup_exclut_les_entrees_non_verifiees_par_defaut():
    assert lex.lookup("le DIPM a été remis") is None


def test_lookup_trouve_les_entrees_non_verifiees_si_demande():
    term = lex.lookup("le DIPM a été remis", verified_only=False)
    assert term is not None
    assert "DIPM" in term.sigles


def test_lookup_ne_trouve_rien_hors_sujet():
    assert lex.lookup("un texte sans rapport", verified_only=False) is None


def test_whisper_prompt_reste_sous_le_plafond():
    prompt = lex.whisper_prompt()
    assert len(prompt) <= lex.WHISPER_PROMPT_MAX_CHARS
    assert "MJPM" in prompt


def test_whisper_prompt_contient_des_sigles_connus():
    prompt = lex.whisper_prompt()
    assert "DIPM" in prompt


def test_glossary_block_liste_les_termes():
    block = lex.glossary_block()
    assert "DIPM" in block
    assert "MJPM" in block


def test_near_misses_rapproche_une_graphie_deformee():
    resultats = lex.near_misses("le document DIPEM remis au majeur")
    mots = [m for m, _ in resultats]
    assert "DIPEM" in mots


def test_near_misses_ignore_les_mots_sans_rapport():
    resultats = lex.near_misses("un cours de mathématiques quelconque")
    assert resultats == []


def test_near_misses_ignore_une_correspondance_exacte():
    # « MJPM » est déjà la graphie connue de ce terme précis : il ne peut pas
    # ressortir comme une quasi-erreur de lui-même (il peut, légitimement,
    # ressortir comme proche d'un tout autre sigle — DIPM par exemple).
    mjpm_terme = next(t for t in lex.load_lexicon() if "MJPM" in t.sigles)
    resultats = lex.near_misses("le MJPM a été désigné")
    assert not any(terme is mjpm_terme for _, terme in resultats)


# ------------------------------------------------------------ lexique utilisateur


def test_save_user_term_ajoute_une_entree(tmp_path, monkeypatch):
    monkeypatch.setattr(lex, "_user_lexicon_path", lambda: tmp_path / "lexique_utilisateur.json")
    lex.save_user_term(lex.Term(terme="Terme test", categorie="autre"))

    terms = lex.load_lexicon(refresh=True)
    assert any(t.terme == "Terme test" for t in terms)


def test_save_user_term_remplace_une_entree_existante(tmp_path, monkeypatch):
    path = tmp_path / "lexique_utilisateur.json"
    monkeypatch.setattr(lex, "_user_lexicon_path", lambda: path)

    lex.save_user_term(lex.Term(terme="Terme test", definition="v1"))
    lex.save_user_term(lex.Term(terme="Terme test", definition="v2"))

    data = json.loads(path.read_text(encoding="utf-8"))
    matches = [e for e in data if e["terme"] == "Terme test"]
    assert len(matches) == 1
    assert matches[0]["definition"] == "v2"


def test_lexique_utilisateur_remplace_une_entree_livree(tmp_path, monkeypatch):
    monkeypatch.setattr(lex, "_user_lexicon_path", lambda: tmp_path / "lexique_utilisateur.json")
    lex.save_user_term(lex.Term(terme="DIPM", definition="Redéfini par l'utilisateur"))

    terms = lex.load_lexicon(refresh=True)
    matches = [t for t in terms if t.terme == "DIPM"]
    assert len(matches) == 1
    assert matches[0].definition == "Redéfini par l'utilisateur"


def test_suppression_utilisateur_retablit_le_lexique_livre(tmp_path, monkeypatch):
    monkeypatch.setattr(lex, "_user_lexicon_path", lambda: tmp_path / "lexique_utilisateur.json")
    terme_livre = "Sauvegarde de justice"
    lex.save_user_term(lex.Term(terme=terme_livre, definition="Remplacement local"))

    assert lex.delete_user_term(terme_livre) is True
    assert lex.delete_user_term(terme_livre) is False
    restored = next(term for term in lex.load_lexicon(refresh=True) if term.terme == terme_livre)
    assert restored.definition != "Remplacement local"


# ------------------------------------------------------------------- verify CLI


@pytest.fixture
def lexicon_copy(tmp_path, monkeypatch):
    """Copie du lexique livré, jamais le vrai fichier — voir test plus bas
    qui garantit explicitement que mjpm.json n'est jamais touché ici."""
    from app.lexicon import __main__ as cli

    work = tmp_path / "mjpm.json"
    work.write_text(lex.LEXICON_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(cli, "LEXICON_PATH", work)
    monkeypatch.setattr(lex, "LEXICON_PATH", work)
    return work


class _FakeBackend:
    def __init__(self, verdict="confirme", confiance="haute", sources=None):
        self.verdict = verdict
        self.confiance = confiance
        self.sources = sources if sources is not None else [{"titre": "Source", "url": "https://exemple.org"}]

    def is_available(self):
        return True, "ok"

    def complete(self, **kwargs):
        return BackendResult(
            text="{}",
            web_searches=1,
            sources=[s["url"] for s in self.sources],
            parsed={"verdict": self.verdict, "confiance": self.confiance, "sources": self.sources},
        )


def test_verify_lexicon_ecrit_les_entrees_confirmees(lexicon_copy, monkeypatch):
    from app.lexicon import __main__ as cli
    import app.proofread.factcheck as fc

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: _FakeBackend())

    count = cli.verify_lexicon(dry_run=False)
    assert count > 0

    data = json.loads(lexicon_copy.read_text(encoding="utf-8"))
    assert all(e["verifie"] for e in data)
    assert all(e["sources"] for e in data)


def test_verify_lexicon_dry_run_n_ecrit_rien(lexicon_copy, monkeypatch):
    from app.lexicon import __main__ as cli
    import app.proofread.factcheck as fc

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: _FakeBackend())
    original = lexicon_copy.read_text(encoding="utf-8")

    count = cli.verify_lexicon(dry_run=True)
    assert count > 0
    assert lexicon_copy.read_text(encoding="utf-8") == original


def test_verify_lexicon_ne_marque_rien_sans_verdict_positif(lexicon_copy, monkeypatch):
    from app.lexicon import __main__ as cli
    import app.proofread.factcheck as fc

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: _FakeBackend(verdict="introuvable", confiance="basse", sources=[]))

    count = cli.verify_lexicon(dry_run=False)
    assert count == 0
    data = json.loads(lexicon_copy.read_text(encoding="utf-8"))
    assert not any(e["verifie"] for e in data)


def test_verify_lexicon_sans_backend_disponible_ne_plante_pas(lexicon_copy, monkeypatch):
    from app.lexicon import __main__ as cli
    import app.proofread.factcheck as fc

    class _Unavailable:
        def is_available(self):
            return False, "indisponible"

    monkeypatch.setattr(fc, "get_backend", lambda settings=None: _Unavailable())

    count = cli.verify_lexicon(dry_run=False)
    assert count == 0
