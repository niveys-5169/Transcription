#!/usr/bin/env bash
# ---------------------------------------------------------------------
#  Transcription de cours — lanceur macOS / Linux
#  Premier lancement : crée un environnement Python isolé et installe les
#  dépendances. Ensuite, démarre simplement l'application.
# ---------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  Transcription de cours"
echo "  ----------------------"
echo

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "  [X] Python 3 est introuvable. Installez-le puis relancez ce script."
  exit 1
fi
echo "  Python détecté : $PYTHON ($("$PYTHON" --version))"

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "  Création de l'environnement Python (une seule fois)…"
  "$PYTHON" -m venv .venv
fi

if [ ! -f ".venv/.dependances-ok" ]; then
  echo "  Installation des dépendances (quelques minutes la première fois)…"
  "$VENV_PY" -m pip install --upgrade pip --quiet
  "$VENV_PY" -m pip install -r requirements-app.txt
  touch ".venv/.dependances-ok"
fi

echo "  Démarrage du serveur…"
echo
exec "$VENV_PY" run.py "$@"
