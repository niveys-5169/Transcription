"""Contrat du lanceur : un port occupé ne doit pas empêcher le démarrage."""

from __future__ import annotations

import socket

import run


def test_trouve_le_premier_port_libre_apres_un_port_occupe():
    """Reproduit le cas où le port demandé est déjà occupé."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        requested_port = occupied.getsockname()[1]

        selected_port = run.find_available_port("127.0.0.1", requested_port)

    assert selected_port == requested_port + 1


def test_conserve_le_port_demande_lorsqu_il_est_libre():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        requested_port = probe.getsockname()[1]

    assert run.find_available_port("127.0.0.1", requested_port) == requested_port
