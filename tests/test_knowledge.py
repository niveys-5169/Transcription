from types import SimpleNamespace

from app import knowledge
from app.config import Settings


class Backend:
    def is_available(self):
        return True, "ok"

    def complete(self, **_kwargs):
        return SimpleNamespace(text='{"concepts":[{"nom":"Tutelle","definition":"Une mesure.","extrait":"La tutelle est une mesure."}],"themes":[{"nom":"Protection juridique","raison":"Thème du cours"}]}')


def test_build_knowledge_reste_une_proposition(monkeypatch):
    monkeypatch.setattr(knowledge, "get_backend", lambda _settings: Backend())
    monkeypatch.setattr(knowledge.exporters, "editorial_text", lambda _job: "La tutelle est une mesure.")
    result = knowledge.build_knowledge({"id": "cours"}, Settings())
    assert result["status"] == "proposed"
    assert result["concepts"][0]["nom"] == "Tutelle"
    assert result["themes"][0]["nom"] == "Protection juridique"


def test_build_knowledge_nechoue_pas_sans_backend(monkeypatch):
    class Absent:
        def is_available(self):
            return False, "absent"
    monkeypatch.setattr(knowledge, "get_backend", lambda _settings: Absent())
    assert knowledge.build_knowledge({}, Settings()) is None


def test_validation_ecrit_seulement_dans_les_regions_gerees(tmp_path):
    vault = tmp_path / "coffre"; vault.mkdir()
    settings = Settings(obsidian_vault_path=str(vault))
    concept_path = vault / settings.obsidian_concepts_folder / "Tutelle.md"
    concept_path.parent.mkdir(parents=True)
    concept_path.write_text("# Tutelle\n\nTexte humain intact.\n", encoding="utf-8")
    result = knowledge.validate_knowledge(
        {"title": "Cours A"}, settings,
        concepts=[{"nom": "Tutelle", "definition": "Une mesure."}], themes=[{"nom": "Protection", "synthesis": "Synthèse proposée."}],
    )
    concept = concept_path.read_text(encoding="utf-8")
    theme = (vault / settings.obsidian_themes_folder / "Protection.md").read_text(encoding="utf-8")
    assert result["status"] == "published"
    assert "Texte humain intact." in concept
    assert "<!-- sources:début -->\n- [[Cours A]]\n<!-- sources:fin -->" in concept
    assert "Synthèse proposée." in theme
    assert "## Sources\n- [[Cours A]]" in theme


def test_validation_est_idempotente(tmp_path):
    vault = tmp_path / "coffre"; vault.mkdir()
    settings = Settings(obsidian_vault_path=str(vault))
    args = {"concepts": [{"nom": "Tutelle"}], "themes": []}
    knowledge.validate_knowledge({"title": "Cours A"}, settings, **args)
    knowledge.validate_knowledge({"title": "Cours A"}, settings, **args)
    content = (vault / settings.obsidian_concepts_folder / "Tutelle.md").read_text(encoding="utf-8")
    assert content.count("- [[Cours A]]") == 1


def test_validation_archive_la_synthese_precedente_et_met_a_jour_le_moc(tmp_path, monkeypatch):
    vault = tmp_path / "coffre"; vault.mkdir()
    settings = Settings(obsidian_vault_path=str(vault))
    monkeypatch.setattr(knowledge.config, "DATA_DIR", tmp_path / "data")
    theme_path = vault / settings.obsidian_themes_folder / "Protection.md"
    theme_path.parent.mkdir(parents=True)
    theme_path.write_text("# Protection\n\n<!-- synthese:début -->\nAncienne synthèse.\n<!-- synthese:fin -->\n", encoding="utf-8")
    knowledge.validate_knowledge({"title": "Cours A", "created_at": "2026-09-16"}, settings, concepts=[], themes=[{"nom": "Protection", "synthesis": "Nouvelle synthèse."}])
    assert (tmp_path / "data" / "syntheses" / "Protection" / "2026-09-16.md").read_text(encoding="utf-8") == "Ancienne synthèse.\n"
    assert "Nouvelle synthèse." in theme_path.read_text(encoding="utf-8")
    assert "- [[Protection]]" in (vault / settings.obsidian_index_note).read_text(encoding="utf-8")
