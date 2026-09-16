"""Point d'entrée "application" : icône barre système, sans console.

Utilisé par le build PyInstaller (voir ``desktop.py`` à la racine et
``packaging/transcription.spec``). Contrairement à ``run.py`` (CLI de
développement), il ne prend pas d'arguments et gère lui-même l'ouverture du
navigateur, les logs fichier et l'instance unique.
"""
from __future__ import annotations

import logging
import os
import sys
import webbrowser
from pathlib import Path

from . import config, media
from .launcher import (
    ServerHandle,
    find_available_port,
    open_browser_window,
    port_is_taken,
    start_server,
)
from .logging_setup import setup_logging

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _existing_instance_url(host: str, port: int) -> str | None:
    """Une instance tourne-t-elle déjà sur le port par défaut ?

    Une icône de barre système ne doit jamais se dupliquer silencieusement
    en choisissant le port suivant comme le fait ``run.py`` — si l'appli
    tourne déjà, on ouvre son navigateur et on quitte ce nouveau processus.
    """
    if port_is_taken(host, port):
        return f"http://{host}:{port}"
    return None


def _open_logs_folder(data_dir) -> None:
    log_dir = data_dir / "logs"
    try:
        if sys.platform == "win32":
            os.startfile(log_dir)  # type: ignore[attr-defined]
        else:
            webbrowser.open(log_dir.as_uri())
    except Exception:
        logger.exception("Impossible d'ouvrir le dossier des logs.")


def main() -> int:
    config.ensure_dirs()
    setup_logging(config.DATA_DIR)
    logger.info("Démarrage de l'application (data_dir=%s)", config.DATA_DIR)

    browser_profile_dir = config.DATA_DIR / "browser-profile"

    existing = _existing_instance_url(HOST, DEFAULT_PORT)
    if existing is not None:
        open_browser_window(existing, browser_profile_dir)
        return 0

    if not media.ffmpeg_available():
        logger.warning("ffmpeg introuvable : l'extraction audio échouera.")

    try:
        port = find_available_port(HOST, DEFAULT_PORT)
        handle: ServerHandle = start_server(HOST, port)
    except OSError:
        logger.exception("Le serveur n'a pas pu démarrer.")
        return 1

    if not handle.wait_until_ready():
        logger.error("Le serveur n'a pas répondu à temps au démarrage.")
        return 1

    url = f"http://{HOST}:{port}"
    open_browser_window(url, browser_profile_dir)

    try:
        import pystray
        from PIL import Image
    except ImportError:
        logger.warning("pystray/Pillow indisponibles : pas d'icône barre système.")
        import time

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            handle.shutdown()
        return 0

    icon_path = Path(__file__).parent / "static" / "icon.ico"
    image = Image.open(icon_path) if icon_path.exists() else Image.new("RGB", (64, 64), "black")

    def _on_open(icon, item) -> None:
        open_browser_window(url, browser_profile_dir)

    def _on_logs(icon, item) -> None:
        _open_logs_folder(config.DATA_DIR)

    def _on_quit(icon, item) -> None:
        handle.shutdown()
        icon.stop()

    tray_icon = pystray.Icon(
        "transcription",
        image,
        "Transcription de cours",
        menu=pystray.Menu(
            pystray.MenuItem("Ouvrir", _on_open, default=True),
            pystray.MenuItem("Voir les logs", _on_logs),
            pystray.MenuItem("Quitter", _on_quit),
        ),
    )
    tray_icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
