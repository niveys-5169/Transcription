"""Tests de la vérification : règles mécaniques et lecture par Claude."""
import pytest

from app.config import Settings
from app.proofread import verify as verify_module
from app.proofread.backends.base import BackendResult
from app.proofread.base import ProofreadError, TextPair
from app.proofread.verify import (
    ClaudeVerifier,
    VerificationReport,
    pairs_a_risque,
    rule_findings,
    verify,
)


class _FakeBackend:
    """Doublure de back-end : une réponse canée, pas d'appel réel."""

    def __init__(self, text: str = "[]"):
        self.text = text
        self.calls = 0

    def is_available(self):
        return True, "simulé"

    def complete(self, **kwargs):
        self.calls += 1
        return BackendResult(text=self.text)


def pair(raw, clean, start=0.0):
    return TextPair(start=start, end=start + 10.0, raw=raw, clean=clean)


# ------------------------------------------------------------------ règles


def test_un_chiffre_perdu_est_signale():
    findings = rule_findings(
        [pair("le seuil est à 42 degrés", "le seuil est à quelques degrés")]
    )
    assert len(findings) == 1
    assert findings[0].kind == "chiffre"
    assert findings[0].severity == "haute"
    assert "42" in findings[0].message


def test_un_chiffre_conserve_ne_declenche_rien():
    assert rule_findings([pair("il y a 42 cas", "Il y a 42 cas.")]) == []


def test_les_variantes_d_ecriture_ne_sont_pas_des_pertes():
    # « 1 000 » relu en « 1000 », « 10,5 » en « 10.5 » : même nombre.
    assert rule_findings([pair("1 000 euros et 10,5 %", "1000 euros et 10.5 %")]) == []


def test_un_chiffre_ajoute_n_est_pas_une_perte():
    # Écrire « 20 » là où l'oral disait « vingt » est une amélioration.
    assert rule_findings([pair("vingt personnes", "20 personnes")]) == []


def test_les_occurrences_multiples_sont_comptees():
    findings = rule_findings([pair("2 et 2 font 4", "2 et 4")])
    chiffres = [f for f in findings if f.kind == "chiffre"]
    # Le « 2 » est prononcé deux fois et écrit une seule : c'est une perte,
    # même si l'autre occurrence est bien là.
    assert len(chiffres) == 1
    assert "2" in chiffres[0].message


def test_un_sigle_perdu_est_signale():
    findings = rule_findings([pair("la molécule d'ADN", "la molécule")])
    assert len(findings) == 1
    assert findings[0].kind == "terme"
    assert "ADN" in findings[0].message


def test_un_passage_qui_fond_est_signale():
    findings = rule_findings([pair("mot " * 100, "mot " * 20)])
    assert any(f.kind == "coupure" and f.severity == "haute" for f in findings)


def test_le_retrait_des_hesitations_ne_declenche_pas_d_alerte():
    brut = "alors euh donc voilà on reprend le cours de thermodynamique ici"
    relu = "Donc on reprend le cours de thermodynamique ici."
    assert [f.kind for f in rule_findings([pair(brut, relu)])] == []


def test_aucune_paire_aucun_signalement():
    assert rule_findings([]) == []


def test_les_extraits_situent_le_probleme():
    findings = rule_findings([pair("on note bien 42 degrés ici", "on note bien degrés ici")])
    assert "42" in findings[0].raw_excerpt
    # Le voisinage du chiffre disparu (« degrés ici ») est bien resté dans le
    # texte relu : l'extrait doit pointer dessus, pas sur le début du passage.
    assert "ici" in findings[0].clean_excerpt


def test_deux_chiffres_distincts_ont_des_extraits_distincts():
    # Les deux chiffres manquants sont loin l'un de l'autre (largement plus de
    # 120 caractères) : une implémentation qui retomberait sur le début du
    # passage relu produirait deux fois le même extrait.
    raw = (
        "Nous allons évoquer aujourd'hui plusieurs chiffres qui comptent vraiment "
        "pour la suite de cet exposé avant de passer à autre chose. "
        "Le premier chiffre à retenir est 12 comme le nombre de jours nécessaires "
        "pour la première étape du dossier. "
        "Un peu plus loin dans le raisonnement il faut aussi retenir que le second "
        "chiffre à retenir est 99 comme le nombre de dossiers traités cette année là."
    )
    clean = raw.replace("12 ", "").replace("99 ", "")
    assert len(clean) > 250  # largement plus que deux fois EXCERPT_CHARS

    findings = rule_findings([pair(raw, clean)])
    chiffres = [f for f in findings if f.kind == "chiffre"]
    assert len(chiffres) == 2
    extraits = {f.clean_excerpt for f in chiffres}
    assert "" not in extraits
    # Deux anomalies distinctes doivent afficher deux citations distinctes,
    # ni l'une ni l'autre égale au simple début du passage relu.
    assert len(extraits) == 2
    assert clean[:120] not in extraits


