"""Mise à jour sécurisée du paquet Windows publié sur GitHub."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .build_info import BUILD_ID
from .process import hidden_console_flags

REPOSITORY = "niveys-5169/Transcription"
RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/latest"
ASSET_NAME = "Transcription-Windows.zip"


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def check() -> dict:
    """Retourne l'état de la dernière release, sans échec réseau bloquant."""
    if not is_packaged() or BUILD_ID == "development":
        return {"supported": False, "available": False}
    try:
        request = urllib.request.Request(RELEASE_URL, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=4) as response:  # nosec B310: URL constante GitHub
            release = json.load(response)
        asset = next((item for item in release.get("assets", []) if item.get("name") == ASSET_NAME), None)
        target = str(release.get("target_commitish") or "")
        available = bool(asset and target and not target.startswith(BUILD_ID))
        return {"supported": True, "available": available, "version": target[:7], "download_url": asset.get("browser_download_url") if asset else None}
    except Exception:
        return {"supported": True, "available": False}


def _powershell_literal(value: Path | str) -> str:
    """Encode une valeur dans une chaîne PowerShell entre apostrophes."""
    return "'" + str(value).replace("'", "''") + "'"


def _write_update_script(work_dir: Path, source_dir: Path, install_dir: Path) -> Path:
    """Crée le panneau autonome qui applique et journalise la mise à jour.

    L'application FastAPI doit s'arrêter avant de pouvoir remplacer son propre
    exécutable. Le script est donc volontairement indépendant : sa fenêtre
    reste affichée pendant cet intervalle, y compris si la copie échoue.
    """
    script = work_dir / "apply-update.ps1"
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Transcription"
    script.write_text(
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "Add-Type -AssemblyName System.Drawing\n"
        f"$processIdToWait = {os.getpid()}\n"
        f"$source = {_powershell_literal(source_dir)}\n"
        f"$target = {_powershell_literal(install_dir)}\n"
        f"$logDirectory = {_powershell_literal(log_dir)}\n"
        "$logFile = Join-Path $logDirectory 'update.log'\n"
        "New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null\n"
        "$form = New-Object System.Windows.Forms.Form\n"
        "$form.Text = 'Verbatim - Installation de la mise a jour'\n"
        "$form.Size = New-Object System.Drawing.Size(720, 430)\n"
        "$form.StartPosition = 'CenterScreen'\n"
        "$form.TopMost = $true\n"
        "$status = New-Object System.Windows.Forms.Label\n"
        "$status.Location = New-Object System.Drawing.Point(18, 16)\n"
        "$status.Size = New-Object System.Drawing.Size(670, 28)\n"
        "$status.Text = 'Preparation de l installation...'\n"
        "$logs = New-Object System.Windows.Forms.TextBox\n"
        "$logs.Location = New-Object System.Drawing.Point(18, 52)\n"
        "$logs.Size = New-Object System.Drawing.Size(670, 285)\n"
        "$logs.Multiline = $true\n"
        "$logs.ReadOnly = $true\n"
        "$logs.ScrollBars = 'Vertical'\n"
        "$logs.Font = New-Object System.Drawing.Font('Consolas', 9)\n"
        "$close = New-Object System.Windows.Forms.Button\n"
        "$close.Text = 'Fermer'\n"
        "$close.Location = New-Object System.Drawing.Point(598, 350)\n"
        "$close.Add_Click({ $form.Close() })\n"
        "$form.Controls.AddRange(@($status, $logs, $close))\n"
        "$writeLog = { param([string]$message)\n"
        "  $line = ('{0:HH:mm:ss}  {1}' -f (Get-Date), $message)\n"
        "  Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8\n"
        "  $logs.AppendText($line + [Environment]::NewLine)\n"
        "  [System.Windows.Forms.Application]::DoEvents()\n"
        "}\n"
        "$form.Show()\n"
        "& $writeLog 'Mise a jour lancee.'\n"
        "try {\n"
        "  & $writeLog 'Attente de la fermeture de Verbatim...'\n"
        "  while (Get-Process -Id $processIdToWait -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 500; [System.Windows.Forms.Application]::DoEvents() }\n"
        "  & $writeLog 'Copie des nouveaux fichiers...'\n"
        "  & robocopy $source $target /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP\n"
        "  if ($LASTEXITCODE -gt 7) { throw ('robocopy a echoue (code {0})' -f $LASTEXITCODE) }\n"
        "  & $writeLog 'Copie terminee.'\n"
        "  $status.Text = 'Mise a jour terminee. Redemarrage de Verbatim...'\n"
        "  & $writeLog 'Redemarrage de Verbatim.'\n"
        "  Start-Process -FilePath (Join-Path $target 'Transcription.exe')\n"
        "  Start-Sleep -Seconds 2\n"
        "  $form.Close()\n"
        "} catch {\n"
        "  $status.Text = 'Echec de la mise a jour - consultez les logs ci-dessous.'\n"
        "  $status.ForeColor = [System.Drawing.Color]::Firebrick\n"
        "  & $writeLog ('ERREUR: ' + $_.Exception.Message)\n"
        "  & $writeLog ('Journal conserve dans: ' + $logFile)\n"
        "  $form.Activate()\n"
        "  while ($form.Visible) { Start-Sleep -Milliseconds 200; [System.Windows.Forms.Application]::DoEvents() }\n"
        "}\n",
        encoding="utf-8-sig",
    )
    return script


def download_and_restart(download_url: str) -> None:
    """Télécharge puis prépare le remplacement après l'arrêt de l'exe actuel."""
    if not is_packaged() or not download_url.startswith(f"https://github.com/{REPOSITORY}/releases/download/latest/"):
        raise ValueError("Mise à jour indisponible.")
    install_dir = Path(sys.executable).resolve().parent
    if install_dir.name != "Transcription" or not (install_dir / "Transcription.exe").is_file():
        raise ValueError("Le dossier d'installation n'est pas reconnu.")

    work_dir = Path(tempfile.mkdtemp(prefix="transcription-update-"))
    archive = work_dir / ASSET_NAME
    urllib.request.urlretrieve(download_url, archive)  # nosec B310: URL validée ci-dessus
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(work_dir / "new")
    source_dir = work_dir / "new"
    if not (source_dir / "Transcription.exe").is_file():
        raise ValueError("Le paquet de mise à jour est incomplet.")

    script = _write_update_script(work_dir, source_dir, install_dir)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | hidden_console_flags()
        ),
    )
