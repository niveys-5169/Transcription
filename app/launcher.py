"""Démarrage du serveur, partagé entre ``run.py`` (CLI) et ``app/desktop.py``
(icône barre système).

Isole la logique indépendante de l'interface de lancement : trouver un port
libre, démarrer uvicorn dans un thread, attendre qu'il écoute réellement
avant d'ouvrir le navigateur, et l'arrêter proprement sans attente
indéfinie (important depuis un callback de tray, qui ne doit jamais geler).
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

logger = logging.getLogger(__name__)

# Navigateurs à base Chromium, dans l'ordre de préférence : leur option
# ``--app=`` ouvre l'URL dans une fenêtre dédiée (sans onglets ni barre
# d'adresse), ce qu'aucune option standard de ``webbrowser`` ne permet.
_LINUX_APP_BROWSERS = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable", "brave-browser",
)
_MACOS_APP_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
_WINDOWS_APP_BROWSERS = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
)


def _find_app_browser() -> str | None:
    if sys.platform == "win32":
        for candidate in _WINDOWS_APP_BROWSERS:
            path = os.path.expandvars(candidate)
            if os.path.isfile(path):
                return path
        return None
    if sys.platform == "darwin":
        return next((path for path in _MACOS_APP_BROWSERS if os.path.isfile(path)), None)
    return next((shutil.which(name) for name in _LINUX_APP_BROWSERS if shutil.which(name)), None)


def open_app_window(url: str, profile_dir: Path | None = None) -> bool:
    """Ouvre ``url`` dans une fenêtre de navigateur dédiée, sans onglets.

    Repose sur le mode « application » des navigateurs Chromium (Chrome,
    Edge, Brave...) ; sans un tel navigateur, retourne ``False`` pour que
    l'appelant se rabatte sur ``webbrowser.open`` (onglet classique).
    Un profil dédié évite tout conflit avec le navigateur habituel de
    l'utilisateur (verrou de profil, extensions, session déjà ouverte).
    """
    browser_path = _find_app_browser()
    if not browser_path:
        return False

    args = [browser_path, f"--app={url}"]
    if profile_dir is not None:
        args.append(f"--user-data-dir={profile_dir}")

    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        logger.exception("Impossible d'ouvrir la fenêtre dédiée du navigateur.")
        return False
    return True


def open_browser_window(url: str, profile_dir: Path | None = None) -> None:
    """Ouvre ``url`` en fenêtre dédiée si possible, sinon en onglet classique."""
    if not open_app_window(url, profile_dir):
        webbrowser.open(url)


def port_is_taken(host: str, port: int) -> bool:
    """Quelqu'un écoute-t-il déjà sur ce port ?

    On teste par une connexion, pas par un bind suivi d'une fermeture : sur
    Windows notamment, un bind peut réussir puis échouer juste après côté
    uvicorn selon l'état de la socket — une connexion qui aboutit est le test
    le plus direct de « quelque chose répond déjà ici ».
    """
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((probe_host, port)) == 0


def find_available_port(host: str, requested_port: int) -> int:
    """Retourne le premier port disponible à partir de ``requested_port``.

    Le port par défaut peut être utilisé par une autre instance locale. Dans
    ce cas, démarrer sur le port suivant évite d'imposer à l'utilisateur une
    relance manuelle avec ``--port``.
    """
    for port in range(requested_port, 65536):
        if not port_is_taken(host, port):
            return port
    raise OSError(f"Aucun port libre entre {requested_port} et 65535.")


def open_browser_later(url: str, delay: float = 1.5, profile_dir: Path | None = None) -> None:
    def opener() -> None:
        time.sleep(delay)
        try:
            open_browser_window(url, profile_dir)
        except Exception:
            pass

    threading.Thread(target=opener, daemon=True).start()


class ServerHandle:
    """Encapsule un serveur uvicorn lancé dans un thread démon."""

    def __init__(self, server: uvicorn.Server, host: str, port: int) -> None:
        self._server = server
        self._host = host
        self._port = port
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._server.run()
        except Exception:
            logger.exception("Le serveur s'est arrêté sur une erreur.")

    def wait_until_ready(self, timeout: float = 15.0) -> bool:
        """Attend que le serveur écoute réellement, sans délai fixe.

        Retourne ``True`` dès qu'une connexion aboutit, ``False`` si le
        délai est dépassé (le thread serveur a pu échouer à démarrer).
        """
        deadline = time.monotonic() + timeout
        probe_host = "127.0.0.1" if self._host in {"0.0.0.0", "::"} else self._host
        while time.monotonic() < deadline:
            if not self._thread.is_alive():
                return False
            if port_is_taken(probe_host, self._port):
                return True
            time.sleep(0.05)
        return False

    def shutdown(self, timeout: float = 5.0) -> None:
        """Arrêt propre borné : jamais d'attente indéfinie (callback de tray)."""
        self._server.should_exit = True
        self._thread.join(timeout)


def start_server(host: str, port: int) -> ServerHandle:
    """Démarre le serveur dans un thread et retourne son ``ServerHandle``.

    Passe l'objet FastAPI directement à uvicorn plutôt que la chaîne
    ``"app.server:app"`` : PyInstaller ne suit que les vrais ``import`` du
    code pour décider quoi embarquer, pas les chaînes qu'uvicorn importerait
    lui-même dynamiquement — avec la chaîne, app/server.py (et tout ce qu'il
    importe : db, pipeline, exporters...) restait absent du build empaqueté.
    """
    from . import server as server_module

    config = uvicorn.Config(
        server_module.app,
        host=host,
        port=port,
        log_level="warning",
        # On configure déjà le logging nous-mêmes (voir logging_setup.py) :
        # laisser uvicorn appliquer sa propre config par défaut plante en
        # mode "windowed" (exe PyInstaller sans console), où sys.stdout /
        # sys.stderr valent None et son formatter coloré appelle
        # stream.isatty() dessus.
        log_config=None,
    )
    server = uvicorn.Server(config)
    return ServerHandle(server, host, port)
