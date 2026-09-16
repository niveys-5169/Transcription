"""Options communes aux processus externes de l'application."""
from __future__ import annotations

import subprocess


def hidden_console_flags() -> int:
    """Empêche l'ouverture d'une console par un programme enfant sous Windows.

    La valeur est nulle sur les autres plates-formes et peut donc être passée
    sans condition à ``subprocess.run`` comme à ``subprocess.Popen``.
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
