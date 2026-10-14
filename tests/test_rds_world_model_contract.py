import copy
from pathlib import Path
import sys

import unittest

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


class WorldModelContractTests(unittest.TestCase):
    def test_exact_replay_retains_unknown_and_detached_values(self):
        candidate, history = packet()
        original = copy.deepcopy(candidate)
        result = assess(candidate, history)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["scope"], "SUPPLIED_HISTORY_ONLY")
        self.assertEqual(result["selection_effect"], "NONE")
        self.assertEqual(result["evidence_status"], "UNKNOWN")
        self.assertEqual(candidate, original)

    def test_failed_semantic_review_rejects(self):
        candidate, history = packet()
        self.assertEqual(assess(candidate, history, verdict="FAIL")["status"], "REJECTED")

    def test_unknown_semantic_review_rejects(self):
        candidate, history = packet()
        self.assertEqual(assess(candidate, history, verdict="UNKNOWN")["status"], "REJECTED")

    def test_original_counterexample_is_retained(self):
        candidate, history = packet()
        result = assess(candidate, history, [{"id": "run-1", "after": {"cost": 2}}])
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["mismatches"], ["run-1"])

    def test_changed_history_rejects(self):
        candidate, history = packet()
        history[0]["after"]["cost"] = 2
        with self.assertRaises(ValueError):
            assess(candidate, history)

    def test_changed_original_receipt_rejects(self):
        candidate, history = packet()
        with self.assertRaises(ValueError):
            assess(candidate, history, receipts={"run-1": "e" * 64})

    def test_stale_parent_rejects(self):
        candidate, history = packet()
        with self.assertRaises(ValueError):
            assess(candidate, history, parent="e" * 64)

    def test_incomplete_replay_rejects(self):
        candidate, history = packet()
        with self.assertRaises(ValueError):
            assess(candidate, history, [])

    def test_boolean_revision_and_invented_authority_rejected(self):
        candidate, _ = packet()
        with self.assertRaises(ValueError):
            validate_candidate({**candidate, "revision": True})
        with self.assertRaises(ValueError):
            validate_candidate({**candidate, "verified": True})
