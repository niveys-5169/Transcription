"""Bouton « Connecter Claude » : lancement de ``claude auth login``."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, server
from app.proofread.backends import cli
from app.proofread.backends.cli import CliBackend

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


class FauxProcessus:
    def __init__(self, termine=False):
        self.termine = termine

    def poll(self):
        return 0 if self.termine else None


@pytest.fixture(autouse=True)
def _sans_connexion_en_cours(monkeypatch):
    monkeypatch.setattr(cli, "_login_process", None)


def _backend(monkeypatch, chemin="/usr/bin/claude"):
    backend = CliBackend(config.load_settings())
    monkeypatch.setattr(backend, "_executable", lambda: chemin)
    return backend


def test_start_login_lance_auth_login_dans_une_console(monkeypatch):
    appels = []

    def faux_popen(commande, **kwargs):
        appels.append((commande, kwargs))
        return FauxProcessus()

    monkeypatch.setattr(cli.subprocess, "Popen", faux_popen)
    ok, detail = _backend(monkeypatch).start_login()
    assert ok
    assert "navigateur" in detail
    commande, kwargs = appels[0]
    assert commande == ["/usr/bin/claude", "auth", "login"]
    assert kwargs["creationflags"] == getattr(cli.subprocess, "CREATE_NEW_CONSOLE", 0)


def test_start_login_refuse_si_cli_introuvable(monkeypatch):
    ok, detail = _backend(monkeypatch, chemin=None).start_login()
    assert not ok
    assert "introuvable" in detail


def test_start_login_refuse_une_seconde_connexion_en_cours(monkeypatch):
    monkeypatch.setattr(cli, "_login_process", FauxProcessus(termine=False))
    lances = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: lances.append(a))
    ok, detail = _backend(monkeypatch).start_login()
    assert not ok
    assert "déjà en cours" in detail
    assert lances == []


def test_start_login_signale_un_echec_de_lancement(monkeypatch):
    def echec(*args, **kwargs):
        raise OSError("accès refusé")

    monkeypatch.setattr(cli.subprocess, "Popen", echec)
    ok, detail = _backend(monkeypatch).start_login()
    assert not ok
    assert "accès refusé" in detail


def test_route_login_repond_409_avec_le_back_end_api(monkeypatch):
    monkeypatch.setattr(
        config, "load_settings",
        lambda: type("S", (), {"claude_backend": "api"})(),
    )
    with TestClient(server.app) as client:
        reponse = client.post("/api/claude/login")
    assert reponse.status_code == 409


def test_route_login_demarre_la_connexion(monkeypatch):
    monkeypatch.setattr(CliBackend, "start_login", lambda self: (True, "Connexion lancée : é è ê à ç œ"))
    monkeypatch.setattr(
        config, "load_settings",
        lambda: type("S", (), {"claude_backend": "cli"})(),
    )
    with TestClient(server.app) as client:
        reponse = client.post("/api/claude/login")
    assert reponse.status_code == 200
    assert reponse.json() == {"started": True, "detail": "Connexion lancée : é è ê à ç œ"}


def test_interface_expose_le_bouton_connecter_claude():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="claude-login"' in html
    assert "/api/claude/login" in script
