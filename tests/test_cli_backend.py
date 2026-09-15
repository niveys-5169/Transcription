"""Tests du back-end CLI, sans lancer le vrai binaire ``claude``.

``subprocess.Popen`` et ``subprocess.run`` sont remplacés par des doublures :
ce qui est testé ici, c'est la construction de la ligne de commande et
l'analyse du flux JSON Lines, jamais un appel réel.
"""
import io
import json

import pytest

from app.config import Settings
from app.proofread.backends.cli import CliBackend, QuotaExhausted
from app.proofread.base import ProofreadError


class _FakeCompletedProcess:
    def __init__(self, stdout: str):
        self.stdout = stdout


class _FakeProcess:
    def __init__(self, stdout_lines, *, stderr="", returncode=0):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("\n".join(stdout_lines) + ("\n" if stdout_lines else ""))
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode
        self.killed = False

    def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude")
    instance = CliBackend(Settings(claude_backend="cli", proofread_model="claude-sonnet-5"))
    return instance


def _auth_status(*, logged_in=True, method="oauth_token"):
    return json.dumps({"loggedIn": logged_in, "authMethod": method})


def _stream(*events):
    return [json.dumps(e) for e in events]


def _assistant(text=None, tool_use=None):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool_use is not None:
        content.append({"type": "tool_use", "id": tool_use["id"], "name": tool_use["name"]})
    return {"type": "assistant", "message": {"content": content}}


def _tool_result(tool_use_id, text):
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]},
    }


def _result(text="", is_error=False, subtype="success"):
    return {"type": "result", "is_error": is_error, "subtype": subtype, "result": text}


# ------------------------------------------------------------- disponibilité


def test_indisponible_sans_binaire(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    instance = CliBackend(Settings())
    available, detail = instance.is_available()
    assert available is False
    assert "introuvable" in detail.lower()


def test_disponible_avec_abonnement(backend, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **k: _FakeCompletedProcess(_auth_status()),
    )
    available, detail = backend.is_available()
    assert available is True


def test_indisponible_sans_connexion(backend, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **k: _FakeCompletedProcess(_auth_status(logged_in=False)),
    )
    available, detail = backend.is_available()
    assert available is False
    assert "connecté" in detail.lower()


def test_indisponible_si_configure_avec_une_cle_api(backend, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **k: _FakeCompletedProcess(_auth_status(method="api_key")),
    )
    available, detail = backend.is_available()
    assert available is False
    assert "clé api" in detail.lower()


# --------------------------------------------------------- ligne de commande


def _make_available(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: _FakeCompletedProcess(_auth_status()))


def test_le_prompt_part_par_stdin_pas_en_argument(backend, monkeypatch):
    _make_available(monkeypatch)
    prompt = "X" * 50_000
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(_stream(_result("ok")))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    backend.complete(system="système", user=prompt, max_tokens=100)

    assert prompt not in captured["cmd"]
    assert all(len(arg) < len(prompt) for arg in captured["cmd"])


def test_jamais_bare_toujours_permission_prompts_none(backend, monkeypatch):
    _make_available(monkeypatch)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(_stream(_result("ok")))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    backend.complete(system="s", user="u", max_tokens=100)

    cmd = captured["cmd"]
    assert "--bare" not in cmd
    assert "--permission-prompts" in cmd
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--setting-sources") + 1] == ""


def test_sans_recherche_web_les_outils_sont_desactives(backend, monkeypatch):
    _make_available(monkeypatch)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(_stream(_result("ok")))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    backend.complete(system="s", user="u", max_tokens=100, web_search=False)

    cmd = captured["cmd"]
    assert cmd[cmd.index("--tools") + 1] == ""


def test_avec_recherche_web_seul_websearch_est_autorise(backend, monkeypatch):
    _make_available(monkeypatch)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(_stream(_result("ok")))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    backend.complete(system="s", user="u", max_tokens=100, web_search=True)

    cmd = captured["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "WebSearch"
    # ``--tools`` ne fait que restreindre l'ensemble disponible ; sans
    # ``--allowedTools`` en plus, WebSearch resterait soumis à une
    # confirmation qu'une session sans écran ne peut jamais donner, et se
    # ferait refuser d'office par ``--permission-prompts none``.
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch"


def test_sans_recherche_web_pas_d_allowedtools(backend, monkeypatch):
    _make_available(monkeypatch)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(_stream(_result("ok")))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    backend.complete(system="s", user="u", max_tokens=100, web_search=False)

    assert "--allowedTools" not in captured["cmd"]


def test_schema_ajoute_json_schema(backend, monkeypatch):
    _make_available(monkeypatch)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(_stream(_result("{}")))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    backend.complete(system="s", user="u", max_tokens=100, schema={"type": "object"})

    assert "--json-schema" in captured["cmd"]


# ----------------------------------------------------------- analyse du flux


def test_texte_final_vient_du_result(backend, monkeypatch):
    _make_available(monkeypatch)
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **k: _FakeProcess(_stream(_assistant("brouillon"), _result("texte final"))),
    )
    result = backend.complete(system="s", user="u", max_tokens=100)
    assert result.text == "texte final"


def test_repli_sur_le_dernier_texte_assistant_si_pas_de_result(backend, monkeypatch):
    _make_available(monkeypatch)
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **k: _FakeProcess(_stream(_assistant("seul texte disponible"))),
    )
    result = backend.complete(system="s", user="u", max_tokens=100)
    assert result.text == "seul texte disponible"


