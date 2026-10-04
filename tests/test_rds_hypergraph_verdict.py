"""Operator-verdict bridge (Issue #49): witness refutation, archived source, blocker recalc."""
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
from copy import deepcopy
from contextlib import closing

ROOT = str(Path(__file__).resolve().parents[1] / "scripts")
sys.path.insert(0, ROOT)
from rds_hypergraph import analyze_hypergraph, apply_operator_verdict, review_hypergraph, ASSURANCE
from rds_operators import BoundedFiniteModelOperator
from tests.test_hypergraph_evidence import ledger, receipt_row, RECEIPT_SHA


def two_route_map():
    """a and b independently support goal g through two supported rules."""
    value = {"claims": {"a": {"state": "supported", "source": "obs-a.json"},
                        "b": {"state": "supported", "source": "obs-b.json"}},
             "rules": [{"from": "a", "to": "g", "state": "supported", "source": "rule-a.json"},
                       {"from": "b", "to": "g", "state": "supported", "source": "rule-b.json"}],
             "goal": "g"}
    return review_hypergraph(value)["dependency_map"]


def verdict(status="CONTRADICTED", operator="bounded_finite_model", witness=None, **extra):
    receipt = {"status": status, "operator": operator, "input_sha256": "c" * 64}
    if witness is None:
        witness = {"counterexample": [1, 2, 3]}
    if witness is not ...:
        receipt["witness"] = witness
    receipt.update(extra)
    return receipt


