"""Configuration du logging applicatif : fichier tournant + console.

Utilisé par ``run.py`` (mode source, console) et ``app/desktop.py`` (mode
empaqueté, sans console). Voir ``ensure_dirs`` dans ``app/config.py`` pour la
création du dossier ``logs``.
"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s  %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

# httpx journalise chaque requête avec son URL complète, en INFO. La clé API
# RunPod voyage en paramètre de requête (app/engines/runpod_pod.py,
# `?api_key=...`) : sans ce masquage, elle finit en clair dans app.log, et
# de là dans tout extrait de log collé pour un diagnostic.
_SECRET_PARAMS = re.compile(r"(?i)\b(api_key|token|key)=([^&\s\"']+)")


class RedactSecretsFilter(logging.Filter):
    """Remplace la valeur des paramètres d'URL sensibles par ``***``.

    Le message est figé dès le premier handler (``getMessage`` puis
    remplacement de ``msg``/``args``) pour que tous les handlers voient la
    version masquée.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = _SECRET_PARAMS.sub(r"\1=***", message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def setup_logging(data_dir: Path, level: int = logging.INFO) -> None:
    """Attache un handler fichier (et console si disponible) au root logger.

    Idempotent : un second appel (``run.py`` et ``app/desktop.py`` peuvent
    tous deux l'invoquer selon le point d'entrée) ne rajoute pas de handlers
    en double, ce qui écrirait chaque ligne plusieurs fois.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Filtre sur le logger httpx (pas sur les handlers) : les filtres d'un
    # logger s'appliquent aux enregistrements qu'il émet lui-même, avant
    # propagation à tous les handlers du root, fichier comme console.
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, RedactSecretsFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(RedactSecretsFilter())

    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(file_handler)

    has_console_handler = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root.handlers
    )
    if sys.stderr is not None and not has_console_handler:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(console_handler)
