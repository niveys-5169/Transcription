"""Tests de la localisation tolérante (empreinte alphanumérique, alignement mot à mot)."""
from app.proofread.textloc import fingerprint, locate, tokenize_words, word_opcodes


def test_fingerprint_ne_garde_que_l_alphanumerique():
    fp, positions = fingerprint("Ah, 42 % !")
    assert fp == "ah42"
    assert positions == [0, 1, 4, 5]


def test_fingerprint_positions_permettent_de_retrouver_l_origine():
    texte = "Bonjour, le monde."
    fp, positions = fingerprint(texte)
    index = fp.find("monde")
    assert texte[positions[index] : positions[index] + len("monde")] == "monde"


def test_locate_trouve_malgre_ponctuation_et_casse():
    texte = "Le second principe introduit la notion d'entropie."
    span = locate(texte, "le SECOND principe, introduit la Notion")
    assert span is not None
    start, end = span
    assert texte[start:end] == "Le second principe introduit la notion"


def test_locate_renvoie_none_si_introuvable():
    assert locate("un texte quelconque", "une phrase absente") is None


def test_locate_renvoie_none_si_trop_court():
    assert locate("un texte quelconque avec ab dedans", "ab") is None


def test_locate_renvoie_none_sur_needle_vide():
    assert locate("un texte", "") is None
    assert locate("un texte", "   ") is None


def test_tokenize_words_donne_les_bornes_d_origine():
    texte = "Bonjour  le monde"
    tokens = tokenize_words(texte)
    assert [mot for mot, _s, _e in tokens] == ["bonjour", "le", "monde"]
    mot, start, end = tokens[-1]
    assert texte[start:end] == "monde"


def test_word_opcodes_repere_une_suppression():
    raw = "un deux trois quatre cinq"
    clean = "un deux cinq"
    opcodes = word_opcodes(raw, clean)
    supressions = [op for op in opcodes if op[0] == "delete"]
    assert len(supressions) == 1
    tag, i1, i2, j1, j2 = supressions[0]
    raw_words = [w for w, _s, _e in tokenize_words(raw)]
    assert raw_words[i1:i2] == ["trois", "quatre"]
    assert j1 == j2  # rien inséré à la place côté relu


def test_word_opcodes_texte_identique_est_tout_egal():
    opcodes = word_opcodes("un deux trois", "un deux trois")
    assert all(tag == "equal" for tag, *_ in opcodes)
