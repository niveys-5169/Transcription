"""Contrat du panneau Windows qui termine une mise à jour autonome."""
from __future__ import annotations

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