class VerdictBridgeTests(unittest.TestCase):
    def test_contradicted_witness_refutes_target_and_keeps_independent_alternative(self):
        first = review_hypergraph(two_route_map())
        self.assertEqual(first["declared_supported_closure"], ["a", "b", "g"])
        result = apply_operator_verdict(two_route_map(), "node:a", verdict())
        self.assertEqual(result["declared_supported_closure"], ["b", "g"])
        self.assertEqual(result["goals"]["g"]["status"], "DECLARED_SUPPORTED")
        self.assertEqual(result["operator_verdict"]["status"], "CONTRADICTED")
        self.assertEqual(result["operator_verdict"]["effect"], "REFUTED")
        self.assertEqual(result["operator_verdict"]["lost_support"], ["a"])
        self.assertEqual(result["operator_verdict"]["witness"], {"counterexample": [1, 2, 3]})
        self.assertEqual(result["operator_verdict"]["operator"], "bounded_finite_model")
        node = next(n for n in result["dependency_map"]["nodes"] if n["id"] == "a")
        self.assertEqual(node["status"], "CONTRADICTED")

    def test_witness_and_complete_previous_source_are_archived(self):
        m = two_route_map()
        previous = {"locator": "obs-a.json", "file": "obs-a.json", "sha256": "b" * 64,
                    "scope": {"domain": [0, 1], "version": 2}}
        m["nodes"][0]["source"] = previous
        original = deepcopy(m)
        result = apply_operator_verdict(m, "node:a", verdict(witness={"divergence_step": 16}, input_sha256="A" * 64))
        node = next(n for n in result["dependency_map"]["nodes"] if n["id"] == "a")
        change = next(c for c in result["revision"]["changes"] if c["id"] == "a")
        self.assertEqual(node["source"], previous)
        self.assertEqual(change["previous_source"], previous)
        self.assertEqual(change["source"]["operator"], "bounded_finite_model")
        self.assertEqual(change["source"]["witness"], {"divergence_step": 16})
        self.assertEqual(change["source"]["input_sha256"], "a" * 64)
        self.assertEqual(change["source"]["verdict_status"], "CONTRADICTED")
        self.assertEqual(m, original)

    def test_blocker_sets_are_recomputed_after_refutation(self):
        # Refuting one rule leaves the proposed alternative as the recalculated blocker set.
        value2 = {"nodes": [{"id": "a", "status": "SUPPORTED", "source": "obs-a.json"},
                            {"id": "g", "status": "UNKNOWN", "source": "obs-g.json"}],
                  "hyperedges": [{"id": "e1", "premises": ["a"], "conclusion": "g",
                                  "status": "SUPPORTED", "source": "r1.json"},
                                 {"id": "e2", "premises": ["a"], "conclusion": "g",
                                  "status": "PROPOSED", "source": "r2.json"}],
                  "goals": ["g"]}
        m = review_hypergraph(value2)["dependency_map"]
        rule_id = m["hyperedges"][0]["id"]
        result = apply_operator_verdict(m, "rule:" + rule_id, verdict())
        self.assertEqual(result["declared_supported_closure"], ["a"])
        # e2 is a remaining viable route: the recalculated minimal blocker set
        # names the rule proof obligation that would complete it.
        self.assertEqual(result["goals"]["g"]["minimal_missing_evidence_sets"], [["rule:e2"]])
        self.assertTrue(result["goals"]["g"]["blocker_sets_complete"])
        self.assertEqual(result["operator_verdict"]["minimal_missing_evidence_sets"]["g"], [["rule:e2"]])
        # Matches a fresh analysis of the revised map (no stale closure).
        fresh = analyze_hypergraph(result["dependency_map"])
        self.assertEqual(result["declared_supported_closure"], fresh["declared_supported_closure"])
        self.assertEqual(result["goals"], fresh["goals"])

    def test_rule_target_refutation_uses_rule_token(self):
        # The refuted rule-a route dies; g survives through rule-b (independent alternative).
        m = two_route_map()
        rule_id = m["hyperedges"][0]["id"]
        result = apply_operator_verdict(m, "rule:" + rule_id, verdict(operator="lean_axiom_review",
                                                                     witness="axiom violation"))
        self.assertEqual(result["declared_supported_closure"], ["a", "b", "g"])
        self.assertEqual(result["revision"]["changes"][0]["id"], rule_id)
        self.assertEqual(result["revision"]["changes"][0]["previous_status"], "SUPPORTED")
        edge = next(e for e in result["dependency_map"]["hyperedges"] if e["id"] == rule_id)
        self.assertEqual(edge["status"], "CONTRADICTED")
        self.assertEqual(edge["source"], "rule-a.json")
        self.assertEqual(result["revision"]["changes"][0]["source"]["operator"], "lean_axiom_review")

    def test_bare_pass_and_unknown_never_claim_support(self):
        m = two_route_map()
        original = json.dumps(m, sort_keys=True)
        for status in ("PASS", "UNKNOWN", "ERROR"):
            result = apply_operator_verdict(m, "node:a", verdict(status=status, witness=...))
            self.assertEqual(result["operator_verdict"]["effect"], "UNCHANGED")
            self.assertEqual(result["declared_supported_closure"], ["a", "b", "g"])
        # Input map was never mutated, even by the PASS call.
        self.assertEqual(original, json.dumps(m, sort_keys=True))
        # PASS does not add support by itself: an UNKNOWN leaf keeps its status.
        value = {"claims": {"a": {"state": "unknown", "source": "obs-a.json"}},
                 "rules": [{"from": "a", "to": "g", "state": "supported", "source": "r.json"}],
                 "goal": "g"}
        m2 = review_hypergraph(value)["dependency_map"]
        result = apply_operator_verdict(m2, "node:a", verdict(status="PASS", witness=...))
        self.assertEqual(result["goals"]["g"]["status"], "UNKNOWN")
        self.assertEqual(result["declared_supported_closure"], [])

    def test_refusals_are_typed_and_leave_no_writes(self):
        m = two_route_map()
        original = json.dumps(m, sort_keys=True)
        cases = [
            ("node:missing", verdict(), "unknown node target"),
            ("cluster:b", verdict(), "node:<id>"),
            ("node:a", "PASS", "operator receipt must be an object"),
            ("node:a", verdict(status="CONTRADICTED", witness=...), "requires a witness value"),
            ("node:a", verdict(status="CONTRADICTED", witness=1, input_sha256="short"), "64 hexadecimal"),
            ("node:a", verdict(status="MAYBE"), "PASS, CONTRADICTED, UNKNOWN or ERROR"),
            ("node:a", verdict(witness={"x": 1}, operator=" "), "must name the operator"),
        ]
        for token, receipt, message in cases:
            with self.subTest(token=token, receipt=receipt):
                with self.assertRaises(ValueError) as caught:
                    apply_operator_verdict(m, token, receipt)
                self.assertIn(message, str(caught.exception))
        self.assertEqual(original, json.dumps(m, sort_keys=True))
        # A malformed dependency map is refused before any verdict handling.
        with self.assertRaises(ValueError):
            apply_operator_verdict({"nodes": "no"}, "node:a", verdict())

    def test_sequenced_refutations_follow_existing_cascade_semantics(self):
        m = two_route_map()
        first = apply_operator_verdict(m, "node:a", verdict())
        self.assertEqual(first["declared_supported_closure"], ["b", "g"])
        second = apply_operator_verdict(first["dependency_map"], "node:b", verdict())
        self.assertEqual(second["declared_supported_closure"], [])
        self.assertEqual(second["goals"]["g"]["status"], "UNRESOLVED")
        self.assertEqual(second["operator_verdict"]["lost_support"], ["b", "g"])
        # Re-analysis of the immutable first snapshot still reproduces the first result.
        self.assertEqual(review_hypergraph(first["dependency_map"])["declared_supported_closure"], ["b", "g"])

    def test_real_finite_operator_counterexample_and_predicate_error(self):
        operator = BoundedFiniteModelOperator()
        actual = operator.search_counterexample([0, 1], lambda x: x == 0)
        self.assertEqual(actual["status"], "FAIL")
        result = apply_operator_verdict(two_route_map(), "node:a",
                                        {**actual, "operator": "bounded_finite_model", "input_sha256": "d" * 64})
        self.assertEqual(result["operator_verdict"]["effect"], "REFUTED")
        self.assertEqual(result["operator_verdict"]["witness"], 1)
        self.assertEqual(result["operator_verdict"]["received_status"], "FAIL")
        error = operator.search_counterexample([0], lambda x: 1 / x)
        for unproven in (error, {**actual, "assurance": "UNKNOWN"}, {**actual, "operator": "other"}):
            receipt = {"operator": "bounded_finite_model", **unproven, "input_sha256": "d" * 64}
            result = apply_operator_verdict(two_route_map(), "node:a", receipt)
            self.assertEqual(result["operator_verdict"]["effect"], "UNCHANGED")
            self.assertEqual(result["declared_supported_closure"], ["a", "b", "g"])
        bare = operator.search_counterexample([0], lambda x: True)
        self.assertEqual(apply_operator_verdict(two_route_map(), "node:a", bare)["operator_verdict"]["effect"], "UNCHANGED")

    def test_refutations_require_input_digest_and_finite_bounded_witness(self):
        m = two_route_map()
        original = deepcopy(m)
        missing = verdict(); missing.pop("input_sha256")
        cyclic = {}; cyclic["self"] = cyclic
        for receipt in (missing, verdict(witness=float("nan")), verdict(witness=float("inf")),
                        verdict(witness={"bad": float("nan")}), verdict(witness=cyclic),
                        verdict(witness="x" * (8 * 1024 * 1024)), verdict(witness={"bytes": b"x"})):
            with self.assertRaises(ValueError):
                apply_operator_verdict(m, "node:a", receipt)
            self.assertEqual(m, original)
        # False is a genuine finite-domain counterexample, not a missing witness.
        result = apply_operator_verdict(m, "node:a", verdict(witness=False))
        self.assertIs(result["operator_verdict"]["witness"], False)

    def test_grounded_pass_updates_nodes_and_rules_with_one_receipt_lookup(self):
        import rds_hypergraph
        for kind in ("node", "rule"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = ledger(Path(tmp))
                binding = {"receipt": {"project_root": str(root), "sha256": RECEIPT_SHA}}
                m = {"nodes": [{"id": "a", "status": "UNKNOWN", "source": "a.json"},
                               {"id": "g", "status": "UNKNOWN", "source": "g.json"}],
                     "hyperedges": [{"id": "e", "premises": ["a"], "conclusion": "g",
                                     "status": "SUPPORTED", "source": "e.json", "evidence": binding}],
                     "goals": ["g"]}
                if kind == "rule":
                    m["nodes"][0].update(status="SUPPORTED", evidence=binding)
                    m["hyperedges"][0]["status"] = "PROPOSED"
                original = deepcopy(m)
                with patch("rds_hypergraph.read_project_receipt", wraps=rds_hypergraph.read_project_receipt) as read:
                    result = apply_operator_verdict(m, "node:a" if kind == "node" else "rule:e",
                                                    {"status": "PASS", "evidence": binding})
                self.assertEqual(read.call_count, 1)
                self.assertEqual(result["declared_supported_closure"], ["a", "g"])
                self.assertEqual(result["operator_verdict"]["effect"], "SUPPORTED")
                self.assertEqual(result["operator_verdict"]["receipt_audit"]["status"], "GROUNDED")
                self.assertIn("g", result["revision"]["gained_support"])
                self.assertEqual(result["assurance"], ASSURANCE)
                self.assertEqual(result["authorization"], "UNCHANGED")
                self.assertEqual(m, original)

    def test_witness_storage_refuses_key_coercion_and_preserves_json_identity(self):
        from rds_hypergraph_input import load_input
        m = two_route_map()
        original = deepcopy(m)
        for witness in ({1: "numeric"}, {1: "numeric", "1": "text"},
                        {"nested": [{False: "boolean"}]}, {"tuple": (1, 2)}):
            with self.subTest(witness=witness), self.assertRaises(ValueError):
                apply_operator_verdict(m, "node:a", verdict(witness=witness))
            self.assertEqual(m, original)
        witness = {"1": "text", "nested": [False, None, 1, 1.25, {"step": "counterexample"}]}
        result = apply_operator_verdict(m, "node:a", verdict(witness=witness))
        reloaded, repairs = load_input(json.dumps(result, allow_nan=False))
        self.assertEqual(repairs, [])
        self.assertEqual(reloaded["revision"]["changes"][0]["source"]["witness"], witness)
        self.assertEqual(reloaded["operator_verdict"]["witness"], witness)
        self.assertEqual(m, original)

    def test_missing_failed_corrupt_and_cross_root_receipts_do_not_support(self):
        import sqlite3
        for case in ("missing", "failed", "corrupt", "cross-root"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                status = "FAILED" if case == "failed" else "SUCCEEDED"
                root = ledger(Path(tmp), status)
                sha = receipt_row(status)[0]
                if case == "missing":
                    sha = "e" * 64
                if case == "corrupt":
                    with closing(sqlite3.connect(root / ".rds/project.sqlite3")) as db:
                        db.execute("UPDATE receipts SET body=?", ('{"sha256":"' + sha + '","run_status":"SUCCEEDED"}',))
                        db.commit()
                if case == "cross-root":
                    root = Path(tmp) / "another-project"
                m = {"nodes": [{"id": "a", "status": "UNKNOWN", "source": "a.json"}],
                     "hyperedges": [], "goals": ["a"]}
                result = apply_operator_verdict(m, "node:a", {"status": "PASS", "evidence": {
                    "receipt": {"project_root": str(root), "sha256": sha}}})
                self.assertEqual(result["operator_verdict"]["effect"], "UNCHANGED")
                self.assertNotEqual(result["operator_verdict"]["receipt_audit"]["status"], "GROUNDED")
                self.assertEqual(result["declared_supported_closure"], [])
                self.assertEqual(result["dependency_map"], m)


if __name__ == "__main__":
    unittest.main()
