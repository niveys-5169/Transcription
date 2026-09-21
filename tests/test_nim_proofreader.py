"""NimProofreader.proofread() : bout en bout, sans réseau réel.

Reproduit la forme exacte de l'incident réel (raisonnement de modèle publié
à la place du texte relu) sur un exemple synthétique, et verrouille que le
contexte inter-blocs vient toujours du paragraphe RAW précédent.
"""
import json

from app.config import Settings
from app.engines.base import Segment
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


def _settings(**overrides):
    base = dict(
        nim_fallback_enabled=True,
        nim_api_key="nim-test",
        nim_base_url="https://integrate.api.nvidia.com/v1/chat/completions",
        nim_model="nvidia/llama-3.3-nemotron-super-49b-v1",
        nim_fallback_model_1="",
        nim_fallback_model_2="",
    )
    base.update(overrides)
    return Settings(**base)


def _segments():
    return [
        Segment(0.0, 3.0, "euh bonjour ceci est le premier bloc de test avec assez de contenu brut", speaker="Intervenant A"),
        Segment(3.5, 6.0, "ceci est le second bloc qui doit recevoir le contexte du premier bloc uniquement", speaker="Intervenant B"),
    ]


def test_reasoning_corrompu_est_rejete_et_le_pipeline_continue(monkeypatch):
    """Reproduit l'incident réel : le modèle répond par son raisonnement."""
    reasoning_response = (
        "We need to apply the rules. The passage is raw transcription, let's examine for errors: "
        "Paragraph 1: bonjour ceci est le premier bloc. Paragraph 2: continuation of the analysis."
    )

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        user_message = payload["messages"][-1]["content"]
        if "premier bloc" in user_message and "second bloc" not in user_message:
            content = reasoning_response  # bloc 1 : réponse corrompue, sans enveloppe
        else:
            content = (
                "<transcription>Ceci est le second bloc, qui doit recevoir le contexte "
                "du premier bloc uniquement, correctement relu.</transcription>"
            )
        return _Response({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("app.proofread.nim.urlopen", fake_urlopen)
    proofreader = NimProofreader(_settings())

    result = proofreader.proofread([s.to_dict() for s in _segments()], structure=False)

    # Invariant : aucun morceau du raisonnement ne doit se retrouver dans le
    # texte final.
    assert "We need to apply the rules" not in result.text
    assert "Paragraph 1" not in result.text
    assert "let's examine" not in result.text.lower()

    # Le bloc rejeté retombe sur le nettoyage mécanique du brut (euh retiré).
    assert "premier bloc" in result.text
    assert "euh" not in result.text.lower()

    # Le second bloc, valide, est bien passé tel quel.
    assert "correctement relu." in result.text

    # Un rejet est journalisé pour observabilité, avec un motif explicite.
    assert len(result.rejections) == 1
    rejection = result.rejections[0]
    assert rejection["validation_status"] == "invalid"
    assert rejection["reject_reason"]
    assert rejection["model"] == "nvidia/llama-3.3-nemotron-super-49b-v1"

    # Le pipeline continue : deux paires produites, pas d'exception levée.
    assert len(result.pairs) == 2


def test_contexte_inter_blocs_vient_toujours_du_brut_precedent(monkeypatch):
    """Une hallucination du bloc N ne doit jamais contaminer le prompt du bloc N+1."""
    captured_second_block_user_message = {}

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        user_message = payload["messages"][-1]["content"]
        if "second bloc" in user_message and "Relis le passage" in user_message:
            captured_second_block_user_message["content"] = user_message
            return _Response({"choices": [{"message": {"content": "<transcription>Second bloc relu.</transcription>"}}]})
        # Bloc 1 : réponse corrompue (sans enveloppe), pour vérifier que la
        # contamination ne se propage pas au bloc suivant.
        return _Response({"choices": [{"message": {"content": "We need to apply the rules, paragraph 1: nonsense output."}}]})

    monkeypatch.setattr("app.proofread.nim.urlopen", fake_urlopen)
    proofreader = NimProofreader(_settings())

    proofreader.proofread([s.to_dict() for s in _segments()], structure=False)

    user_message = captured_second_block_user_message["content"]
    # Le contexte doit contenir le texte BRUT du premier bloc (avec « euh »,
    # marqueur de l'oral non nettoyé), jamais le raisonnement halluciné du
    # bloc 1 ni une version déjà nettoyée.
    assert "euh bonjour ceci est le premier bloc" in user_message
    assert "We need to apply the rules" not in user_message
    assert "nonsense output" not in user_message
