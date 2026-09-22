"""Mesure locale des garde-fous de relecture et de décision ASR."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.proofread.asr_retry import choose_asr_candidate  # noqa: E402
from app.proofread.validation import validate_proofread_candidate  # noqa: E402

PROOFREAD_CORPUS = REPO_ROOT / "tests" / "corpus" / "proofread_guardrails.jsonl"
ASR_CORPUS = REPO_ROOT / "tests" / "corpus" / "asr_retry.jsonl"
PROOFREAD_REQUIRED = {"id", "raw", "candidate", "expected", "tags", "source"}
ASR_REQUIRED = {"id", "original", "retry", "issue", "expected", "tags"}


def load_jsonl(path: Path, required_fields: set[str] | None = None) -> list[dict]:
    """Charge un JSONL et signale précisément les lignes invalides."""
    cases = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: JSON invalide: {exc.msg}") from exc
            missing = (required_fields or set()) - set(case)
            if missing:
                raise ValueError(f"{path}:{line_number}: champs manquants: {', '.join(sorted(missing))}")
            cases.append(case)
    return cases


def score_binary(results: list[dict]) -> dict:
    """Calcule la matrice accept/reject et les ventilations du corpus."""
    counts = Counter()
    tags: dict[str, Counter] = {}
    reasons = Counter()
    divergences = []
    for result in results:
        expected, actual = result["expected"], result["actual"]
        if expected == "accept" and actual == "accept":
            outcome = "true_accept"
        elif expected == "reject" and actual == "reject":
            outcome = "true_reject"
        elif expected == "reject" and actual == "accept":
            outcome = "false_accept"
        else:
            outcome = "false_reject"
        counts[outcome] += 1
        correct = expected == actual
        for tag in result.get("tags", []):
            tags.setdefault(tag, Counter())["total"] += 1
            tags[tag]["correct"] += int(correct)
        if actual == "reject":
            reasons.update(result.get("reasons", []))
        if not correct:
            divergences.append({**result, "outcome": outcome})

    expected_reject = counts["true_reject"] + counts["false_accept"]
    expected_accept = counts["true_accept"] + counts["false_reject"]
    total = len(results)
    return {
        "total": total,
        "true_accept": counts["true_accept"],
        "true_reject": counts["true_reject"],
        "false_accept": counts["false_accept"],
        "false_reject": counts["false_reject"],
        "accuracy": (counts["true_accept"] + counts["true_reject"]) / total if total else 0.0,
        "false_accept_rate": counts["false_accept"] / expected_reject if expected_reject else 0.0,
        "false_reject_rate": counts["false_reject"] / expected_accept if expected_accept else 0.0,
        "tags": {tag: dict(value) for tag, value in sorted(tags.items())},
        "rejection_reasons": dict(reasons.most_common()),
        "divergences": divergences,
    }


def evaluate_proofread(cases: list[dict], *, prefix_options: dict | None = None) -> dict:
    results = []
    for case in cases:
        verdict = validate_proofread_candidate(
            case["raw"], case["candidate"], prefix_options=prefix_options,
        )
        results.append({
            "id": case["id"], "expected": case["expected"],
            "actual": "accept" if verdict.valid else "reject",
            "tags": case["tags"], "reasons": verdict.reasons,
        })
    return score_binary(results)


def evaluate_asr(cases: list[dict]) -> dict:
    results = []
    tags: dict[str, Counter] = {}
    reasons = Counter()
    for case in cases:
        decision = choose_asr_candidate(case["original"], case["retry"], case["issue"])
        correct = decision.action == case["expected"]
        result = {
            "id": case["id"], "expected": case["expected"], "actual": decision.action,
            "tags": case["tags"], "reasons": decision.reasons,
        }
        results.append(result)
        reasons.update(decision.reasons)
        for tag in case["tags"]:
            tags.setdefault(tag, Counter())["total"] += 1
            tags[tag]["correct"] += int(correct)
    return {
        "total": len(results),
        "correct": sum(item["expected"] == item["actual"] for item in results),
        "incorrect": sum(item["expected"] != item["actual"] for item in results),
        "tags": {tag: dict(value) for tag, value in sorted(tags.items())},
        "decision_reasons": dict(reasons.most_common()),
        "divergences": [item for item in results if item["expected"] != item["actual"]],
    }


def _print_tags(tags: dict[str, dict]) -> None:
    for tag, values in tags.items():
        print(f"{tag:<20} {values['correct']}/{values['total']} correct")


def print_proofread(report: dict) -> None:
    for item in report["divergences"]:
        print(f"{item['outcome'].replace('_', ' ').upper()}  {item['id']}")
        print(f"expected={item['expected']}")
        print(f"actual={item['actual']}")
        print(f"tags={','.join(item['tags'])}")
        if item["reasons"]:
            print(f"reasons={','.join(item['reasons'])}")
        print()
    print(f"TOTAL          {report['total']}")
    print(f"TRUE ACCEPT    {report['true_accept']}")
    print(f"TRUE REJECT    {report['true_reject']}")
    print(f"FALSE ACCEPT   {report['false_accept']}")
    print(f"FALSE REJECT   {report['false_reject']}")
    print(f"ACCURACY          {report['accuracy']:.2%}")
    print(f"FALSE ACCEPT RATE {report['false_accept_rate']:.2%}")
    print(f"FALSE REJECT RATE {report['false_reject_rate']:.2%}")
    print("\nPAR TAG")
    _print_tags(report["tags"])
    print("\nRAISONS DE REJET")
    for reason, count in report["rejection_reasons"].items():
        print(f"{reason:<24} {count}")


def print_asr(report: dict) -> None:
    for item in report["divergences"]:
        print(f"ASR MISMATCH  {item['id']}")
        print(f"expected={item['expected']}")
        print(f"actual={item['actual']}")
        print(f"tags={','.join(item['tags'])}")
        print(f"reasons={','.join(item['reasons'])}\n")
    print(f"TOTAL       {report['total']}")
    print(f"CORRECT     {report['correct']}")
    print(f"INCORRECT   {report['incorrect']}")
    print("\nPAR TAG")
    _print_tags(report["tags"])


def print_prefix_sweep(cases: list[dict]) -> None:
    print("WINDOW SWEEP")
    print("window  floor  false_accept  false_reject")
    for window in (4, 6, 8, 10, 12):
        for floor in (0.50, 0.60, 0.70, 0.80, 0.90):
            report = evaluate_proofread(cases, prefix_options={
                "window_words": window, "support_floor": floor,
            })
            print(f"{window:<7} {floor:<6.2f} {report['false_accept']:<13} {report['false_reject']}")
    print("\nOPENING LINE SWEEP")
    print("floor  false_accept  false_reject")
    for floor in (0.40, 0.50, 0.60, 0.70):
        report = evaluate_proofread(cases, prefix_options={
            "opening_line_support_floor": floor,
        })
        print(f"{floor:<6.2f} {report['false_accept']:<13} {report['false_reject']}")


def _compact(report: dict) -> dict:
    scalar_keys = (
        "total", "true_accept", "true_reject", "false_accept", "false_reject",
        "accuracy", "false_accept_rate", "false_reject_rate", "correct", "incorrect",
    )
    return {key: report[key] for key in scalar_keys if key in report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--proofread", action="store_true", help="évalue seulement la relecture")
    selection.add_argument("--asr", action="store_true", help="évalue seulement les décisions ASR")
    selection.add_argument("--all", action="store_true", help="évalue les deux corpus (défaut)")
    parser.add_argument("--json", action="store_true", help="produit un rapport JSON compact")
    parser.add_argument("--sweep-prefix", action="store_true", help="compare les seuils d'alignement")
    args = parser.parse_args(argv)

    run_proofread = args.proofread or args.all or not args.asr
    run_asr = args.asr or args.all or (not args.proofread and not args.asr)
    proofread_cases = load_jsonl(PROOFREAD_CORPUS, PROOFREAD_REQUIRED) if run_proofread else []
    asr_cases = load_jsonl(ASR_CORPUS, ASR_REQUIRED) if run_asr else []

    if args.sweep_prefix:
        if not run_proofread:
            parser.error("--sweep-prefix exige l'évaluation proofread")
        print_prefix_sweep(proofread_cases)
        return 0

    reports = {}
    if run_proofread:
        reports["proofread"] = evaluate_proofread(proofread_cases)
    if run_asr:
        reports["asr"] = evaluate_asr(asr_cases)

    if args.json:
        payload = {name: _compact(report) for name, report in reports.items()}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        if "proofread" in reports:
            print("PROOFREAD")
            print_proofread(reports["proofread"])
        if "asr" in reports:
            if "proofread" in reports:
                print("\nASR RETRY")
            print_asr(reports["asr"])

    proofread_ok = reports.get("proofread", {}).get("false_accept", 0) == 0 and reports.get("proofread", {}).get("false_reject", 0) == 0
    asr_ok = reports.get("asr", {}).get("incorrect", 0) == 0
    return 0 if proofread_ok and asr_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
