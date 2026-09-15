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


def test_setup_logging_is_idempotent(tmp_path):
    setup_logging(tmp_path)
    setup_logging(tmp_path)

    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1

    logging.getLogger("test").info("une seule fois")
    log_file = tmp_path / "logs" / "app.log"
    assert log_file.read_text(encoding="utf-8").count("une seule fois") == 1
