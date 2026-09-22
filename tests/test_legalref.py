"""Tests du repérage mécanique (sans modèle) des références juridiques et des
organismes de mesure — cas réels tirés de ``app/lexicon/mjpm.json``."""
from __future__ import annotations

import pytest

from app.proofread.legalref import has_legal_reference, is_organisme_de_mesure

# Références réelles du lexique MJPM (champ « reference »).
REFERENCES_REELLES = [
    "Code civil, art. 433 à 439",
    "Code civil, art. 440, 467 sqq.",
    "Code civil, art. 472",
    "CASF, art. L. 471-1 à L. 471-9",
    "Code de l'organisation judiciaire, art. L. 213-4-1",
    "CASF, art. L. 472-6",
    "CASF, art. L. 471-6, art. D. 471-6",
    "Code civil, art. 511",
    "Loi n° 2007-308 du 5 mars 2007",
    "Loi n° 2019-222 du 23 mars 2019",
]


@pytest.mark.parametrize("reference", REFERENCES_REELLES)
def test_references_reelles_du_lexique_sont_reconnues(reference):
    assert has_legal_reference(reference) is True


@pytest.mark.parametrize(
    "reference",
    [
        "article 440",
        "art. 433 à 439",
        "articles L. 471-1 à L. 471-9",
        "art. R. 471-5",
        "article D. 472-5",
        "art. A. 271-1",
    ],
)
def test_formes_d_articles_avec_ou_sans_lettre(reference):
    assert has_legal_reference(reference) is True


@pytest.mark.parametrize(
    "reference",
    [
        "code civil",
        "code de l'action sociale et des familles",
        "CASF",
        "code de la santé publique",
        "CSP",
        "code de procédure civile",
        "code de l'organisation judiciaire",
        "code de la sécurité sociale",
        "code pénal",
        "code du travail",
        "code monétaire et financier",
        "code général des impôts",
    ],
)
def test_codes_sont_reconnus(reference):
    assert has_legal_reference(reference) is True


@pytest.mark.parametrize(
    "reference",
    [
        "décret n° 2008-1484",
        "décret n ° 2008-1484",
        "décret no 2008-1484",
        "décret nº 2008-1484",
        "arrêté du 30 décembre 2018",
        "ordonnance n° 2020-232",
        "circulaire",
        "règlement (UE) 2016/679",
        "directive 2016/679",
        "RGPD",
    ],
)
def test_textes_et_variantes_de_n_sont_reconnus(reference):
    assert has_legal_reference(reference) is True


def test_asr_sans_accents_reste_reconnu():
    # L'ASR perd souvent les accents : le repérage doit rester fiable.
    assert has_legal_reference("prefecture, decret n 2008-1484, arrete du 30 decembre 2018") is True
    assert has_legal_reference("Code de l'organisation judiciaire, art. L. 213-4-1".lower()) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "article de journal",
        "il a codé toute la nuit",
        "la loi de la jungle",
        "440 euros",
        "le décor était planté",
    ],
)
def test_contre_exemples_ne_matchent_pas(phrase):
    assert has_legal_reference(phrase) is False


# --------------------------------------------------------------- organismes


@pytest.mark.parametrize(
    "phrase",
    [
        "le juge des tutelles a rendu sa décision",
        "juge des contentieux de la protection",
        "convoqué par le JCP",
        "saisine du tribunal judiciaire",
        "tribunal d'instance",
        "dépôt au greffe",
        "signalement au procureur",
        "le parquet a été informé",
        "arrêté préfectoral pris par la préfecture",
        "sur décision du préfet",
        "conseil départemental",
        "DDETS",
        "DDCS",
        "notification de l'ARS",
        "dossier MDPH",
        "allocation versée par la CAF",
        "remboursement CPAM",
        "retraite CARSAT",
        "régime MSA",
        "UDAF du département",
        "ATI",
        "CNC",
        "recommandation ANESM",
        "avis de la HAS",
        "saisine du Défenseur des droits",
        "CNAPE",
        "FNAT",
        "UNAF",
    ],
)
def test_organismes_de_mesure_sont_reconnus(phrase):
    assert is_organisme_de_mesure(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "il a des tas de choses a faire",
        "un chat sur le toit",
        "il a pris une carafe d'eau",
        "elle a beaucoup ri",
    ],
)
def test_contre_exemples_organismes_ne_matchent_pas(phrase):
    assert is_organisme_de_mesure(phrase) is False
