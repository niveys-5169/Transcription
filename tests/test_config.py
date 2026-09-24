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
    monkeypatch.setattr(config, "SECRETS_PATH", tmp_path / "partage" / "secrets.json")
    monkeypatch.setattr(sys, "platform", "linux")
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


def test_hugging_face_token_is_masked_in_public_settings():
    settings = config.save_settings({"hf_token": "hf-secret"})
    public = settings.public_dict()
    assert public["hf_token"] == ""
    assert public["hf_token_set"] is True


def test_doc_maitre_notebooklm_reste_active_par_defaut():
    assert config.load_settings().notebooklm_master_doc_enabled is True


def test_domain_label_updates_only_default_obsidian_paths():
    settings = config.save_settings({"domain_label": "Droit social"})
    assert settings.obsidian_entities_folder == "Formation/Droit social/Entités"
    assert settings.obsidian_glossary_note == "Formation/Droit social/Glossaire Droit social.md"


# ------------------------------------------------ clés partagées exe / lancer.bat


def _changer_d_installation(monkeypatch, tmp_path, nom):
    """Simule l'autre lanceur : autre config.json, même fichier de clés."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / nom / "config.json")
    config._settings = None


def test_une_cle_saisie_dans_l_exe_est_vue_par_lancer_bat(monkeypatch, tmp_path):
    _changer_d_installation(monkeypatch, tmp_path, "exe")
    config.save_settings({"hf_token": "hf-partage", "default_model": "tiny"})

    _changer_d_installation(monkeypatch, tmp_path, "source")
    settings = config.load_settings()
    assert settings.hf_token == "hf-partage"
    # Seules les clés sont communes, pas les autres réglages.
    assert settings.default_model == config.Settings().default_model != "tiny"


def test_les_cles_deja_saisies_sont_reprises_des_deux_installations(monkeypatch, tmp_path):
    import json

    (tmp_path / "exe").mkdir()
    (tmp_path / "exe" / "config.json").write_text(
        json.dumps({"runpod_api_key": "rp-exe"}), encoding="utf-8")
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "config.json").write_text(
        json.dumps({"hf_token": "hf-source"}), encoding="utf-8")

    _changer_d_installation(monkeypatch, tmp_path, "exe")
    assert config.load_settings().runpod_api_key == "rp-exe"
    # Un enregistrement depuis l'exe, sans jeton HF, ne bloque pas sa reprise.
    config.save_settings({"default_model": "large-v3"})

    _changer_d_installation(monkeypatch, tmp_path, "source")
    settings = config.load_settings()
    assert (settings.runpod_api_key, settings.hf_token) == ("rp-exe", "hf-source")

    _changer_d_installation(monkeypatch, tmp_path, "exe")
    assert config.load_settings().hf_token == "hf-source"


def test_une_cle_effacee_n_est_pas_reimportee(monkeypatch, tmp_path):
    _changer_d_installation(monkeypatch, tmp_path, "exe")
    config.save_settings({"hf_token": "hf-ancien"})
    config.save_settings({"hf_token": "__clear__"})

    _changer_d_installation(monkeypatch, tmp_path, "exe")
    assert config.load_settings().hf_token == ""


def test_sous_windows_lancer_bat_reprend_les_cles_de_l_exe(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert config._default_secrets_path() == tmp_path / "Transcription" / "secrets.json"
    (tmp_path / "Transcription").mkdir()
    (tmp_path / "Transcription" / "config.json").write_text(
        json.dumps({"hf_token": "hf-exe"}), encoding="utf-8")

    _changer_d_installation(monkeypatch, tmp_path, "source")
    assert config.load_settings().hf_token == "hf-exe"
