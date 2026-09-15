"""Démarrage du serveur, partagé entre ``run.py`` (CLI) et ``app/desktop.py``
(icône barre système).

Isole la logique indépendante de l'interface de lancement : trouver un port
libre, démarrer uvicorn dans un thread, attendre qu'il écoute réellement
avant d'ouvrir le navigateur, et l'arrêter proprement sans attente
indéfinie (important depuis un callback de tray, qui ne doit jamais geler).
"""
from __future__ import annotations

import logging
import socket
import threading
import time
import webbrowser

import uvicorn

logger = logging.getLogger(__name__)


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


def open_browser_later(url: str, delay: float = 1.5) -> None:
    def opener() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
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


def start_server(host: str, port: int, reload: bool = False) -> ServerHandle:
    """Démarre le serveur dans un thread et retourne son ``ServerHandle``."""
    config = uvicorn.Config(
        "app.server:app",
        host=host,
        port=port,
        reload=reload,
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
