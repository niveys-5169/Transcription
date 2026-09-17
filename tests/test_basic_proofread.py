from app.proofread import basic

from conftest import segment


def test_strip_fillers_retire_les_hesitations():
    assert basic.strip_fillers("alors euh on reprend") == "alors on reprend"
    assert basic.strip_fillers("Euh, voilà") == "voilà"


def test_strip_fillers_ne_touche_pas_aux_mots_qui_les_contiennent():
    assert basic.strip_fillers("le pneu heurte le mur") == "le pneu heurte le mur"
    assert basic.strip_fillers("bahut") == "bahut"


def test_collapse_repeats_supprime_les_begaiements():
    assert basic.collapse_repeats("je je pense que") == "je pense que"
    assert basic.collapse_repeats("le le le point") == "le point"


def test_collapse_repeats_epargne_les_doublons_legitimes():
    assert basic.collapse_repeats("nous nous sommes vus") == "nous nous sommes vus"
    assert basic.collapse_repeats("c'est très très clair") == "c'est très très clair"


def test_fix_typography_espaces_et_majuscules():
    assert basic.fix_typography("bonjour . ça va ?") == "Bonjour. Ça va ?"
    assert basic.fix_typography("un  double   espace") == "Un double espace"
    assert basic.fix_typography("il dit« bonjour »") == "Il dit « bonjour »"


def test_fix_typography_majuscule_apres_ponctuation_forte():
    assert basic.fix_typography("un point. deux points ! trois") == (
        "Un point. Deux points ! Trois"
    )


def test_split_paragraphs_coupe_sur_une_vraie_pause():
    long_text = "Nous parlons du sujet pendant un moment assez long. " * 6
    segments = [
        segment(0.0, 30.0, long_text),
        segment(32.0, 40.0, "Passons maintenant au point suivant."),
    ]
    paragraphes = basic.split_paragraphs(segments)
    assert len(paragraphes) == 2
    assert paragraphes[1].startswith("Passons")


def test_split_paragraphs_ignore_les_pauses_trop_precoces():
    segments = [
        segment(0.0, 2.0, "Bonjour."),
        segment(10.0, 12.0, "On commence."),
    ]
    # La pause est longue mais le paragraphe est encore trop court pour
    # justifier une coupure.
    assert len(basic.split_paragraphs(segments)) == 1


def test_split_paragraphs_separe_les_tours_de_parole_identifies():
    segments = [
        {"start": 0, "end": 1, "text": "Bonjour.", "speaker": "SPEAKER_00"},
        {"start": 1, "end": 2, "text": "Bienvenue.", "speaker": "SPEAKER_00"},
        {"start": 2, "end": 3, "text": "Merci.", "speaker": "SPEAKER_01"},
    ]

    paragraphs = basic.split_paragraph_spans(segments)

    assert [paragraph.text for paragraph in paragraphs] == ["Bonjour. Bienvenue.", "Merci."]
    assert [(paragraph.first_segment_index, paragraph.last_segment_index) for paragraph in paragraphs] == [(1, 2), (3, 3)]


def test_basic_proofread_produit_un_texte_propre():
    segments = [
        segment(0.0, 3.0, "alors euh bonjour à à tous"),
        segment(3.0, 6.0, "on commence le cours ."),
    ]
    resultat = basic.basic_proofread(segments)
    assert resultat.mode == "basic"
    assert "euh" not in resultat.text
    assert "à à" not in resultat.text
    assert resultat.text.startswith("Alors")
    assert resultat.text.endswith("cours.")


def test_basic_proofread_sur_liste_vide():
    assert basic.basic_proofread([]).text == ""