def test_extrait_relu_absent_si_le_voisinage_est_introuvable():
    # La phrase a été intégralement récrite à la relecture : ni le chiffre,
    # ni son voisinage (avant ou après), ne se retrouvent dans le texte relu.
    findings = rule_findings(
        [pair("Le capteur indique 42 degrés précisément", "Une anomalie a été détectée sur le site")]
    )
    assert findings[0].clean_excerpt == ""


def test_coupure_localise_le_passage_disparu():
    # Le passage supprimé est loin du début (plus de 120 caractères) : un
    # extrait qui retomberait sur pair.clean[:120] ou pair.raw[:120] ne le
    # verrait pas du tout.
    prefixe = (
        "Nous commençons ce cours par un rapide rappel historique sur "
        "l'organisation judiciaire en France avant d'entrer dans le vif du sujet. "
    )
    passage_disparu = (
        "Il faut noter en particulier que la procédure disciplinaire devant "
        "le conseil régional obéit à des règles très strictes et méconnues. "
    )
    suite = "Passons maintenant au premier point de notre programme d'aujourd'hui."
    assert len(prefixe) > 120

    findings = rule_findings([pair(prefixe + passage_disparu + suite, prefixe + suite)])
    coupures = [f for f in findings if f.kind == "coupure"]
    assert len(coupures) == 1
    assert "procédure disciplinaire" in coupures[0].raw_excerpt
    # Côté relu, l'extrait doit être ancré au point de coupure réel (la
    # jonction entre le préfixe et la suite), pas au début du passage.
    assert "Passons" in coupures[0].clean_excerpt


# ------------------------------------------------------------------ Claude


@pytest.fixture
def verificateur(monkeypatch):
    instance = ClaudeVerifier(Settings(anthropic_api_key="sk-ant-test", claude_backend="api"))
    monkeypatch.setattr(instance, "is_available", lambda: (True, "simulé"))
    return instance


def test_claude_signale_un_glissement_de_sens(verificateur, monkeypatch):
    reponse = """[
      {"type": "sens", "gravite": "haute", "brut": "ne jamais dépasser",
       "relu": "ne pas dépasser souvent", "commentaire": "L'interdiction devient une recommandation."}
    ]"""
    monkeypatch.setattr(verificateur.backend, "complete", lambda **k: BackendResult(text=reponse))

    findings = verificateur.verify([pair("ne jamais dépasser", "ne pas dépasser souvent")])
    assert len(findings) == 1
    assert findings[0].kind == "sens"
    assert findings[0].severity == "haute"
    assert findings[0].source == "claude"
    assert findings[0].start == 0.0


def test_un_tableau_vide_veut_dire_fidele(verificateur, monkeypatch):
    monkeypatch.setattr(verificateur.backend, "complete", lambda **k: BackendResult(text="[]"))
    assert verificateur.verify([pair("un texte", "Un texte.")]) == []


def test_une_reponse_illisible_est_ignoree(verificateur, monkeypatch):
    monkeypatch.setattr(
        verificateur.backend, "complete", lambda **k: BackendResult(text="tout va bien !")
    )
    assert verificateur.verify([pair("un texte", "Un texte.")]) == []


def test_les_types_inconnus_sont_ramenes_a_des_valeurs_sures(verificateur, monkeypatch):
    reponse = '[{"type": "bizarre", "gravite": "catastrophique", "commentaire": "hmm"}]'
    monkeypatch.setattr(verificateur.backend, "complete", lambda **k: BackendResult(text=reponse))

    finding = verificateur.verify([pair("a", "b")])[0]
    assert finding.kind == "sens"
    assert finding.severity == "moyenne"


def test_un_signalement_sans_commentaire_est_jete(verificateur, monkeypatch):
    monkeypatch.setattr(
        verificateur.backend,
        "complete",
        lambda **k: BackendResult(text='[{"type": "sens", "commentaire": "  "}]'),
    )
    assert verificateur.verify([pair("a", "b")]) == []


def test_annulation_pendant_la_verification(verificateur, monkeypatch):
    monkeypatch.setattr(verificateur.backend, "complete", lambda **k: BackendResult(text="[]"))
    with pytest.raises(ProofreadError, match="annulée"):
        verificateur.verify([pair("a", "b")], should_cancel=lambda: True)


