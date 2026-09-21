"""Contrôle qualité ASR : signalement pur, jamais de réécriture des segments."""
import copy

from app.proofread.asr_quality import check_segments


def test_segment_propre_ne_declenche_rien():
    segments = [{"start": 0.0, "end": 3.0, "text": "Bonjour à tous, bienvenue dans ce cours."}]
    assert check_segments(segments) == []


def test_taux_eleve_de_unk_est_signale():
    segments = [{"start": 0.0, "end": 3.0, "text": "<unk> <unk> <unk> le reste du texte est correct"}]
    issues = check_segments(segments)
    assert any(i["issue"] == "unk_ratio_eleve" for i in issues)


def test_boucle_de_tokens_est_signalee():
    segments = [{"start": 0.0, "end": 5.0, "text": "voilà voilà voilà voilà voilà voilà voilà voilà"}]
    issues = check_segments(segments)
    assert any(i["issue"] == "boucle_de_tokens" for i in issues)


def test_texte_vide_sur_une_longue_plage_est_signale():
    segments = [{"start": 0.0, "end": 12.0, "text": ""}]
    issues = check_segments(segments)
    assert any(i["issue"] == "texte_vide_plage_longue" for i in issues)


def test_segment_anormalement_long_est_signale():
    segments = [{"start": 0.0, "end": 60.0, "text": "un segment qui dure beaucoup trop longtemps sans coupure"}]
    issues = check_segments(segments)
    assert any(i["issue"] == "segment_anormalement_long" for i in issues)


def test_ne_modifie_jamais_les_segments():
    segments = [{"start": 0.0, "end": 12.0, "text": ""}, {"start": 12.0, "end": 60.0, "text": "voilà voilà voilà voilà voilà voilà voilà"}]
    before = copy.deepcopy(segments)
    check_segments(segments)
    assert segments == before
