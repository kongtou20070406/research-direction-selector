"""All-node analysis covers disconnected maps without changing declared goals."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rds_hypergraph import ASSURANCE, DEFAULT_LIMITS, analyze_hypergraph


def node(ident, status="UNKNOWN", **fields):
    return {"id": ident, "status": status, "source": {"locator": "fixture:" + ident}, **fields}


def edge(ident, premises, conclusion, status="SUPPORTED", **fields):
    return {"id": ident, "premises": premises, "conclusion": conclusion,
            "status": status, "source": {"locator": "fixture:" + ident}, **fields}


def graph(nodes, edges=(), goals=(), **limits):
    return {"schema": 1, "nodes": nodes, "hyperedges": list(edges), "goals": list(goals), "limits": limits}


class HypergraphCoverageTests(unittest.TestCase):
    def test_disconnected_components_have_actual_blocker_analysis(self):
        spec = graph([node("goal", "SUPPORTED"), node("a"), node("b"), node("c")],
                     [edge("ab", ["a"], "b"), edge("bc", ["b"], "c", "PROPOSED")], ["goal"])
        original = deepcopy(spec)
        local = analyze_hypergraph(spec)
        result = analyze_hypergraph(spec, analysis_targets="all")
        self.assertEqual(spec, original)
        self.assertEqual(result["goals"], local["goals"])
        self.assertEqual(result["node_analysis"]["b"]["minimal_missing_evidence_sets"], [["node:a"]])
        self.assertEqual(result["node_analysis"]["c"]["minimal_missing_evidence_sets"], [["node:a", "rule:bc"]])
        self.assertEqual(result["declared_supported_closure"], ["goal"])
        self.assertEqual(result["ready_obligations"], [])
        self.assertEqual({row["token"] for row in result["all_ready_obligations"]}, {"node:a"})
        coverage = result["coverage"]
        self.assertTrue(coverage["full"])
        self.assertEqual(coverage["status"], "FULL")
        self.assertEqual(coverage["node_ids"], ["a", "b", "c", "goal"])
        self.assertEqual(coverage["hyperedge_ids"], ["ab", "bc"])
        self.assertEqual(coverage["counts"], {"nodes": 4, "hyperedges": 2, "nodes_validated": 4,
                         "hyperedges_validated": 2, "node_analysis_reports": 4,
                         "nodes_blocker_complete": 4, "hyperedges_classified": 2})
        self.assertEqual(coverage["reasons"], [])
        self.assertEqual(result["limits"], DEFAULT_LIMITS)

    def test_all_node_results_match_complete_individual_target_queries(self):
        spec = graph([node("a"), node("b"), node("c"), node("orphan"), node("bad", "CONTRADICTED")],
                     [edge("ab", ["a"], "b"), edge("ba", ["b"], "a"),
                      edge("bc", ["b"], "c", "PROPOSED"), edge("bad-rule", ["c"], "bad")], ["c"])
        result = analyze_hypergraph(spec, analysis_targets="all")
        self.assertFalse(result["truncated"])
        for ident in result["node_analysis"]:
            individual = analyze_hypergraph({**spec, "goals": [ident]})
            self.assertEqual(result["node_analysis"][ident], individual["goals"][ident], ident)
        self.assertEqual(result["node_analysis"]["a"]["status"], "UNRESOLVED")
        self.assertEqual(result["node_analysis"]["orphan"]["status"], "UNKNOWN")
        self.assertEqual(result["node_analysis"]["bad"]["status"], "CONTRADICTED")

    def test_goal_ready_obligations_keep_original_scope_and_order(self):
        spec = graph([node("goal"), node("s", "SUPPORTED"), node("leaf"), node("other"), node("proposed")],
                     [edge("goal-proof", ["s"], "goal", "PROPOSED"),
                      edge("goal-leaf", ["leaf"], "goal"),
                      edge("other-proof", ["s"], "proposed", "PROPOSED")], ["goal"])
        default = analyze_hypergraph(spec)
        full = analyze_hypergraph(spec, analysis_targets="all")
        self.assertEqual(full["ready_obligations"], default["ready_obligations"])
        self.assertEqual([row["token"] for row in full["ready_obligations"]], ["rule:goal-proof", "node:leaf"])
        self.assertEqual({row["token"] for row in full["all_ready_obligations"]},
                         {"rule:goal-proof", "rule:other-proof", "node:leaf", "node:other"})
        full["all_ready_obligations"][0]["source"]["locator"] = "changed"
        self.assertEqual(full["ready_obligations"], default["ready_obligations"])

    def test_disconnected_receipt_revalidation_keeps_original_global_behavior(self):
        binding = {"receipt": {"project_root": "fixture", "sha256": "c" * 64}}
        spec = graph([node("goal", "SUPPORTED"), node("s", "SUPPORTED"), node("other")],
                     [edge("blocked", ["s"], "other", evidence=binding)], ["goal"])
        default = analyze_hypergraph(spec)
        full = analyze_hypergraph(spec, analysis_targets="all")
        self.assertEqual(full["ready_obligations"], default["ready_obligations"])
        self.assertEqual([row["token"] for row in full["ready_obligations"]], ["rule:blocked"])
        self.assertEqual(full["all_ready_obligations"], full["ready_obligations"])

    def test_disconnected_truncation_cannot_be_masked_by_supported_goal(self):
        spec = graph([node("healthy", "SUPPORTED"), node("a"), node("b"), node("c")],
                     [edge("abc", ["a", "b"], "c")], ["healthy"], max_combinations=1)
        local = analyze_hypergraph(spec)
        self.assertFalse(local["truncated"])
        result = analyze_hypergraph(spec, analysis_targets="all")
        self.assertEqual(result["goals"], local["goals"])
        self.assertEqual(result["node_analysis"]["healthy"]["minimal_missing_evidence_sets"], [[]])
        self.assertTrue(result["node_analysis"]["healthy"]["blocker_sets_complete"])
        self.assertTrue(result["truncated"])
        self.assertFalse(result["coverage"]["full"])
        self.assertEqual(result["coverage"]["status"], "INCOMPLETE")
        self.assertEqual(result["coverage"]["reasons"], ["max_combinations exceeded"])
        self.assertEqual(result["node_analysis"]["c"]["status"], "UNKNOWN")
        self.assertFalse(result["node_analysis"]["c"]["blocker_sets_complete"])
        self.assertEqual(result["node_analysis"]["c"]["minimal_missing_evidence_sets"], [])
        self.assertEqual(result["coverage"]["hyperedge_analysis"]["abc"]["blocker_analysis"], "INCOMPLETE")

    def test_disconnected_components_share_one_work_cap(self):
        spec = graph([node("a"), node("b"), node("x"), node("y")],
                     [edge("ab", ["a"], "b"), edge("xy", ["x"], "y")], ["b"], max_combinations=3)
        self.assertFalse(analyze_hypergraph(spec)["truncated"])
        self.assertFalse(analyze_hypergraph({**spec, "goals": ["y"]})["truncated"])
        result = analyze_hypergraph(spec, analysis_targets="all")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["combinations_examined"], 3)
        self.assertEqual(result["limits"]["max_combinations"], 3)

    def test_disconnected_antichain_cap_is_global_incomplete(self):
        spec = graph([node("known", "SUPPORTED"), node("a"), node("b"), node("c")],
                     [edge("ac", ["a"], "c"), edge("bc", ["b"], "c")], ["known"], max_blocker_sets=1)
        result = analyze_hypergraph(spec, analysis_targets="all")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["truncation_reason"], "max_blocker_sets exceeded")
        self.assertFalse(result["coverage"]["full"])
        self.assertEqual(result["goals"]["known"]["status"], "DECLARED_SUPPORTED")

    def test_irrelevant_malformed_edge_is_rejected(self):
        for bad in (edge("bad", ["missing"], "a"), edge("bad", ["a"], "missing"),
                    edge("bad", ["a"], "a", "INVALID")):
            spec = graph([node("goal", "SUPPORTED"), node("a")], [bad], ["goal"])
            with self.subTest(edge=bad), self.assertRaises(ValueError):
                analyze_hypergraph(spec, analysis_targets="all")

    def test_every_edge_has_disposition_including_exact_exclusions(self):
        binding = {"receipt": {"project_root": "fixture", "sha256": "a" * 64}}
        spec = graph([node("s", "SUPPORTED"), node("a"), node("p"), node("bad", "CONTRADICTED")],
                     [edge("done", ["a"], "s"), edge("refuted", ["s"], "a", "CONTRADICTED"),
                      edge("bad-head", ["s"], "bad"), edge("proposed", ["s"], "p", "PROPOSED"),
                      edge("blocked", ["s"], "a", evidence=binding)], ["s"])
        result = analyze_hypergraph(spec, analysis_targets="all")
        reports = result["coverage"]["hyperedge_analysis"]
        self.assertEqual(set(reports), {row["id"] for row in spec["hyperedges"]})
        for ident, expected in {"done": "CONCLUSION_SUPPORTED", "refuted": "CONTRADICTED",
                                "bad-head": "CONCLUSION_CONTRADICTED", "proposed": "PROPOSED_PREMISES_READY",
                                "blocked": "RECEIPT_BLOCKED"}.items():
            self.assertEqual(reports[ident]["disposition"], expected)
            self.assertTrue(reports[ident]["reason"])
        self.assertEqual(reports["proposed"]["premise_states"], {"s": "DECLARED_SUPPORTED"})
        self.assertEqual(reports["refuted"]["blocker_analysis"], "EXACTLY_EXCLUDED")
        self.assertEqual(result["node_analysis"]["a"]["minimal_missing_evidence_sets"], [["rule:blocked"]])
        self.assertEqual(result["node_analysis"]["p"]["status"], "UNKNOWN")
        self.assertTrue(result["coverage"]["full"])
        self.assertEqual(result["assurance"], ASSURANCE)

    def test_receipt_audit_preserves_support_boundary_for_disconnected_node(self):
        binding = {"receipt": {"project_root": "fixture", "sha256": "b" * 64}}
        spec = graph([node("goal", "SUPPORTED"), node("bound", "SUPPORTED", evidence=binding)], goals=["goal"])
        reads = []

        def missing(root, sha):
            reads.append((root, sha))
            return {"status": "RECEIPT_NOT_FOUND"}

        result = analyze_hypergraph(spec, audit_receipts_enabled=True, read_receipt=missing, analysis_targets="all")
        self.assertEqual(reads, [("fixture", "b" * 64)])
        self.assertEqual(result["declared_supported_closure"], ["goal"])
        self.assertEqual(result["receipt_blocked_node_ids"], ["bound"])
        self.assertNotEqual(result["node_analysis"]["bound"]["status"], "DECLARED_SUPPORTED")
        self.assertEqual(result["node_analysis"]["bound"]["minimal_missing_evidence_sets"], [["node:bound"]])
        self.assertTrue(result["coverage"]["full"])

    def test_empty_goals_remain_empty_with_all_nodes_analyzed(self):
        result = analyze_hypergraph(graph([node("a"), node("b")], [edge("ab", ["a"], "b")]), analysis_targets="all")
        self.assertEqual(result["goals"], {})
        self.assertEqual(result["node_analysis"]["b"]["minimal_missing_evidence_sets"], [["node:a"]])
        self.assertTrue(result["coverage"]["full"])

    def test_default_shape_and_goal_scope_stay_unchanged(self):
        spec = graph([node("goal"), node("other")], goals=["goal"])
        default = analyze_hypergraph(spec)
        self.assertEqual(default, analyze_hypergraph(spec, analysis_targets="goals"))
        self.assertNotIn("node_analysis", default)
        self.assertNotIn("coverage", default)
        self.assertEqual(list(default["goals"]), ["goal"])
        full = analyze_hypergraph(spec, analysis_targets="all")
        full["node_analysis"]["goal"]["minimal_missing_evidence_sets"].append(["changed"])
        self.assertEqual(full["goals"], default["goals"])

    def test_digest_binds_all_components_and_all_limits(self):
        spec = graph([node("goal", "SUPPORTED"), node("other")], goals=["goal"])
        result = analyze_hypergraph(spec, analysis_targets="all")
        raw = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.assertEqual(result["coverage"]["input_sha256"], hashlib.sha256(raw).hexdigest())
        for changed in ({**spec, "nodes": spec["nodes"] + [node("new")]},
                        {**spec, "limits": {"max_combinations": 100}}):
            self.assertNotEqual(analyze_hypergraph(changed, analysis_targets="all")["coverage"]["input_sha256"],
                                result["coverage"]["input_sha256"])

    def test_invalid_target_scope_and_input_limits_are_rejected(self):
        spec = graph([node("a"), node("b")], max_nodes=1)
        with self.assertRaisesRegex(ValueError, "max_nodes exceeded"):
            analyze_hypergraph(spec, analysis_targets="all")
        for scope in ("local", None, 1, ["a"]):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "analysis_targets"):
                analyze_hypergraph(graph([node("a")]), analysis_targets=scope)


if __name__ == "__main__":
    unittest.main()
