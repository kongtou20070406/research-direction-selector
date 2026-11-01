import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rds_offline_contract_values import digest
from rds_world_model_contract import assess_replay, history_digest, validate_candidate


def packet():
    history = [{"id": "run-1", "before": {"cost": 0}, "action": {"test": "a"},
                "after": {"cost": 1}, "receipt_sha256": "a" * 64}]
    candidate = {"schema": "rds-world-model-candidate-v1",
                 "model": {"id": "frozen", "version": "1", "sha256": "b" * 64},
                 "revision": 1, "parent_sha256": None,
                 "rulebook": {"sha256": "c" * 64, "format": "markdown"},
                 "executable": {"sha256": "d" * 64, "format": "python"},
                 "history_sha256": history_digest(history)}
    return candidate, history


def assess(candidate, history, predictions=None, verdict="PASS", **kw):
    return assess_replay(candidate, history,
                         predictions if predictions is not None else [{"id": "run-1", "after": {"cost": 1}}],
                         semantic_review={"candidate_sha256": digest(candidate), "verdict": verdict},
                         current_parent_sha256=kw.get("parent"),
                         receipt_identities=kw.get("receipts", {"run-1": "a" * 64}))


def test_exact_replay_retains_unknown_and_detached_values():
    candidate, history = packet()
    original = copy.deepcopy(candidate)
    result = assess(candidate, history)
    assert result["status"] == "ACCEPTED"
    assert result["scope"] == "SUPPLIED_HISTORY_ONLY"
    assert result["selection_effect"] == "NONE" and result["evidence_status"] == "UNKNOWN"
    assert candidate == original


def test_semantic_gate_cannot_be_skipped(verdict):
    candidate, history = packet()
    assert assess(candidate, history, verdict=verdict)["status"] == "REJECTED"


def test_original_counterexample_is_retained():
    candidate, history = packet()
    result = assess(candidate, history, [{"id": "run-1", "after": {"cost": 2}}])
    assert result["status"] == "REJECTED" and result["mismatches"] == ["run-1"]


def test_tamper_stale_and_incomplete_replay_fail_closed(change):
    candidate, history = packet()
    kw, predictions = {}, None
    if change == "history":
        history[0]["after"]["cost"] = 2
    elif change == "receipt":
        kw["receipts"] = {"run-1": "e" * 64}
    elif change == "parent":
        kw["parent"] = "e" * 64
    else:
        predictions = []
    with pytest.raises(ValueError):
        assess(candidate, history, predictions, **kw)


def test_boolean_revision_and_invented_authority_rejected():
    candidate, _ = packet()
    with pytest.raises(ValueError):
        validate_candidate({**candidate, "revision": True})
    with pytest.raises(ValueError):
        validate_candidate({**candidate, "verified": True})

test_semantic_gate_cannot_be_skipped = pytest.mark.parametrize("verdict", ["FAIL", "UNKNOWN"])(test_semantic_gate_cannot_be_skipped)
test_tamper_stale_and_incomplete_replay_fail_closed = pytest.mark.parametrize("change", ["history", "receipt", "parent", "denominator"])(test_tamper_stale_and_incomplete_replay_fail_closed)
