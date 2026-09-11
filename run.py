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
import socket
import sys
import threading
import time
import webbrowser

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


def open_browser_later(url: str, delay: float = 1.5) -> None:
    def opener() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=opener, daemon=True).start()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        import uvicorn
    except ImportError:
        print(
            "Les dépendances ne sont pas installées.\n"
            "    pip install -r requirements-app.txt",
            file=sys.stderr,
        )
        return 1

    from app import config, media

    config.ensure_dirs()
    if not media.ffmpeg_available():
        print(
            "Attention : ffmpeg est introuvable. L'extraction audio échouera.\n"
            "    pip install imageio-ffmpeg      (le plus simple)\n"
            "    ou installez ffmpeg depuis https://ffmpeg.org\n",
            file=sys.stderr,
        )

    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{display_host}:{args.port}"

    if port_is_taken(args.host, args.port):
        print(
            f"\nLe port {args.port} est déjà utilisé.\n"
            f"L'application tourne peut-être déjà : essayez d'abord d'ouvrir\n"
            f"    {url}\n"
            f"dans votre navigateur. Sinon, relancez sur un autre port :\n"
            f"    lancer.bat --port {args.port + 1}   (Windows)\n"
            f"    ./lancer.sh --port {args.port + 1}  (macOS/Linux)\n",
            file=sys.stderr,
        )
        return 1

    print(f"\n  Transcription de cours  →  {url}")
    print("  (Ctrl+C pour arrêter)\n")

    if not args.no_browser:
        open_browser_later(url)

    try:
        uvicorn.run(
            "app.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="warning",
        )
    except OSError as exc:
        # Filet de sécurité : le port a pu se libérer puis se reprendre entre
        # le test ci-dessus et le démarrage réel du serveur.
        print(f"\nLe serveur n'a pas pu démarrer : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
