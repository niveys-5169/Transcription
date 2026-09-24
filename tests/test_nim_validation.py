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


def test_accepte_relecture_normale_article_code_civil():
    raw = "alors aujourd'hui on va parler de l'article 440 du code civil"
    candidate = "Aujourd'hui, on va parler de l'article 440 du Code civil."

    assert validate_proofread_candidate(raw, candidate).valid is True


def test_rejette_preambule_chatbot_sur_premiere_ligne():
    raw = "alors aujourd'hui on va parler de l'article 440 du code civil"
    candidate = (
        "Bien sûr, voici le texte corrigé :\n\n"
        "Aujourd'hui, on va parler de l'article 440 du Code civil."
    )

    result = validate_proofread_candidate(raw, candidate)
    assert result.valid is False
    assert "prefix_misaligned" in result.reasons


def test_rejette_reponse_anglaise_totalement_hors_sujet():
    raw = "Aujourd'hui nous étudions la mesure de tutelle."
    candidate = "I need to analyse the user's request and determine how best to rewrite the passage."

    result = validate_proofread_candidate(raw, candidate)
    assert result.valid is False
    assert set(result.reasons) & {"prefix_misaligned", "language_mismatch", "reasoning_leak"}


def test_accepte_anglicismes_techniques_isoles():
    raw = "Nous utilisons GitHub Actions pour lancer le workflow, faire un commit et appeler une API."
    candidate = "Nous utilisons GitHub Actions pour lancer le workflow, faire un commit et appeler une API."

    assert validate_proofread_candidate(raw, candidate).valid is True


def test_accepte_entite_du_lexique_normalisee_depuis_une_variante():
    raw = "Le dossier est transmis à l'U.D.A.F. pour assurer le suivi de la mesure."
    candidate = "Le dossier est transmis à l'UDAF pour assurer le suivi de la mesure."

    assert validate_proofread_candidate(raw, candidate).valid is True


def test_rejette_entite_metier_reellement_introduite():
    raw = "Le dossier est transmis au service compétent pour assurer le suivi de la mesure."
    candidate = "Le dossier est transmis à l'ARS compétente pour assurer le suivi de la mesure."

    result = validate_proofread_candidate(raw, candidate)
    assert result.valid is False
    assert "ungrounded_entity" in result.reasons
    assert "Agence régionale de santé" in result.metrics["entity_grounding"]["introduced_entities"]


def test_non_regression_corrections_locales_acceptees():
    cases = [
        ("bonjour comment allez vous aujourd hui", "Bonjour, comment allez-vous aujourd'hui ?"),
        ("euh nous allons euh commencer la mesure", "Nous allons commencer la mesure."),
        ("je je dois transmettre le dossier demain", "Je dois transmettre le dossier demain."),
        ("le délai est de 20 jours pour répondre", "Le délai est de vingt jours pour répondre."),
        ("le document DIPEM est remis au majeur protégé", "Le document DIPM est remis au majeur protégé."),
    ]

    for raw, candidate in cases:
        result = validate_proofread_candidate(raw, candidate)
        assert result.valid is True, (raw, candidate, result.reasons)


def test_rejette_un_raisonnement_anglais_noye_dans_un_long_passage_francais():
    raw = (
        "le juge reçoit la requête puis consulte le certificat médical détaillé il vérifie la "
        "situation familiale les ressources les charges et les besoins de la personne concernée "
        "avant de l entendre avec son avocat puis il rend une décision motivée proportionnée et "
        "limitée dans le temps"
    )
    candidate = (
        "Le juge reçoit la requête, puis consulte le certificat médical détaillé. Il vérifie la "
        "situation familiale, les ressources, les charges et les besoins de la personne concernée. "
        "We need to carefully rewrite this paragraph before continuing. Il l'entend avec son "
        "avocat, puis rend une décision motivée, proportionnée et limitée dans le temps."
    )

    result = validate_proofread_candidate(raw, candidate)

    assert result.valid is False
    assert "language_mismatch" in result.reasons


def test_rejette_un_metadiscours_francais_noye_dans_un_long_passage():
    raw = (
        "le juge reçoit la requête puis consulte le certificat médical détaillé il vérifie la "
        "situation familiale les ressources les charges et les besoins de la personne concernée "
        "avant de l entendre avec son avocat puis il rend une décision motivée proportionnée et "
        "limitée dans le temps"
    )
    candidate = (
        "Le juge reçoit la requête, puis consulte le certificat médical détaillé. Il vérifie la "
        "situation familiale, les ressources, les charges et les besoins de la personne concernée. "
        "Il faut analyser ce passage avant de poursuivre la correction. Il l'entend avec son avocat, "
        "puis rend une décision motivée, proportionnée et limitée dans le temps."
    )

    result = validate_proofread_candidate(raw, candidate)

    assert result.valid is False
    assert "reasoning_leak" in result.reasons


def test_accepte_une_citation_anglaise_deja_presente_dans_le_brut():
    raw = (
        "dans le cours l intervenant cite la phrase we need to carefully rewrite this paragraph "
        "before continuing puis il explique pourquoi cette consigne anglaise ne doit pas apparaître "
        "dans le texte relu"
    )
    candidate = (
        "Dans le cours, l'intervenant cite la phrase « We need to carefully rewrite this paragraph "
        "before continuing », puis il explique pourquoi cette consigne anglaise ne doit pas "
        "apparaître dans le texte relu."
    )

    assert validate_proofread_candidate(raw, candidate).valid is True


def test_accepte_analyser_une_situation_juridique_sans_metadiscours_editorial():
    raw = (
        "pour statuer il faut analyser la situation juridique les ressources et les besoins de la "
        "personne avant de choisir une mesure proportionnée"
    )
    candidate = (
        "Pour statuer, il faut analyser la situation juridique, les ressources et les besoins de la "
        "personne avant de choisir une mesure proportionnée."
    )

    assert validate_proofread_candidate(raw, candidate).valid is True


def test_rejette_un_candidat_contenant_u_fffd():
    raw = "La tutelle prévoit un certificat médical."
    result = validate_proofread_candidate(raw, "La tutelle pr\ufffdvoit un certificat m\ufffd\ufffddical.")
    assert result.valid is False
    assert "replacement_char" in result.reasons


def test_rejette_une_boucle_introduite_par_le_modele():
    raw = "Le compte rendu de gestion, le CRG, est remis chaque année au juge."
    candidate = raw + " " + "Le compte rendu de gestion, le CRG, " * 4
    result = validate_proofread_candidate(raw, candidate)
    assert result.valid is False
    assert "repetition_loop" in result.reasons


def test_accepte_une_relecture_accentuee_fidele():
    raw = "oeuvre a cote la tutelle prevoit un certificat medical ca reste pret et tres sur"
    candidate = "Œuvre à côté : la tutelle prévoit un certificat médical, ça reste prêt et très sûr."
    result = validate_proofread_candidate(raw, candidate)
    assert "replacement_char" not in result.reasons
    assert "repetition_loop" not in result.reasons
