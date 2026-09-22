"""Vérification web du lexique, avec propositions persistantes à valider."""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from .. import config
from ..proofread.factcheck import Claim, Verdict, verify_claim as _verify_claim
from . import Term, load_lexicon

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_cancel = threading.Event()
_state: dict = {"en_cours": False, "faits": 0, "total": 0, "terme_courant": None}


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


def _run(terms: list[Term], settings) -> None:
    try:
        workers = max(1, min(int(settings.factcheck_workers), len(terms)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lexicon-verify") as pool:
            futures = {pool.submit(verify_term, term, settings=settings): term for term in terms}
            for future in as_completed(futures):
                term = futures[future]
                if _cancel.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                with _lock:
                    _state["terme_courant"] = term.terme
                try:
                    verdict = future.result()
                    _store_proposal(_proposal(term, verdict))
                except Exception as exc:
                    logger.exception("Échec de vérification du terme %s", term.terme)
                    _store_proposal({
                        "terme": term.terme, "verdict": "erreur", "forme_correcte": "",
                        "explication": str(exc), "sources": [], "confiance": "basse",
                        "origine": "erreur", "status": "attente",
                    })
                with _lock:
                    _state["faits"] += 1
    finally:
        with _lock:
            _state["en_cours"] = False
            _state["terme_courant"] = None


def start(termes: list[str] | None = None) -> dict:
    """Lance la vérification. ``None`` sélectionne toutes les non vérifiées."""
    with _lock:
        if _state["en_cours"]:
            return {**_state, "started": False}
        requested = set(termes) if termes is not None else None
        terms = [
            term for term in load_lexicon()
            if not term.verifie and (requested is None or term.terme in requested)
        ]
        _cancel.clear()
        _state.update({"en_cours": bool(terms), "faits": 0, "total": len(terms), "terme_courant": None})
        snapshot = {**_state, "started": True}
    if terms:
        settings = config.load_settings()
        threading.Thread(target=_run, args=(terms, settings), name="lexicon-verification", daemon=True).start()
    return snapshot


def status() -> dict:
    with _lock:
        snapshot = dict(_state)
        snapshot["propositions"] = _load_proposals()
        return snapshot


def cancel() -> None:
    _cancel.set()