def test_verification_indisponible_sans_cle():
    with pytest.raises(ProofreadError):
        ClaudeVerifier(Settings(anthropic_api_key="", claude_backend="api")).verify(
            [pair("a", "b")]
        )


# ---------------------------------------------------------- orchestration


def test_verify_combine_regles_et_claude(monkeypatch):
    reponse = '[{"type": "omission", "gravite": "basse", "commentaire": "Nuance perdue."}]'
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: _FakeBackend(reponse))

    rapport = verify([pair("il y a 42 cas précis", "il y a des cas")])
    sources = {f.source for f in rapport.findings}
    assert sources == {"regles", "claude"}
    assert rapport.mode == "claude-cible"
    assert rapport.claude_pairs == 1


def test_verify_trie_par_gravite(monkeypatch):
    reponse = '[{"type": "sens", "gravite": "basse", "commentaire": "Détail."}]'
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: _FakeBackend(reponse))

    rapport = verify([pair("le seuil de 42 degrés", "le seuil")])
    gravites = [f.severity for f in rapport.findings]
    assert gravites == sorted(gravites, key=lambda g: ["haute", "moyenne", "basse"].index(g))


def test_verify_sans_claude_garde_les_regles():
    rapport = verify([pair("il y a 42 cas", "il y a des cas")], use_claude=False)
    assert rapport.mode == "regles"
    assert len(rapport.findings) == 1


def test_un_echec_de_claude_ne_perd_pas_le_rapport_des_regles(monkeypatch):
    def explose(self, *args, **kwargs):
        raise ProofreadError("API indisponible")

    monkeypatch.setattr(ClaudeVerifier, "is_available", lambda self: (True, "ok"))
    monkeypatch.setattr(ClaudeVerifier, "verify", explose)

    rapport = verify([pair("il y a 42 cas", "il y a des cas")])
    assert rapport.mode == "regles"
    assert len(rapport.findings) == 1


def test_rapport_serialisable():
    rapport = verify([pair("il y a 42 cas", "il y a des cas")], use_claude=False)
    data = rapport.to_dict()
    assert data["counts"]["haute"] == 1
    assert data["checked_pairs"] == 1
    assert data["skipped_pairs"] == 0
    assert isinstance(data["findings"][0], dict)


def test_rapport_vide():
    assert VerificationReport().to_dict()["findings"] == []


def test_les_paires_vides_sont_comptees_comme_non_verifiees():
    rapport = verify(
        [pair("il y a 42 cas", "il y a des cas"), pair("", ""), pair("   ", "aussi vide")],
        use_claude=False,
    )
    assert rapport.skipped_pairs == 2
    assert rapport.checked_pairs == 3


# ------------------------------------------------------------- pairs_a_risque


def test_aucune_paire_a_risque_sur_aucune_paire():
    assert pairs_a_risque([], []) == []


def test_une_paire_propre_n_est_pas_a_risque():
    propre = pair("Donc on reprend le cours de thermodynamique ici.", "Donc on reprend le cours de thermodynamique ici.")
    assert pairs_a_risque([propre], rule_findings([propre])) == []


def test_une_paire_avec_finding_de_regle_est_a_risque():
    p = pair("le seuil est à 42 degrés", "le seuil est à quelques degrés")
    findings = rule_findings([p])
    assert findings  # un finding « chiffre » est bien produit
    assert pairs_a_risque([p], findings) == [p]


def test_une_reference_juridique_est_a_risque_meme_sans_finding_de_regle():
    p = pair(
        "cette mesure relève de l'article L. 471-1 du code de l'action sociale et des familles",
        "cette mesure relève de l'article L. 471-1 du code de l'action sociale et des familles",
    )
    findings = rule_findings([p])
    assert findings == []  # aucune règle ne se déclenche
    assert pairs_a_risque([p], findings) == [p]


def test_pre_alerte_de_longueur_sous_le_seuil_de_la_regle():
    # Ratio de 0,70 : sous PRE_ALERT_RATIO (0.75) mais au-dessus de
    # LENGTH_ALERT_RATIO (0.62), donc pas de finding « coupure ».
    brut = "mot " * 100
    relu = "mot " * 70
    p = pair(brut, relu)
    findings = rule_findings([p])
    assert not any(f.kind == "coupure" for f in findings)
    assert pairs_a_risque([p], findings) == [p]


