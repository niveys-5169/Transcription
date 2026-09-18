"""Catalogue des modèles NVIDIA NIM."""
import json

from app.config import Settings
from app.proofread import nim as nim_module
from app.proofread.nim import NimProofreader


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_list_models_utilise_endpoint_openai_compatible(monkeypatch):
    requests = []
    monkeypatch.setattr(
        "app.proofread.nim.urlopen",
        lambda request, timeout: requests.append((request, timeout)) or _Response({"data": [
            {"id": "zeta/model"}, {"id": "alpha/model"}, {"id": "alpha/model"},
        ]}),
    )
    proofreader = NimProofreader(Settings(
        nim_api_key="nim-test", nim_base_url="https://integrate.api.nvidia.com/v1/chat/completions",
    ))

    models, detail = proofreader.list_models()

    assert models == ["alpha/model", "zeta/model"]
    assert "2 modèle" in detail
    assert requests[0][0].full_url == "https://integrate.api.nvidia.com/v1/models"


def test_startup_models_ne_rafraichit_qu_une_fois_par_configuration(monkeypatch):
    nim_module._MODELS_CACHE.clear()
    proofreader = NimProofreader(Settings(nim_api_key="nim-test"))
    calls = []
    monkeypatch.setattr(
        proofreader, "list_models", lambda: calls.append(True) or (["nvidia/test"], "chargé"),
    )

    assert proofreader.startup_models()[0] == ["nvidia/test"]
    assert proofreader.startup_models()[0] == ["nvidia/test"]
    assert calls == [True]


def test_complete_bascule_sur_le_modele_suivant_si_reponse_trop_courte(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        model = json.loads(request.data.decode("utf-8"))["model"]
        requests.append(model)
        content = "court" if model == "nvidia/principal" else "Une réponse de secours suffisamment longue."
        return _Response({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("app.proofread.nim.urlopen", fake_urlopen)
    proofreader = NimProofreader(Settings(
        nim_api_key="nim-test", nim_model="nvidia/principal",
        nim_fallback_model_1="nvidia/secours", nim_fallback_model_2="nvidia/dernier",
    ))

    result = proofreader.complete(system="s", user="u", max_tokens=10, minimum_length=20)

    assert result.startswith("Une réponse")
    assert requests == ["nvidia/principal", "nvidia/secours"]
