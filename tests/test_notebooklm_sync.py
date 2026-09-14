"""Tests du module de synchronisation NotebookLM.

``build_master_markdown`` est pure (aucun appel réseau, aucune bibliothèque
Google) : elle doit pouvoir tourner même si google-api-python-client n'est
pas installé, exactement comme en environnement de test (requirements-dev.txt
ne les installe pas). ``sync_master_doc`` doit se dégrader proprement dans
tous les cas où Drive n'est pas joignable ou pas configuré — jamais lever,
jamais faire planter le pipeline.
"""
from __future__ import annotations

import logging

import pytest

from app import config
from app.notebooklm_sync import build_master_markdown, sync_master_doc


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config._settings = None
    yield
    config._settings = None


def _write_course(dir_path, name: str, title: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


# --------------------------------------------------------- build_master_markdown


def test_dossier_absent_donne_un_document_sans_exception(tmp_path):
    markdown = build_master_markdown(tmp_path / "n_existe_pas")
    assert "# Cours transcrits" in markdown
    assert "## Sommaire" in markdown


def test_dossier_vide_donne_un_sommaire_vide(tmp_path):
    (tmp_path / "cours").mkdir()
    markdown = build_master_markdown(tmp_path / "cours")
    assert "Aucun cours relu pour l'instant" in markdown


def test_sommaire_et_sections_dans_l_ordre_des_fichiers(tmp_path):
    courses = tmp_path / "cours"
    _write_course(courses, "2024-01-01-premier.md", "Premier cours", "Contenu un.")
    _write_course(courses, "2024-06-01-second.md", "Second cours", "Contenu deux.")

    markdown = build_master_markdown(courses)

    sommaire = markdown.index("## Sommaire")
    premier_lien = markdown.index("[Premier cours]")
    second_lien = markdown.index("[Second cours]")
    premier_section = markdown.index("## Premier cours", sommaire)
    second_section = markdown.index("## Second cours", sommaire)

    # Le sommaire précède les sections, et les cours restent dans l'ordre
    # chronologique (celui des noms de fichiers, préfixés par leur date).
    assert sommaire < premier_lien < second_lien < premier_section < second_section


def test_ancre_du_sommaire_correspond_au_titre_de_section(tmp_path):
    courses = tmp_path / "cours"
    _write_course(courses, "2024-01-01-accents.md", "Été à l'École", "Du contenu.")

    markdown = build_master_markdown(courses)

    assert "(#ete-a-lecole)" in markdown
    assert "## Été à l'École" in markdown


def test_separateur_entre_sections_et_pas_de_titre_duplique(tmp_path):
    courses = tmp_path / "cours"
    _write_course(courses, "2024-01-01-a.md", "Cours A", "Corps A.")
    _write_course(courses, "2024-01-02-b.md", "Cours B", "Corps B.")

    markdown = build_master_markdown(courses)

    assert "---" in markdown
    # Le titre d'origine (# Cours A) ne doit pas apparaître en plus du titre
    # de section (## Cours A) : sinon deux titres pour le même cours.
    assert markdown.count("Cours A") == 2  # sommaire + section, jamais un 3e


def test_titres_identiques_recoivent_des_ancres_distinctes(tmp_path):
    courses = tmp_path / "cours"
    _write_course(courses, "2024-01-01-a.md", "Introduction", "Premier.")
    _write_course(courses, "2024-01-02-b.md", "Introduction", "Second.")

    markdown = build_master_markdown(courses)

    assert "(#introduction)" in markdown
    assert "(#introduction-2)" in markdown


def test_fichier_sans_titre_utilise_le_nom_de_fichier(tmp_path):
    courses = tmp_path / "cours"
    courses.mkdir()
    (courses / "2024-01-01-sans-titre.md").write_text("Juste du texte.\n", encoding="utf-8")

    markdown = build_master_markdown(courses)

    assert "2024-01-01-sans-titre" in markdown


# ------------------------------------------------------------- sync_master_doc


def test_sync_desactive_ne_fait_rien(tmp_path, caplog):
    settings = config.save_settings({"notebooklm_sync_enabled": False})
    with caplog.at_level(logging.WARNING):
        result = sync_master_doc(tmp_path, settings=settings)
    assert result is False
    assert caplog.records == []


def test_sync_active_sans_doc_maitre_echoue_proprement(tmp_path, caplog):
    settings = config.save_settings(
        {"notebooklm_sync_enabled": True, "notebooklm_master_doc_id": ""}
    )
    with caplog.at_level(logging.WARNING):
        result = sync_master_doc(tmp_path, settings=settings)
    assert result is False
    assert any("Doc maître" in record.message for record in caplog.records)


def test_sync_sans_identifiants_ne_leve_pas(tmp_path, caplog):
    """Ni credentials.json ni token.json ne sont présents : la sync doit se
    dégrader (journal clair) plutôt que de lever — le reste du pipeline
    (déjà marqué terminé en base) ne doit jamais dépendre de ce résultat.
    """
    settings = config.save_settings(
        {
            "notebooklm_sync_enabled": True,
            "notebooklm_master_doc_id": "un-identifiant",
            "notebooklm_credentials_path": str(tmp_path / "absent_credentials.json"),
            "notebooklm_token_path": str(tmp_path / "absent_token.json"),
        }
    )
    with caplog.at_level(logging.WARNING):
        result = sync_master_doc(tmp_path, settings=settings)
    assert result is False


def test_sync_ne_propage_pas_une_erreur_inattendue(tmp_path, monkeypatch, caplog):
    """Même une exception qu'on n'a pas prévue ne doit jamais remonter."""
    from app import notebooklm_sync

    settings = config.save_settings(
        {"notebooklm_sync_enabled": True, "notebooklm_master_doc_id": "un-identifiant"}
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("panne inattendue")

    monkeypatch.setattr(notebooklm_sync, "load_credentials", _boom)

    with caplog.at_level(logging.WARNING):
        result = sync_master_doc(tmp_path, settings=settings)
    assert result is False
