"""Tests du module Obsidian : garde-fous d'écriture, idempotence, fiche."""
import pytest

from app.config import Settings
from app import obsidian
from app.obsidian.vault import ObsidianError, resolve, write_atomic, read


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "coffre"
    v.mkdir()
    return v


@pytest.fixture
def settings(vault):
    return Settings(obsidian_vault_path=str(vault))


def job(**overrides):
    base = {
        "id": "abc123",
        "filename": "cours.mp4",
        "title": "Introduction à la tutelle",
        "summary": ["Point 1"],
        "clean_text": "Un texte relu sans aucune annotation.",
        "raw_text": "",
        "duration": 120.0,
        "engine": "local",
        "model": "large-v3",
        "proofread": "claude",
        "status": "checked",
        "factcheck_report": {"claims_checked": 0, "corrections": 0, "findings": []},
        "verification": {"findings": [], "mode": "claude"},
        "entities": [],
        "created_at": "2026-09-14T10:00:00+00:00",
        "obsidian_path": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- garde-fous


def test_ecriture_hors_du_coffre_est_refusee(vault):
    with pytest.raises(ObsidianError):
        resolve(str(vault), "../../etc/passwd")


def test_chemin_absolu_hors_coffre_est_refuse(vault):
    with pytest.raises(ObsidianError):
        resolve(str(vault), "/etc/passwd")


def test_coffre_absent_leve_une_erreur_explicite(tmp_path):
    with pytest.raises(ObsidianError):
        resolve(str(tmp_path / "n-existe-pas"), "fiche.md")


def test_sans_coffre_configure_publish_echoue():
    with pytest.raises(ObsidianError):
        obsidian.publish(job(), settings=Settings(obsidian_vault_path=""))


# ------------------------------------------------------------------- fiche


def test_publier_ecrit_la_fiche_dans_le_bon_dossier(vault, settings):
    relative = obsidian.publish(job(), settings=settings)
    assert relative.startswith(settings.obsidian_notes_folder)
    assert (vault / relative).exists()


def test_republier_reecrit_la_meme_fiche_sans_duplication(vault, settings):
    j = job()
    relative1 = obsidian.publish(j, settings=settings)

    j["obsidian_path"] = relative1
    j["title"] = "Titre modifié"
    relative2 = obsidian.publish(j, settings=settings)

    assert relative1 == relative2
    fiches = list((vault / settings.obsidian_notes_folder).glob("*.md"))
    assert len(fiches) == 1


def test_fiche_obsidian_utilise_les_blocs_corriges_sans_toucher_au_brut(vault, settings):
    j = job(
        clean_text="Version IA.", raw_text="Texte brut intact.",
        segments=[{"text": "Version IA."}],
        review_blocks=[{"id": "segment-1", "text": "Version humaine."}],
    )
    relative = obsidian.publish(j, settings=settings)
    content = (vault / relative).read_text(encoding="utf-8")
    assert "Version humaine." in content
    assert "Texte brut intact." not in content
    assert j["raw_text"] == "Texte brut intact."


def test_encart_warning_si_points_incertains(vault, settings):
    j = job(
        verification={
            "findings": [{"kind": "fait", "severity": "haute", "message": "douteux"}],
            "mode": "claude",
        }
    )
    relative = obsidian.publish(j, settings=settings)
    content = (vault / relative).read_text(encoding="utf-8")
    assert "[!warning]" in content
    assert "statut_verification: incertain" in content


def test_encart_success_si_aucun_point_incertain(vault, settings):
    relative = obsidian.publish(job(), settings=settings)
    content = (vault / relative).read_text(encoding="utf-8")
    assert "[!success]" in content
    assert "statut_verification: verifie" in content


def test_statut_non_verifie_si_factcheck_pas_execute(vault, settings):
    j = job(status="published", factcheck_report=None)
    relative = obsidian.publish(j, settings=settings)
    content = (vault / relative).read_text(encoding="utf-8")
    assert "statut_verification: non_verifie" in content
    assert "[!warning]" in content


# --------------------------------------------------------------- entités


def test_entite_confirmee_devient_un_wikilink_et_une_fiche(vault, settings):
    j = job(
        entities=[
            {
                "nom": "UDAF",
                "wikilink": "UDAF",
                "categorie": "organisme",
                "definition": "Union départementale.",
                "sources": [{"titre": "Source", "url": "https://example.org"}],
            }
        ]
    )
    relative = obsidian.publish(j, settings=settings)
    note = (vault / relative).read_text(encoding="utf-8")
    assert "[[UDAF]]" in note

    entity_path = vault / settings.obsidian_entities_folder / "UDAF.md"
    assert entity_path.exists()
    assert "Union départementale" in entity_path.read_text(encoding="utf-8")


def test_fiche_entite_existante_n_est_jamais_ecrasee(vault, settings):
    entity_path = vault / settings.obsidian_entities_folder / "UDAF.md"
    write_atomic(entity_path, "# UDAF\n\nNotes personnelles à ne jamais perdre.\n")

    j = job(entities=[{"nom": "UDAF", "wikilink": "UDAF", "categorie": "organisme"}])
    obsidian.publish(j, settings=settings)

    assert "Notes personnelles" in entity_path.read_text(encoding="utf-8")


# -------------------------------------------------------------------- MOC


def test_moc_recoit_une_ligne_par_travail(vault, settings):
    obsidian.publish(job(id="a", title="Cours A"), settings=settings)
    obsidian.publish(job(id="b", title="Cours B"), settings=settings)

    moc = read(vault / settings.obsidian_index_note)
    assert "Cours A" in moc
    assert "Cours B" in moc


def test_republier_met_a_jour_la_ligne_du_moc_sans_la_dupliquer(vault, settings):
    j = job()
    relative = obsidian.publish(j, settings=settings)
    j["obsidian_path"] = relative
    obsidian.publish(j, settings=settings)

    moc = read(vault / settings.obsidian_index_note)
    assert moc.count("Introduction à la tutelle") == 1


def test_moc_preserve_le_contenu_hors_de_la_region_balisee(vault, settings):
    from app.obsidian.entities import MOC_END, MOC_START

    index_path = vault / settings.obsidian_index_note
    write_atomic(
        index_path,
        f"# Mes notes personnelles\n\nDu texte à moi.\n\n{MOC_START}\n{MOC_END}\n\nEncore du texte à moi.\n",
    )
    obsidian.publish(job(), settings=settings)

    content = read(index_path)
    assert "Du texte à moi." in content
    assert "Encore du texte à moi." in content


# -------------------------------------------------------------- glossaire


def test_glossaire_est_genere_depuis_le_lexique(vault, settings):
    obsidian.publish(job(), settings=settings)
    glossaire = read(vault / settings.obsidian_glossary_note)
    assert "Glossaire MJPM" in glossaire
    assert "DIPM" in glossaire or "Document individuel" in glossaire


def test_domaine_parametrable_apparait_dans_la_fiche_et_le_glossaire(vault, settings):
    settings.domain_label = "Droit social"
    settings.obsidian_glossary_note = "Formation/Droit social/Glossaire Droit social.md"
    relative = obsidian.publish(job(), settings=settings)
    assert "domaine: Droit social" in (vault / relative).read_text(encoding="utf-8")
    assert "# Glossaire Droit social" in (vault / settings.obsidian_glossary_note).read_text(encoding="utf-8")
