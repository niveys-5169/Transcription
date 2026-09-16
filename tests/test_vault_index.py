from app.config import Settings
from app.obsidian import index
from app import obsidian
from app import server
from fastapi.testclient import TestClient


def _write(vault, name, content):
    path = vault / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_index_lit_titre_alias_tags_et_type(tmp_path, monkeypatch):
    vault = tmp_path / "coffre"; vault.mkdir()
    monkeypatch.setattr(index.config, "DATA_DIR", tmp_path / "data")
    _write(vault, "References/CDAPH.md", "---\ntype: organisme\naliases: [Cdaph, Maison du handicap]\ntags: [droit, handicap]\n---\n# CDAPH\n")
    notes = index.build(Settings(obsidian_vault_path=str(vault)), force=True)
    assert notes == [{"title": "CDAPH", "aliases": ["Cdaph", "Maison du handicap"], "tags": ["droit", "handicap"], "type": "organisme", "path": "References/CDAPH.md"}]


def test_resolution_utilise_un_alias_sans_creer_de_doublon(tmp_path, monkeypatch):
    vault = tmp_path / "coffre"; vault.mkdir()
    monkeypatch.setattr(index.config, "DATA_DIR", tmp_path / "data")
    _write(vault, "CDAPH.md", "---\naliases: [Cdaph]\n---\n# CDAPH\n")
    settings = Settings(obsidian_vault_path=str(vault))
    entities = index.resolve_entities(settings, [{"nom": "Cdaph", "categorie": "organisme"}])
    assert entities[0]["wikilink"] == "CDAPH"
    assert entities[0]["vault_path"] == "CDAPH.md"


def test_reindexe_si_une_note_change(tmp_path, monkeypatch):
    vault = tmp_path / "coffre"; vault.mkdir()
    monkeypatch.setattr(index.config, "DATA_DIR", tmp_path / "data")
    settings = Settings(obsidian_vault_path=str(vault))
    _write(vault, "A.md", "# A\n")
    assert [note["title"] for note in index.build(settings)] == ["A"]
    _write(vault, "B.md", "# B\n")
    assert [note["title"] for note in index.build(settings)] == ["A", "B"]


def test_routes_index_et_reindex(tmp_path, monkeypatch):
    vault = tmp_path / "coffre"; vault.mkdir()
    _write(vault, "CDAPH.md", "---\naliases: [Cdaph]\n---\n# CDAPH\n")
    settings = Settings(obsidian_vault_path=str(vault))
    monkeypatch.setattr(server.config, "load_settings", lambda: settings)
    monkeypatch.setattr(index.config, "DATA_DIR", tmp_path / "data")
    client = TestClient(server.app)
    found = client.get("/api/vault/index", params={"q": "cdaph"})
    rebuilt = client.post("/api/vault/reindex")
    assert found.status_code == 200
    assert found.json()["notes"][0]["title"] == "CDAPH"
    assert rebuilt.json()["count"] == 1


def test_publication_lie_l_entite_existante_et_conserve_son_alias(tmp_path, monkeypatch):
    vault = tmp_path / "coffre"; vault.mkdir()
    _write(vault, "CDAPH.md", "---\naliases: [Cdaph]\n---\n# CDAPH\n")
    monkeypatch.setattr(index.config, "DATA_DIR", tmp_path / "data")
    settings = Settings(obsidian_vault_path=str(vault))
    job = {"id": "cours", "title": "Cours", "filename": "cours.wav", "entities": [{"nom": "Cdaph", "categorie": "organisme"}]}
    relative = obsidian.publish(job, settings=settings)
    fiche = (vault / relative).read_text(encoding="utf-8")
    assert 'organismes: ["[[CDAPH]]"]' in fiche
    assert "aliases_entites: [Cdaph → CDAPH]" in fiche
    assert not (vault / settings.obsidian_entities_folder / "Cdaph.md").exists()
