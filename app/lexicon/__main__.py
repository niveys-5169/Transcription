"""Ligne de commande du lexique MJPM.

    python -m app.lexicon verify [--dry-run]

Vérifie par recherche web chaque entrée non vérifiée du lexique livré
(``mjpm.json``) et écrit le résultat en place. C'est une commande de
mainteneur, à lancer avant de committer une modification du lexique — le
lexique lui-même est livré non vérifié (voir ``app/lexicon/__init__.py``) et
ne devient une source d'autorité qu'après ce passage. Jamais lancée au
runtime de l'application.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .. import config
from ..proofread import factcheck as factcheck_module
from ..proofread.base import ProofreadError
from . import LEXICON_PATH, _from_dict
from .verification import question_for, verify_term


def _load_raw() -> list[dict]:
    return json.loads(LEXICON_PATH.read_text(encoding="utf-8"))


def _save_raw(entries: list[dict]) -> None:
    LEXICON_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def verify_lexicon(*, dry_run: bool = False) -> int:
    """Vérifie chaque entrée non vérifiée. Renvoie le nombre nouvellement vérifiées."""
    settings = config.load_settings()

    # Référencé via le module (pas un ``from ... import get_backend``) pour
    # que ce garde-fou et ``verify_claim`` ci-dessous regardent toujours le
    # même back-end — y compris quand un test le remplace après coup.
    available, detail = factcheck_module.get_backend(settings).is_available()
    if not available:
        print(f"Back-end Claude indisponible : {detail}", file=sys.stderr)
        return 0

    entries = _load_raw()
    verified_count = 0

    for raw in entries:
        if raw.get("verifie"):
            continue
        term = _from_dict(raw)
        if term is None:
            continue

        try:
            verdict = verify_term(term, settings=settings)
        except ProofreadError as exc:
            print(f"[erreur] {term.terme} : {exc}", file=sys.stderr)
            continue

        if verdict.verdict == "confirme" and verdict.confiance == "haute" and verdict.sources:
            print(f"[vérifié] {term.terme} — confirmé, {len(verdict.sources)} source(s).")
            if not dry_run:
                raw["verifie"] = True
                raw["verifie_le"] = date.today().isoformat()
                raw["sources"] = [
                    {"titre": s.titre, "url": s.url} for s in verdict.sources
                ]
            verified_count += 1
        else:
            print(
                f"[à revoir] {term.terme} — {verdict.verdict} ({verdict.confiance}) : "
                f"{verdict.explication or 'aucune explication'}",
                file=sys.stderr,
            )

    if not dry_run and verified_count:
        _save_raw(entries)
    return verified_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.lexicon")
    sub = parser.add_subparsers(dest="command", required=True)
    verify_parser = sub.add_parser(
        "verify", help="Vérifie par recherche web les entrées non vérifiées."
    )
    verify_parser.add_argument(
        "--dry-run", action="store_true",
        help="N'écrit rien dans mjpm.json, affiche seulement le résultat.",
    )
    args = parser.parse_args(argv)

    if args.command == "verify":
        count = verify_lexicon(dry_run=args.dry_run)
        print(f"\n{count} entrée(s) nouvellement vérifiée(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
