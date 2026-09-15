#!/usr/bin/env python3
"""Point d'entrée du build PyInstaller (icône barre système, sans console).

Doit rester à la racine du dépôt : un ``.spec`` PyInstaller pointé
directement sur ``app/desktop.py`` fait de ``app/`` la racine effective de
l'exécutable, ce qui casse les imports ``from app import ...``.
"""
from __future__ import annotations

from app.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
