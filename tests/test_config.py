"""Tests de la configuration, notamment le nettoyage de l'identifiant RunPod.

Erreur fréquente signalée : coller l'URL affichée dans la console RunPod
(``https://api.runpod.ai/v2/<id>/run``) au lieu du seul identifiant, ce qui
fait échouer tous les appels avec un 404 sans indice sur la cause.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import config


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config._settings = None
    yield
    config._settings = None


def test_default_data_dir_uses_cwd_in_source_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert config._default_data_dir() == Path.cwd() / "data"


def test_default_data_dir_uses_localappdata_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert config._default_data_dir() == tmp_path / "Transcription"


def test_default_data_dir_falls_back_without_localappdata(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    expected = Path.home() / "AppData" / "Local" / "Transcription"
    assert config._default_data_dir() == expected


def test_nim_key_is_masked_in_public_settings():
    settings = config.save_settings(
        {"nim_api_key": "nvapi-secret", "nim_fallback_enabled": True}
    )
    public = settings.public_dict()
    assert public["nim_api_key"] == ""
    assert public["nim_api_key_set"] is True
    assert public["nim_fallback_enabled"] is True
