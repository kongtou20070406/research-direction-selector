"""Caller-level parity checks for optional native hypergraph relevance."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rds_hypergraph as hypergraph


def fixture():
    return {
        "schema": 1,
        "nodes": [
            {"id": name, "status": status, "source": "fixture:" + name}
            for name, status in (("base", "SUPPORTED"), ("goal", "UNKNOWN"),
                                 ("cycle-a", "UNKNOWN"), ("cycle-b", "UNKNOWN"),
                                 ("proposed-goal", "UNKNOWN"), ("isolated", "UNKNOWN"),
                                 ("fact", "UNKNOWN"), ("empty-goal", "UNKNOWN"))
        ],
        "hyperedges": [
            {"id": "supported", "premises": ["base"], "conclusion": "goal",
             "status": "SUPPORTED", "source": "fixture:supported"},
            {"id": "cycle-forward", "premises": ["cycle-a"], "conclusion": "cycle-b",
             "status": "SUPPORTED", "source": "fixture:cycle-forward"},
            {"id": "cycle-back", "premises": ["cycle-b"], "conclusion": "cycle-a",
             "status": "SUPPORTED", "source": "fixture:cycle-back"},
            {"id": "proposed-rule", "premises": ["fact"], "conclusion": "proposed-goal",
             "status": "PROPOSED", "source": "fixture:proposed"},
            {"id": "contradicted-rule", "premises": [], "conclusion": "goal",
             "status": "CONTRADICTED", "source": "fixture:contradicted"},
            {"id": "no-premise", "premises": [], "conclusion": "fact",
             "status": "SUPPORTED", "source": "fixture:axiom"},
            {"id": "empty-proposal", "premises": [], "conclusion": "empty-goal",
             "status": "PROPOSED", "source": "fixture:empty-proposal"},
        ],
        "goals": ["goal", "empty-goal", "proposed-goal", "cycle-a"],
    }


class RdsBusIntegrationTests(unittest.TestCase):
    def test_analyze_caller_uses_native_and_matches_python_without_promoting_proofs(self):
        from rds_bus_adapter import TopologyGraph
        with TopologyGraph(use_native=None) as probe:
            available = probe.native
        if not available:
            if os.environ.get("RDS_REQUIRE_BUS") == "1":
                self.fail("RDS_REQUIRE_BUS=1 but the ABI2 native library is unavailable")
            self.skipTest("compiled ABI2 library is unavailable")

        spec = fixture()
        with patch.dict(os.environ, {"RDS_REQUIRE_BUS": "1"}), \
             patch.object(hypergraph, "_native_goal_relevance", wraps=hypergraph._native_goal_relevance) as native:
            native_result = hypergraph.analyze_hypergraph(spec)
        with patch.dict(os.environ, {"RDS_REQUIRE_BUS": "0"}), \
             patch.object(hypergraph, "_native_goal_relevance", return_value=None):
            fallback_result = hypergraph.analyze_hypergraph(spec)

        self.assertGreater(native.call_count, 0)
        self.assertEqual(native_result, fallback_result)
        self.assertEqual(native_result["declared_supported_closure"], ["base", "fact", "goal"])
        self.assertEqual(native_result["goals"]["goal"]["status"], "DECLARED_SUPPORTED")
        self.assertEqual(native_result["goals"]["empty-goal"]["status"], "UNKNOWN")
        self.assertNotIn("proposed-goal", native_result["declared_supported_closure"])
        self.assertNotIn("cycle-a", native_result["declared_supported_closure"])
        self.assertNotIn("cycle-b", native_result["declared_supported_closure"])
        self.assertNotIn("isolated", native_result["declared_supported_closure"])
        self.assertNotIn("contradicted-rule", {row["token"] for row in native_result["ready_obligations"]})
        self.assertIn("rule:proposed-rule", {row["token"] for row in native_result["ready_obligations"]})
        self.assertIn("rule:empty-proposal", {row["token"] for row in native_result["ready_obligations"]})
        self.assertEqual(native_result["assurance"], hypergraph.ASSURANCE)

    def test_required_native_failure_cannot_silently_use_fallback(self):
        with patch.dict(os.environ, {"RDS_REQUIRE_BUS": "1"}), \
             patch.object(hypergraph, "_native_goal_relevance", side_effect=RuntimeError("native ABI failure")):
            with self.assertRaisesRegex(RuntimeError, "native ABI failure"):
                hypergraph.analyze_hypergraph(fixture())

    def test_required_native_missing_library_cannot_silently_use_fallback(self):
        import rds_bus_adapter
        with patch.dict(os.environ, {"RDS_REQUIRE_BUS": "1"}), \
             patch.object(rds_bus_adapter, "_load_native", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "native library is unavailable"):
                hypergraph.analyze_hypergraph(fixture())

    def test_missing_native_keeps_optional_python_fallback(self):
        import rds_bus_adapter
        with patch.dict(os.environ, {"RDS_REQUIRE_BUS": "0"}), \
             patch.object(rds_bus_adapter, "_load_native", return_value=None):
            result = hypergraph.analyze_hypergraph(fixture())
        self.assertEqual(result["goals"]["goal"]["status"], "DECLARED_SUPPORTED")
        self.assertEqual(result["assurance"], hypergraph.ASSURANCE)


if __name__ == "__main__":
    unittest.main()
