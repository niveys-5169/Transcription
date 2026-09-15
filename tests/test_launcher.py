"""Contrat du lanceur : un port occupé ne doit pas empêcher le démarrage."""

from __future__ import annotations

import socket
import time

from app import launcher


def test_trouve_le_premier_port_libre_apres_un_port_occupe():
    """Reproduit le cas où le port demandé est déjà occupé."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        requested_port = occupied.getsockname()[1]

        selected_port = launcher.find_available_port("127.0.0.1", requested_port)

    assert selected_port == requested_port + 1


def test_conserve_le_port_demande_lorsqu_il_est_libre():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        requested_port = probe.getsockname()[1]

    assert launcher.find_available_port("127.0.0.1", requested_port) == requested_port


def test_start_server_wait_until_ready_and_shutdown():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    handle = launcher.start_server("127.0.0.1", port)
    try:
        assert handle.wait_until_ready(timeout=10.0) is True
    finally:
        handle.shutdown(timeout=5.0)

    assert not launcher.port_is_taken("127.0.0.1", port)


def test_wait_until_ready_times_out_when_nothing_listens():
    class _DeadThread:
        def is_alive(self) -> bool:
            return True

    handle = launcher.ServerHandle.__new__(launcher.ServerHandle)
    handle._host = "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        handle._port = probe.getsockname()[1]
    handle._thread = _DeadThread()

    start = time.monotonic()
    assert handle.wait_until_ready(timeout=0.2) is False
    assert time.monotonic() - start < 2.0
