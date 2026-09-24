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

# Si ce dossier est un clone git, on récupère la dernière version avant de
# démarrer. Un échec (hors ligne, conflit local) n'empêche pas le lancement.
if [ -d ".git" ] && command -v git >/dev/null 2>&1; then
  echo "  Recherche de mises à jour…"
  git pull --ff-only || echo "  [~] Mise à jour impossible, lancement de la version actuelle."
fi

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "  Création de l'environnement Python (une seule fois)…"
  "$PYTHON" -m venv .venv
fi

# Copie du requirements-app.txt installé : s'il change après une mise à
# jour, les dépendances sont réinstallées automatiquement.
DEPS_MARKER=".venv/.requirements-installees.txt"
if ! cmp -s requirements-app.txt "$DEPS_MARKER"; then
  echo "  Installation des dépendances (quelques minutes la première fois)…"
  "$VENV_PY" -m pip install --upgrade pip --quiet
  "$VENV_PY" -m pip install -r requirements-app.txt
  cp requirements-app.txt "$DEPS_MARKER"
fi

echo "  Démarrage du serveur…"
echo
exec "$VENV_PY" run.py "$@"
