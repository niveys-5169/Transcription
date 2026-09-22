import json

import pytest

from tools.evaluate_guardrails import load_jsonl, score_binary


def _result(expected, actual, *, tags=None, reasons=None):
    return {
        "id": f"{expected}-{actual}",
        "expected": expected,
        "actual": actual,
        "tags": tags or [],
        "reasons": reasons or [],
    }


def test_score_les_quatre_resultats_binaires():
    report = score_binary([
        _result("accept", "accept"),
        _result("reject", "reject"),
        _result("reject", "accept"),
        _result("accept", "reject"),
    ])

    assert report["true_accept"] == 1
    assert report["true_reject"] == 1
    assert report["false_accept"] == 1
    assert report["false_reject"] == 1


def test_taux_utilisent_les_populations_pertinentes():
    report = score_binary([
        _result("reject", "reject"),
        _result("reject", "reject"),
        _result("reject", "accept"),
        _result("accept", "accept"),
        _result("accept", "reject"),
    ])

    assert report["false_accept_rate"] == pytest.approx(1 / 3)
    assert report["false_reject_rate"] == pytest.approx(1 / 2)


def test_lecture_jsonl(tmp_path):
    path = tmp_path / "corpus.jsonl"
    rows = [
        {"id": "one", "expected": "accept"},
        {"id": "two", "expected": "reject"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    assert load_jsonl(path, {"id", "expected"}) == rows
