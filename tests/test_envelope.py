"""Contrat de sortie NIM : extraction stricte, jamais devinée."""
from app.proofread.envelope import extract_candidate, extract_delimited, extract_structured
from app.proofread.nim_profiles import NimModelProfile

_STRUCTURED_PROFILE = NimModelProfile(pattern="x", reasoning_mode="none", supports_structured_output=True)
_PLAIN_PROFILE = NimModelProfile(pattern="x", reasoning_mode="none", supports_structured_output=False)


def test_extrait_le_contenu_delimite():
    response = "<transcription>Bonjour à tous.</transcription>"
    assert extract_delimited(response) == "Bonjour à tous."


def test_ignore_tout_ce_qui_est_hors_de_l_enveloppe():
    response = "We need to apply the rules.\n<transcription>Le texte relu.</transcription>\nFin."
    assert extract_delimited(response) == "Le texte relu."


def test_enveloppe_absente_est_invalide_jamais_devinee():
    response = "We need to apply the rules. The passage is raw transcription... Paragraph 1: ..."
    assert extract_delimited(response) is None


def test_enveloppe_vide_est_invalide():
    assert extract_delimited("<transcription>   </transcription>") is None


def test_derniere_enveloppe_retenue_si_plusieurs():
    response = "<transcription>brouillon</transcription> puis <transcription>version finale</transcription>"
    assert extract_delimited(response) == "version finale"


def test_extraction_structuree_json_valide():
    assert extract_structured('{"text": "Bonjour à tous."}') == "Bonjour à tous."


def test_extraction_structuree_json_invalide_est_none():
    assert extract_structured("We need to apply the rules...") is None
    assert extract_structured('{"other": "champ"}') is None
    assert extract_structured('{"text": ""}') is None


def test_extract_candidate_utilise_structure_si_supportee():
    assert extract_candidate('{"text": "ok"}', profile=_STRUCTURED_PROFILE) == "ok"


def test_extract_candidate_retombe_sur_enveloppe_si_json_absent():
    response = "<transcription>ok</transcription>"
    assert extract_candidate(response, profile=_STRUCTURED_PROFILE) == "ok"


def test_extract_candidate_profil_non_structure_ignore_le_json():
    # Un modèle sans structured output ne doit jamais voir son JSON "deviné" :
    # seule l'enveloppe délimitée est reconnue.
    assert extract_candidate('{"text": "ok"}', profile=_PLAIN_PROFILE) is None
