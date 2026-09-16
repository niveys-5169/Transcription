"""Contrat du panneau Windows qui termine une mise à jour autonome."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import updates


def test_update_script_shows_progress_and_preserves_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.os, "getpid", lambda: 4242)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))

    source = tmp_path / "new bundle"
    target = tmp_path / "Transcription"
    script = updates._write_update_script(tmp_path, source, target)

    content = script.read_text(encoding="utf-8-sig")
    assert script.name == "apply-update.ps1"
    assert "$form.Show()" in content
    assert "Attente de la fermeture de Verbatim" in content
    assert "Copie des nouveaux fichiers" in content
    assert "robocopy $source $target /MIR" in content
    assert "update.log" in content
    assert "ERREUR:" in content
    assert "Start-Process -FilePath" in content
    assert "installer-ready" in content


def test_download_writes_a_log_before_opening_the_network(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(updates, "is_packaged", lambda: True)
    monkeypatch.setattr(updates.sys, "executable", str(tmp_path / "Transcription" / "Transcription.exe"))
    (tmp_path / "Transcription").mkdir()
    (tmp_path / "Transcription" / "Transcription.exe").touch()

    def network_failure(*_args, **_kwargs):
        raise OSError("réseau indisponible")

    monkeypatch.setattr(updates.urllib.request, "urlopen", network_failure)
    try:
        updates.download_and_restart("https://github.com/niveys-5169/Transcription/releases/download/latest/Transcription-Windows.zip")
    except OSError:
        pass

    log = tmp_path / "local-app-data" / "Transcription" / "update.log"
    assert "Téléchargement démarré" in log.read_text(encoding="utf-8")


def test_installateur_ne_confirme_pas_le_demarrage_si_powershell_meurt(tmp_path):
    process = SimpleNamespace(poll=lambda: 1)

    with pytest.raises(RuntimeError, match="arrêté"):
        updates._wait_for_installer_ready(process, tmp_path / "installer-ready")


def test_installateur_confirme_le_demarrage_apres_son_premier_log(tmp_path):
    marker = tmp_path / "installer-ready"
    marker.write_text("ready", encoding="utf-8")
    process = SimpleNamespace(poll=lambda: None)

    updates._wait_for_installer_ready(process, marker)
