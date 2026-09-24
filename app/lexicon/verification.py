"""Vérification web du lexique, avec propositions persistantes à valider."""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .. import config
from ..proofread.factcheck import Claim, Verdict, verify_claim as _verify_claim
from . import Term, load_lexicon

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_cancel = threading.Event()
# ``en_file`` : termes demandés et pas encore traités (en attente ou en
# cours) ; ``en_verification`` : sous-ensemble en cours d'appel. De nouveaux
# termes peuvent rejoindre la file pendant qu'une vérification tourne.
_state: dict = {
    "en_cours": False, "faits": 0, "total": 0, "terme_courant": None,
    "en_file": [], "en_verification": [],
}
_executor: ThreadPoolExecutor | None = None
# Incrémentée à chaque annulation : une tâche encore en file d'une
# génération antérieure se termine sans rien faire.
_generation = 0


def _proposals_path() -> Path:
    return config.DATA_DIR / "lexique_propositions.json"


def _load_proposals() -> list[dict]:
    path = _proposals_path()
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Propositions de lexique illisibles : %s", path)
        return []
    return value if isinstance(value, list) else []


def _save_proposals(proposals: list[dict]) -> None:
    path = _proposals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(proposals, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def question_for(term: Term) -> str:
    sigles = f", sigle {'/'.join(term.sigles)}," if term.sigles else ""
    return (
        "Le terme du domaine MJPM (protection juridique des majeurs) "
        f"« {term.terme} »{sigles} est défini ainsi : « {term.definition} ». "
        f"Référence juridique donnée : « {term.reference} ». Cette définition "
        "et cette référence sont-elles exactes ?"
    )


def verify_term(term: Term, *, settings) -> Verdict:
    claim = Claim(type="reference_juridique", citation=term.terme, question=question_for(term))
    return _verify_claim(claim, settings=settings)


def _proposal(term: Term, verdict: Verdict) -> dict:
    return {
        "terme": term.terme,
        "verdict": verdict.verdict,
        "forme_correcte": verdict.forme_correcte,
        "explication": verdict.explication,
        "sources": [asdict(source) for source in verdict.sources],
        "confiance": verdict.confiance,
        "origine": verdict.origine,
        "status": "attente",
    }


def proposals() -> list[dict]:
    with _lock:
        return _load_proposals()


def find_proposal(terme: str) -> dict | None:
    with _lock:
        return next((item for item in _load_proposals() if item.get("terme") == terme), None)


def mark_proposal(terme: str, status_value: str) -> bool:
    with _lock:
        items = _load_proposals()
        item = next((entry for entry in items if entry.get("terme") == terme), None)
        if item is None:
            return False
        item["status"] = status_value
        _save_proposals(items)
        return True


def _store_proposal(proposal: dict) -> None:
    with _lock:
        items = [item for item in _load_proposals() if item.get("terme") != proposal["terme"]]
        items.append(proposal)
        _save_proposals(items)


def _failure(term: Term, exc: Exception) -> dict:
    return {
        "terme": term.terme, "verdict": "erreur", "forme_correcte": "",
        "explication": str(exc), "sources": [], "confiance": "basse",
        "origine": "erreur", "status": "attente",
    }


def _release_executor() -> None:
    """À appeler sous ``_lock`` quand la file est vide."""
    global _executor
    _state["en_cours"] = False
    _state["terme_courant"] = None
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def _verify_one(term: Term, settings, generation: int) -> None:
    with _lock:
        if generation != _generation:
            return  # annulé avant d'avoir commencé
        _state["en_verification"].append(term.terme)
        _state["terme_courant"] = term.terme
    try:
        proposal = _proposal(term, verify_term(term, settings=settings))
    except Exception as exc:
        logger.exception("Échec de vérification du terme %s", term.terme)
        proposal = _failure(term, exc)
    # Un appel déjà lancé va à son terme même après une annulation : son
    # résultat reste une simple proposition à valider.
    _store_proposal(proposal)
    with _lock:
        if term.terme in _state["en_verification"]:
            _state["en_verification"].remove(term.terme)
        if term.terme in _state["en_file"]:
            _state["en_file"].remove(term.terme)
        _state["faits"] += 1
        if not _state["en_file"]:
            _release_executor()


def start(termes: list[str] | None = None) -> dict:
    """Ajoute des termes à la file de vérification.

    ``None`` sélectionne toutes les entrées non vérifiées. Les termes déjà
    en file sont ignorés ; une vérification en cours n'est pas un obstacle.
    """
    global _executor
    settings = config.load_settings()
    with _lock:
        requested = set(termes) if termes is not None else None
        queued = set(_state["en_file"])
        terms = [
            term for term in load_lexicon()
            if not term.verifie and term.terme not in queued
            and (requested is None or term.terme in requested)
        ]
        if terms:
            if _executor is None:
                _cancel.clear()
                _state.update({"faits": 0, "total": 0, "terme_courant": None,
                               "en_file": [], "en_verification": []})
                workers = max(1, int(settings.factcheck_workers))
                _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lexicon-verify")
            _state["en_cours"] = True
            _state["total"] += len(terms)
            for term in terms:
                _state["en_file"].append(term.terme)
                _executor.submit(_verify_one, term, settings, _generation)
        return _snapshot()


def _snapshot() -> dict:
    snapshot = dict(_state)
    snapshot["en_file"] = list(_state["en_file"])
    snapshot["en_verification"] = list(_state["en_verification"])
    return snapshot


def status() -> dict:
    with _lock:
        snapshot = _snapshot()
        snapshot["propositions"] = _load_proposals()
        return snapshot


def cancel() -> None:
    """Retire de la file les termes pas encore commencés."""
    global _generation
    with _lock:
        _cancel.set()
        _generation += 1
        running = list(_state["en_verification"])
        _state["en_file"] = running
        _state["total"] = _state["faits"] + len(running)
        if not running:
            _release_executor()
