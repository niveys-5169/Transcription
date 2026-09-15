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

    script = work_dir / "apply-update.cmd"
    script.write_text(
        "@echo off\r\n"
        f"set \"PID={os.getpid()}\"\r\n"
        f"set \"SOURCE={source_dir}\"\r\n"
        f"set \"TARGET={install_dir}\"\r\n"
        ":wait\r\n"
        "tasklist /fi \"PID eq %PID%\" /nh | findstr /r /c:\"%PID%\" >nul\r\n"
        "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\r\n"
        "robocopy \"%SOURCE%\" \"%TARGET%\" /MIR /R:2 /W:1 >nul\r\n"
        "start \"\" \"%TARGET%\\Transcription.exe\"\r\n"
        "del \"%~f0\"\r\n",
        encoding="utf-8",
    )
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
