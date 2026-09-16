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
