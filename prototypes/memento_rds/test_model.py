import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("reflective_model", Path(__file__).with_name("model.py"))
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def rules(expr="state['remaining'] - action['cost']"):
    return {"rules": [{"action": "execute", "text": "Execution consumes its measured cost.",
                       "updates": {"remaining": expr}}], "unknown": ["unseen task success"]}


def history():
    return [{"id": "attempt-1", "before": {"remaining": 10},
             "action": {"kind": "execute", "cost": 2}, "after": {"remaining": 8}}]


def review(book):
    return {"artifact_sha256": model.digest(model.replay(book, history())["artifact"]), "verdict": "PASS"}


def test_counterexample_revision_preserves_previous_pair_and_input():
    wrong = rules("state['remaining'] + action['cost']")
    original = copy.deepcopy(wrong)
    rejected = model.revision(None, wrong, history(), semantic_review=review(wrong))
    assert rejected["decision"] == "REJECT" and rejected["accepted"] is None
    assert rejected["candidate"]["counterexamples"][0]["observed"] == {"remaining": 8}
    assert wrong == original
    repaired = model.revision(None, rules(), history(), semantic_review=review(rules()))
    assert repaired["decision"] == "KEEP" and repaired["evidence_status"] == "UNKNOWN"
    old = repaired["accepted"]
    failed = model.revision(old, wrong, history(), semantic_review=review(wrong))
    assert failed["accepted"] == old and failed["parent_sha256"] == model.digest(old)


def test_unseen_prediction_is_separate_from_replay():
    predict, _ = model.compile_rulebook(rules())
    assert predict({"remaining": 25}, {"kind": "execute", "cost": 7}) == {"remaining": 18}
    with pytest.raises(ValueError, match="UNKNOWN"):
        predict({"remaining": 25}, {"kind": "unknown", "cost": 7})


def test_unknown_semantic_review_rejects_exact_replay():
    book = rules()
    result = model.revision(None, book, history(), semantic_review={**review(book), "verdict": "UNKNOWN"})
    assert result["candidate"]["replay"] == "PASS" and result["decision"] == "REJECT"
    with pytest.raises(ValueError, match="bind"):
        model.revision(None, book, history(), semantic_review={"artifact_sha256": "bad", "verdict": "PASS"})


def test_calls_attributes_loops_nonfinite_and_huge_expressions_rejected():
    for expr in ("__import__('os')", "state.__class__", "[x for x in state]", "1e309", "2 ** 1000", "'x' * 1000000"):
        with pytest.raises((ValueError, SyntaxError)):
            predict, _ = model.compile_rulebook(rules(expr))
            predict({"remaining": 2}, {"kind": "execute", "cost": 1})
    predict, _ = model.compile_rulebook(rules())
    with pytest.raises(ValueError, match="numeric"):
        predict({"remaining": "x"}, {"kind": "execute", "cost": 1000000})