def test_l_ordre_d_origine_est_preserve():
    propre = pair("Donc on reprend le cours de thermodynamique ici.", "Donc on reprend le cours de thermodynamique ici.", start=0.0)
    risquee = pair("le seuil est à 42 degrés", "le seuil est à quelques degrés", start=10.0)
    autre_propre = pair("Encore une phrase tranquille et bien relue.", "Encore une phrase tranquille et bien relue.", start=20.0)
    pairs = [propre, risquee, autre_propre]
    assert pairs_a_risque(pairs, rule_findings(pairs)) == [risquee]


# --------------------------------------------------- verify : tri ciblé


def test_une_paire_propre_n_est_pas_envoyee_a_claude(monkeypatch):
    fake = _FakeBackend("[]")
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: fake)

    propre = pair("Donc on reprend le cours de thermodynamique ici.", "Donc on reprend le cours de thermodynamique ici.")
    rapport = verify([propre])
    assert fake.calls == 0
    assert rapport.claude_pairs == 0
    assert rapport.mode == "regles"


def test_une_reference_juridique_est_envoyee_a_claude_sans_finding_de_regle(monkeypatch):
    fake = _FakeBackend("[]")
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: fake)

    texte = "cette mesure relève de l'article L. 471-1 du code de l'action sociale et des familles"
    rapport = verify([pair(texte, texte)])
    assert fake.calls == 1
    assert rapport.claude_pairs == 1
    assert rapport.mode == "claude-cible"


def test_une_paire_a_pre_alerte_est_envoyee_meme_sans_regle_de_coupure(monkeypatch):
    fake = _FakeBackend("[]")
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: fake)

    brut = "mot " * 100
    relu = "mot " * 70  # ratio 0,70 : sous PRE_ALERT_RATIO, mais pas la règle « coupure »
    rapport = verify([pair(brut, relu)])
    assert fake.calls == 1
    assert rapport.claude_pairs == 1


def test_claude_pairs_compte_seulement_les_blocs_lus(monkeypatch):
    fake = _FakeBackend("[]")
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: fake)

    propre = pair("Donc on reprend le cours de thermodynamique ici.", "Donc on reprend le cours de thermodynamique ici.", start=0.0)
    risquee = pair("le seuil est à 42 degrés", "le seuil est à quelques degrés", start=10.0)
    rapport = verify([propre, risquee])
    assert rapport.claude_pairs == 1
    assert fake.calls == 1


def test_mode_regles_quand_aucune_paire_a_risque(monkeypatch):
    fake = _FakeBackend("[]")
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: fake)

    propre = pair("Donc on reprend le cours de thermodynamique ici.", "Donc on reprend le cours de thermodynamique ici.")
    rapport = verify([propre])
    assert rapport.mode == "regles"


def test_les_regles_couvrent_tout_le_document_meme_les_blocs_non_lus_par_claude(monkeypatch):
    fake = _FakeBackend("[]")
    monkeypatch.setattr(verify_module, "get_backend", lambda settings=None: fake)

    # Deux paires propres, dont une avec un chiffre perdu (à risque) : les
    # deux doivent produire leurs findings de règle, même si une seule est
    # envoyée à Claude.
    perd_un_chiffre = pair("il y a 42 cas précis", "il y a des cas", start=0.0)
    propre_ailleurs = pair("mot perdu ici", "mot perdu ici", start=10.0)
    rapport = verify([perd_un_chiffre, propre_ailleurs])

    chiffres = [f for f in rapport.findings if f.kind == "chiffre"]
    assert len(chiffres) == 1
    assert rapport.claude_pairs == 1
    assert fake.calls == 1


def test_sans_claude_la_lecture_ciblee_passe_par_nim(monkeypatch):
    from app.config import Settings
    from app.proofread.nim import NimCompletion, NimProofreader

    monkeypatch.setattr(ClaudeVerifier, "is_available", lambda self: (False, "Claude absent"))
    appels = []

    def faux_complete(self, *, system, user, max_tokens, minimum_length=0, fast=False):
        appels.append(fast)
        return NimCompletion(text='[{"type": "sens", "gravite": "haute", "commentaire": "Sens inversé."}]', model="meta/leger")

    monkeypatch.setattr(NimProofreader, "complete", faux_complete)
    settings = Settings(nim_api_key="k", nim_fallback_enabled=True, lexicon_enabled=False)

    rapport = verify([pair("il y a 42 cas", "il y a des cas")], settings=settings)

    assert rapport.mode == "nim-cible"
    assert rapport.claude_pairs == 1
    assert appels == [True]
    assert any(f.message == "Sens inversé." for f in rapport.findings)
