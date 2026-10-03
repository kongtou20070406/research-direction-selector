"""Operator-verdict bridge (Issue #49): witness refutation, archived source, blocker recalc."""
import json
from pathlib import Path
import sys
import unittest

ROOT = str(Path(__file__).resolve().parents[1] / "scripts")
sys.path.insert(0, ROOT)
from rds_hypergraph import analyze_hypergraph, apply_operator_verdict, review_hypergraph


def two_route_map():
    """a and b independently support goal g through two supported rules."""
    value = {"claims": {"a": {"state": "supported", "source": "obs-a.json"},
                        "b": {"state": "supported", "source": "obs-b.json"}},
             "rules": [{"from": "a", "to": "g", "state": "supported", "source": "rule-a.json"},
                       {"from": "b", "to": "g", "state": "supported", "source": "rule-b.json"}],
             "goal": "g"}
    return review_hypergraph(value)["dependency_map"]


def verdict(status="CONTRADICTED", operator="bounded_finite_model", witness=None, **extra):
    receipt = {"status": status, "operator": operator}
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

    def test_witness_and_operator_are_archived_in_the_refuted_record_source(self):
        result = apply_operator_verdict(two_route_map(), "node:a",
                                        verdict(witness={"divergence_step": 16, "energy_ratio": 1.42},
                                                input_sha256="A" * 64))
        node = next(n for n in result["dependency_map"]["nodes"] if n["id"] == "a")
        source = node["source"]
        self.assertEqual(source["operator"], "bounded_finite_model")
        self.assertEqual(source["verdict_status"], "CONTRADICTED")
        self.assertEqual(source["witness"], {"divergence_step": 16, "energy_ratio": 1.42})
        self.assertEqual(source["input_sha256"], "a" * 64)  # normalized
        self.assertEqual(source["previous_locator"], "obs-a.json")
        self.assertTrue(source["locator"].startswith("operator-verdict#witness/node/a"))
        # The revision record carries the archived source into persisted history.
        change = next(c for c in result["revision"]["changes"] if c["id"] == "a")
        self.assertEqual(change["previous_source"], source)

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
        self.assertEqual(edge["source"]["operator"], "lean_axiom_review")
        self.assertEqual(edge["source"]["previous_locator"], "rule-a.json")

    def test_pass_and_unknown_never_claim_support(self):
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


if __name__ == "__main__":
    unittest.main()
