"""Ancrage terminologique léger : un indice ciblé, jamais une réécriture."""
from app.config import Settings
from app.proofread.grounding import grounding_hints


def test_suggere_une_graphie_du_lexique_pour_le_passage():
    hint = grounding_hints("le document DIPEM remis au majeur protégé")
    assert "DIPM" in hint
    assert "DIPEM" in hint


def test_aucun_terme_ambigu_rend_une_chaine_vide():
    assert grounding_hints("un cours de mathématiques quelconque") == ""


def test_texte_vide_rend_une_chaine_vide():
    assert grounding_hints("") == ""


def test_respecte_lexicon_enabled_desactive():
    hint = grounding_hints("le document DIPEM remis au majeur protégé", Settings(lexicon_enabled=False))
    assert hint == ""
