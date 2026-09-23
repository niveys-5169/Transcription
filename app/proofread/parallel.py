"""Exécution concurrente de blocs indépendants, rappels dans le thread appelant.

Relecture et vérification passent l'essentiel de leur temps à attendre le
réseau : chaque bloc est un appel indépendant. Les lancer de front divise la
durée par le nombre d'appels simultanés, sans rien changer à ce qui est
demandé au modèle.

``on_done`` est toujours appelé dans le thread appelant, jamais dans un
thread de travail : les écritures SQLite (checkpoint, progression) restent
sérialisées comme avant.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Sequence, TypeVar

from .base import ProofreadError

T = TypeVar("T")
R = TypeVar("R")

# Intervalle de vérification de l'annulation pendant qu'un appel est en vol.
_CANCEL_POLL_SECONDS = 1.0


def run_in_parallel(
    items: Sequence[T],
    work: Callable[[T], R],
    *,
    workers: int,
    should_cancel: Callable[[], bool] | None = None,
    on_done: Callable[[int, R], None] | None = None,
    cancel_message: str = "Relecture annulée.",
) -> list[R]:
    """Applique ``work`` à chaque élément, résultats rendus dans l'ordre d'entrée.

    La première exception levée par un bloc interrompt l'ensemble : les blocs
    pas encore commencés sont abandonnés et l'exception remonte telle quelle.
    Les blocs déjà terminés ont été signalés à ``on_done`` (et donc
    checkpointés) : une reprise ne les rappelle pas.
    """
    if not items:
        return []
    results: list[R | None] = [None] * len(items)
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(int(workers), len(items))),
        thread_name_prefix="relecture",
    )
    try:
        pending = {executor.submit(work, item): index for index, item in enumerate(items)}
        while pending:
            if should_cancel is not None and should_cancel():
                raise ProofreadError(cancel_message)
            done, _ = wait(pending, timeout=_CANCEL_POLL_SECONDS, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=pending.__getitem__):
                index = pending.pop(future)
                result = future.result()
                results[index] = result
                if on_done is not None:
                    on_done(index, result)
    finally:
        # Sans attendre les appels en vol : une annulation ou un échec doit
        # rendre la main tout de suite. Leurs résultats sont simplement ignorés.
        executor.shutdown(wait=False, cancel_futures=True)
    return results  # type: ignore[return-value]