def test_aucun_resultat_leve_une_erreur(backend, monkeypatch):
    _make_available(monkeypatch)
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **k: _FakeProcess([]))
    with pytest.raises(ProofreadError):
        backend.complete(system="s", user="u", max_tokens=100)


def test_recherche_web_constatee_via_tool_use_et_tool_result(backend, monkeypatch):
    _make_available(monkeypatch)
    events = _stream(
        _assistant(tool_use={"id": "tool_1", "name": "WebSearch"}),
        _tool_result("tool_1", "Résultat : voir https://example.org/rapport et aussi https://drees.fr"),
        _assistant("réponse finale"),
        _result("réponse finale"),
    )
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **k: _FakeProcess(events))

    result = backend.complete(system="s", user="u", max_tokens=100, web_search=True)
    assert result.web_searches == 1
    assert "https://example.org/rapport" in result.sources
    assert "https://drees.fr" in result.sources


def test_tool_result_sans_lien_avec_websearch_est_ignore(backend, monkeypatch):
    _make_available(monkeypatch)
    # Un tool_result dont le tool_use_id ne correspond à aucun WebSearch vu
    # ne doit pas polluer les sources.
    events = _stream(
        _tool_result("id-inconnu", "https://ne-doit-pas-apparaitre.example"),
        _result("réponse"),
    )
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **k: _FakeProcess(events))

    result = backend.complete(system="s", user="u", max_tokens=100)
    assert result.sources == []
    assert result.web_searches == 0


def test_erreur_generique_leve_proofread_error_pas_quota(backend, monkeypatch):
    _make_available(monkeypatch)
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **k: _FakeProcess(_stream(_result("Erreur de connexion réseau", is_error=True))),
    )
    with pytest.raises(ProofreadError) as exc_info:
        backend.complete(system="s", user="u", max_tokens=100)
    assert not isinstance(exc_info.value, QuotaExhausted)


def test_limite_d_usage_leve_quota_exhausted(backend, monkeypatch):
    _make_available(monkeypatch)
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **k: _FakeProcess(_stream(_result("You have reached your usage limit", is_error=True))),
    )
    with pytest.raises(QuotaExhausted):
        backend.complete(system="s", user="u", max_tokens=100)


def test_parsed_est_rempli_quand_la_sortie_est_un_json_valide(backend, monkeypatch):
    _make_available(monkeypatch)
    payload = {"verdict": "confirme", "confiance": "haute"}
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **k: _FakeProcess(_stream(_result(json.dumps(payload)))),
    )
    result = backend.complete(
        system="s", user="u", max_tokens=100, schema={"type": "object"}
    )
    assert result.parsed == payload


def test_parsed_tableau(backend, monkeypatch):
    _make_available(monkeypatch)
    payload = [{"citation": "x"}]
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **k: _FakeProcess(_stream(_result(json.dumps(payload)))),
    )
    result = backend.complete(
        system="s", user="u", max_tokens=100, schema={"type": "array"}
    )
    assert result.parsed == payload


def test_schema_tableau_est_enveloppe_pour_le_cli(backend, monkeypatch):
    """L'API Anthropic exige un ``input_schema`` de type objet pour un outil
    personnalisé (ce que devient ``--json-schema``) : un schéma « array » nu
    fait échouer l'appel réel avec « tools.0.custom.input_schema.type: Input
    should be 'object' ». Le schéma envoyé au CLI doit donc être enveloppé,
    même si l'appelant continue de passer un schéma « array »."""
    _make_available(monkeypatch)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(_stream(_result("{}")))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    backend.complete(system="s", user="u", max_tokens=100, schema={"type": "array"})

    schema_index = captured["cmd"].index("--json-schema") + 1
    sent_schema = json.loads(captured["cmd"][schema_index])
    assert sent_schema["type"] == "object"


def test_parsed_tableau_enveloppe_est_defait(backend, monkeypatch):
    """Réponse réaliste d'un CLI contraint par le schéma enveloppé ci-dessus :
    ``{"items": [...]}`` plutôt qu'un tableau nu."""
    _make_available(monkeypatch)
    payload = [{"citation": "x"}]
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **k: _FakeProcess(_stream(_result(json.dumps({"items": payload})))),
    )
    result = backend.complete(
        system="s", user="u", max_tokens=100, schema={"type": "array"}
    )
    assert result.parsed == payload
