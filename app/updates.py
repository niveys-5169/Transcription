"""Mise à jour sécurisée du paquet Windows publié sur GitHub."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from .build_info import BUILD_ID
from .process import hidden_console_flags

REPOSITORY = "niveys-5169/Transcription"
RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/latest"
ASSET_NAME = "Transcription-Windows.zip"
DOWNLOAD_TIMEOUT_SECONDS = 20
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

logger = logging.getLogger(__name__)
_state_lock = threading.Lock()
_state: dict = {"phase": "idle", "message": ""}


def _log_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Transcription"
    root.mkdir(parents=True, exist_ok=True)
    return root / "update.log"


def _set_state(phase: str, message: str, **extra: object) -> None:
    with _state_lock:
        _state.clear()
        _state.update({"phase": phase, "message": message, **extra})


def status() -> dict:
    """État courant, séparé de la vérification distante de la release."""
    with _state_lock:
        return dict(_state)


def _write_log(message: str) -> None:
    line = f"{datetime.now():%H:%M:%S}  {message}"
    with _log_path().open("a", encoding="utf-8") as stream:
        stream.write(f"{line}\n")
    logger.info("Mise à jour : %s", message)


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
        return {
            "supported": True,
            "available": available,
            "version": target[:7],
            "download_url": asset.get("browser_download_url") if asset else None,
            "installation": status(),
        }
    except Exception:
        return {"supported": True, "available": False, "installation": status()}


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
    _set_state("downloading", "Téléchargement de la mise à jour…", downloaded=0, total=None)
    _write_log("Téléchargement démarré.")
    request = urllib.request.Request(download_url, headers={"User-Agent": "Verbatim updater"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # nosec B310: URL validée ci-dessus
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else None
        downloaded = 0
        with archive.open("wb") as output:
            while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                output.write(chunk)
                downloaded += len(chunk)
                _set_state("downloading", "Téléchargement de la mise à jour…", downloaded=downloaded, total=total)
                if downloaded == len(chunk) or downloaded % (10 * DOWNLOAD_CHUNK_SIZE) < len(chunk):
                    detail = f"{downloaded // (1024 * 1024)} Mo"
                    if total:
                        detail += f" / {total // (1024 * 1024)} Mo"
                    _write_log(f"Téléchargement : {detail}.")
    _write_log("Téléchargement terminé, vérification du paquet.")
    _set_state("preparing", "Préparation de l’installation…")
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
    _set_state("restarting", "Installation démarrée, Verbatim redémarre.")
    _write_log("Installateur démarré.")


def start_download_and_restart(download_url: str, on_ready: Callable[[], None]) -> bool:
    """Lance la mise à jour hors du thread web afin de garder l'UI réactive."""
    with _state_lock:
        if _state.get("phase") in {"downloading", "preparing"}:
            return False
        _state.clear()
        _state.update({"phase": "downloading", "message": "Préparation du téléchargement…", "downloaded": 0, "total": None})

    def worker() -> None:
        try:
            download_and_restart(download_url)
        except Exception as exc:  # l'erreur doit rester visible même sans console PyInstaller
            logger.exception("Échec de la mise à jour")
            _set_state("failed", f"Mise à jour impossible : {exc}")
            _write_log(f"ERREUR: {exc}")
            return
        on_ready()

    threading.Thread(target=worker, name="verbatim-update", daemon=True).start()
    return True
