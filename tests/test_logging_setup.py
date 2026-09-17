"""Le fichier de logs se crée une fois et ne duplique pas ses handlers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

from app.logging_setup import setup_logging


@pytest.fixture(autouse=True)
def _clean_root_logger():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    yield
    for handler in root.handlers:
        handler.close()
    root.handlers = original_handlers


def test_setup_logging_creates_log_file(tmp_path):
    setup_logging(tmp_path)
    logging.getLogger("test").info("bonjour")

    log_file = tmp_path / "logs" / "app.log"
    assert log_file.exists()
    assert "bonjour" in log_file.read_text(encoding="utf-8")


def test_la_cle_api_runpod_est_masquee_dans_les_logs_httpx(tmp_path):
    """httpx journalise l'URL complète de chaque requête ; la clé API RunPod
    y voyage en paramètre (?api_key=...). Elle ne doit jamais atterrir en
    clair dans app.log — un extrait de log collé pour un diagnostic l'a
    déjà exposée une fois."""
    setup_logging(tmp_path)
    logging.getLogger("httpx").info(
        'HTTP Request: POST %s "%s"',
        "https://api.runpod.io/graphql?api_key=rpa_SECRET123&x=1",
        "HTTP/1.1 200 OK",
    )

    contenu = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "rpa_SECRET123" not in contenu
    assert "api_key=***&x=1" in contenu
    assert "HTTP/1.1 200 OK" in contenu


def test_le_masquage_ne_touche_pas_aux_autres_messages(tmp_path):
    setup_logging(tmp_path)
    logging.getLogger("httpx").info("HTTP Request: GET https://x-8000.proxy.runpod.net/health")
    contenu = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "https://x-8000.proxy.runpod.net/health" in contenu


def test_setup_logging_is_idempotent(tmp_path):
    setup_logging(tmp_path)
    setup_logging(tmp_path)

    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1

    logging.getLogger("test").info("une seule fois")
    log_file = tmp_path / "logs" / "app.log"
    assert log_file.read_text(encoding="utf-8").count("une seule fois") == 1
