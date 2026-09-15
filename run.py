#!/usr/bin/env python3
"""Lance l'application de transcription et ouvre le navigateur.

    python run.py                 # http://127.0.0.1:8765
    python run.py --port 9000     # autre port
    python run.py --no-browser    # sans ouvrir le navigateur
    python run.py --host 0.0.0.0  # accessible depuis le réseau local
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcription de cours")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="ne pas ouvrir le navigateur au démarrage",
    )
    parser.add_argument(
        "--reload", action="store_true", help="rechargement à chaud (développement)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    from app import config
    from app.logging_setup import setup_logging

    config.ensure_dirs()
    setup_logging(config.DATA_DIR)
    logging.getLogger(__name__).info("Démarrage de l'application (data_dir=%s)", config.DATA_DIR)

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print(
            "Les dépendances ne sont pas installées.\n"
            "    pip install -r requirements-app.txt",
            file=sys.stderr,
        )
        return 1

    from app import media
    from app.launcher import find_available_port, open_browser_later, start_server

    if not media.ffmpeg_available():
        print(
            "Attention : ffmpeg est introuvable. L'extraction audio échouera.\n"
            "    pip install imageio-ffmpeg      (le plus simple)\n"
            "    ou installez ffmpeg depuis https://ffmpeg.org\n",
            file=sys.stderr,
        )

    try:
        selected_port = find_available_port(args.host, args.port)
    except OSError as exc:
        print(
            f"\nLe serveur n'a pas pu trouver de port libre : {exc}\n",
            file=sys.stderr,
        )
        return 1

    if selected_port != args.port:
        print(f"\nLe port {args.port} est déjà utilisé ; démarrage sur le premier port libre : {selected_port}.")
    args.port = selected_port
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{display_host}:{args.port}"

    print(f"\n  Transcription de cours  →  {url}")
    print("  (Ctrl+C pour arrêter)\n")

    if args.reload:
        # Le rechargement à chaud repose sur le superviseur multiprocessus
        # d'uvicorn.run(), incompatible avec le thread démon de ServerHandle
        # — flag de développement uniquement, on bloque ici comme avant.
        if not args.no_browser:
            open_browser_later(url)
        try:
            uvicorn.run(
                "app.server:app",
                host=args.host,
                port=args.port,
                reload=True,
                log_level="warning",
            )
        except OSError as exc:
            print(f"\nLe serveur n'a pas pu démarrer : {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        handle = start_server(args.host, args.port)
    except OSError as exc:
        # Filet de sécurité : le port a pu se libérer puis se reprendre entre
        # le test ci-dessus et le démarrage réel du serveur.
        print(f"\nLe serveur n'a pas pu démarrer : {exc}", file=sys.stderr)
        return 1

    if not handle.wait_until_ready():
        print("\nLe serveur n'a pas pu démarrer.", file=sys.stderr)
        return 1

    if not args.no_browser:
        open_browser_later(url, delay=0)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
