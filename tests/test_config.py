"""Tests de la configuration, notamment le nettoyage de l'identifiant RunPod.

Erreur fréquente signalée : coller l'URL affichée dans la console RunPod
(``https://api.runpod.ai/v2/<id>/run``) au lieu du seul identifiant, ce qui
fait échouer tous les appels avec un 404 sans indice sur la cause.
"""
from __future__ import annotations

import pytest

from app import config


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config._settings = None
    yield
    config._settings = None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("abc123xyz", "abc123xyz"),
        ("  abc123xyz  ", "abc123xyz"),
        ('"abc123xyz"', "abc123xyz"),
        ("https://api.runpod.ai/v2/abc123xyz", "abc123xyz"),
        ("https://api.runpod.ai/v2/abc123xyz/run", "abc123xyz"),
        ("https://api.runpod.ai/v2/abc123xyz/", "abc123xyz"),
        ("api.runpod.ai/v2/abc123xyz/runsync", "abc123xyz"),
        ("abc123xyz/status/jobid", "abc123xyz"),
    ],
)
def test_normalize_runpod_endpoint_id(raw, expected):
    assert config._normalize_runpod_endpoint_id(raw) == expected


def test_save_settings_normalizes_endpoint_id():
    settings = config.save_settings(
        {"runpod_endpoint_id": "https://api.runpod.ai/v2/abc123xyz/run"}
    )
    assert settings.runpod_endpoint_id == "abc123xyz"


def test_env_normalizes_endpoint_id(monkeypatch):
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "https://api.runpod.ai/v2/abc123xyz/run")
    settings = config.load_settings(refresh=True)
    assert settings.runpod_endpoint_id == "abc123xyz"


def test_nim_key_is_masked_in_public_settings():
    settings = config.save_settings(
        {"nim_api_key": "nvapi-secret", "nim_fallback_enabled": True}
    )
    public = settings.public_dict()
    assert public["nim_api_key"] == ""
    assert public["nim_api_key_set"] is True
    assert public["nim_fallback_enabled"] is True
