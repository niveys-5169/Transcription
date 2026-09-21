"""Catalogue des modèles NVIDIA NIM."""
import json
from urllib.error import URLError

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

    assert result.text.startswith("Une réponse")
    assert result.model == "nvidia/secours"
    assert requests == ["nvidia/principal", "nvidia/secours"]


def test_complete_desactive_le_raisonnement_nemotron_avec_temperature_zero(monkeypatch):
    payloads = []

    def fake_urlopen(request, timeout):
        payloads.append(json.loads(request.data.decode("utf-8")))
        return _Response({"choices": [{"message": {"content": "réponse suffisamment longue pour passer"}}]})

    monkeypatch.setattr("app.proofread.nim.urlopen", fake_urlopen)
    proofreader = NimProofreader(Settings(
        nim_api_key="nim-test", nim_model="nvidia/llama-3.3-nemotron-super-49b-v1",
    ))

    proofreader.complete(system="RELECTURE", user="Bloc", max_tokens=10)

    payload = payloads[0]
    assert payload["temperature"] == 0.0
    assert payload["messages"][0] == {"role": "system", "content": "detailed thinking off"}
    assert payload["messages"][1] == {"role": "system", "content": "RELECTURE"}


def test_complete_n_invente_aucun_prefixe_pour_un_modele_instruct_simple(monkeypatch):
    payloads = []

    def fake_urlopen(request, timeout):
        payloads.append(json.loads(request.data.decode("utf-8")))
        return _Response({"choices": [{"message": {"content": "réponse suffisamment longue pour passer"}}]})

    monkeypatch.setattr("app.proofread.nim.urlopen", fake_urlopen)
    proofreader = NimProofreader(Settings(
        nim_api_key="nim-test", nim_model="meta/llama-3.1-8b-instruct",
    ))

    proofreader.complete(system="RELECTURE", user="Bloc", max_tokens=10)

    payload = payloads[0]
    assert payload["messages"] == [
        {"role": "system", "content": "RELECTURE"},
        {"role": "user", "content": "Bloc"},
    ]


def test_complete_applique_le_profil_propre_a_chaque_modele_de_secours(monkeypatch):
    """Un fallback n'est jamais envoyé « nu » : son profil s'applique aussi."""
    payloads = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if payload["model"] == "nvidia/principal-nemotron":
            raise URLError("indisponible")
        return _Response({"choices": [{"message": {"content": "réponse suffisamment longue"}}]})

    monkeypatch.setattr("app.proofread.nim.urlopen", fake_urlopen)
    proofreader = NimProofreader(Settings(
        nim_api_key="nim-test",
        nim_model="nvidia/principal-nemotron",
        nim_fallback_model_1="meta/llama-3.1-8b-instruct",
    ))

    result = proofreader.complete(system="RELECTURE", user="Bloc", max_tokens=10)

    assert result.model == "meta/llama-3.1-8b-instruct"
    assert payloads[0]["messages"][0] == {"role": "system", "content": "detailed thinking off"}
    assert payloads[1]["messages"] == [
        {"role": "system", "content": "RELECTURE"},
        {"role": "user", "content": "Bloc"},
    ]
