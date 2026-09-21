"""Validateur local d'un candidat de relecture NIM — le garde-fou principal.

Ces exemples sont synthétiques (aucun contenu du cours privé) mais
reproduisent la forme exacte de l'incident réel : un raisonnement de modèle
substitué au texte relu, assez long pour passer un simple ratio de longueur.
"""
from app.proofread.validation import validate_proofread_candidate

RAW = "La mesure de protection concerne une requête aux fins de résiliation de bail engagée par le curateur."


def test_rejette_le_raisonnement_anglais_reproduisant_l_incident_reel():
    candidate = (
        "We need to apply the rules. The passage is raw transcription, let's examine for errors: "
        "Paragraph 1: the measure concerns a request. Paragraph 2: it was filed by the curator."
    )
    result = validate_proofread_candidate(RAW, candidate, model="nvidia/llama-3.3-nemotron-super-49b-v1")

    assert result.valid is False
    assert "reasoning_leak" in result.reasons
    assert result.metrics["words_raw"] > 0


def test_rejette_les_balises_think_meme_courtes():
    candidate = "<think>Il faut nettoyer ce passage.</think>La mesure de protection concerne une requête."
    result = validate_proofread_candidate(RAW, candidate)

    assert result.valid is False
    assert "model_artifact" in result.reasons
    assert result.metrics["model_artifacts"]


def test_rejette_le_raisonnement_en_francais():
    candidate = (
        "Il faut appliquer les règles. Le passage est une transcription brute qu'il faut corriger. "
        "Examinons les erreurs une par une avant de proposer une version finale complète et détaillée."
    )
    result = validate_proofread_candidate(RAW, candidate)

    assert result.valid is False
    assert "reasoning_leak" in result.reasons


def test_rejette_une_enorme_expansion_sans_aucun_marqueur_de_metadiscours():
    # Pas un seul marqueur de la liste : uniquement le volume doit suffire.
    invented = " ".join(f"motinventé{i}" for i in range(120))
    candidate = f"La mesure de protection concerne une requête aux fins. {invented}"
    result = validate_proofread_candidate(RAW, candidate)

    assert result.valid is False
    assert "too_long" in result.reasons or "long_added_span" in result.reasons


def test_rejette_un_resume_trop_court():
    candidate = "Mesure de protection."
    result = validate_proofread_candidate(RAW, candidate)

    assert result.valid is False
    assert "too_short" in result.reasons


def test_accepte_un_seul_chiffre_corrige_sans_autre_signal():
    raw = "Le dossier numéro 12 a été transmis au juge des tutelles la semaine dernière."
    candidate = "Le dossier numéro 12 a été transmis au juge des tutelles la semaine dernière."
    result = validate_proofread_candidate(raw, candidate)
    assert result.valid is True


def test_rejette_un_changement_de_reference_juridique_associe_a_une_fuite():
    raw = "L'article 415 du code civil encadre la mesure de protection."
    candidate = (
        "We need to apply the rules. L'article 9999 du code civil encadre la mesure. "
        "Let's examine for errors before answering."
    )
    result = validate_proofread_candidate(raw, candidate)

    assert result.valid is False
    assert "reasoning_leak" in result.reasons


def test_rejette_une_phrase_inventee_mais_parfaitement_francaise():
    # Aucun marqueur de méta-discours, aucun artefact : seul le diff
    # structurel (longue séquence continue absente du brut) doit bloquer.
    invented = (
        "et il convient de noter que cette situation particulière a nécessité la mise en place "
        "d'un dispositif spécifique impliquant plusieurs intervenants sociaux et médicaux au cours "
        "des semaines suivantes avant que la décision définitive ne soit rendue par le magistrat"
    )
    candidate = f"{RAW} {invented}."
    result = validate_proofread_candidate(RAW, candidate)

    assert result.valid is False
    assert "long_added_span" in result.reasons


def test_accepte_une_relecture_nim_propre_et_proche_du_brut():
    raw = "alors euh la mesure de protection euh concerne une requête aux fins de résiliation de bail"
    candidate = "La mesure de protection concerne une requête aux fins de résiliation de bail."
    result = validate_proofread_candidate(raw, candidate)

    assert result.valid is True
    assert result.reasons == []


def test_metrics_toujours_peuples():
    result = validate_proofread_candidate(RAW, "La mesure de protection concerne une requête.")
    for key in ("words_raw", "words_candidate", "added_words", "longest_added_span", "ratio"):
        assert key in result.metrics
