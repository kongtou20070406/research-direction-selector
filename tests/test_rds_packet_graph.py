"""F2 compatibility with the original hypergraph validator and closure."""
from dataclasses import FrozenInstanceError
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_decision_packet import DecisionPacket
from rds_research_import import import_research
from rds_packet_graph import project_packet, Hyperedge
from rds_hypergraph import _validate, analyze_hypergraph


class GraphTests(unittest.TestCase):
    def packet(self, actor="human", **kwargs):
        return DecisionPacket.from_json(import_research(ROOT, project="fixture", goal="test mechanism",
            texts={"a": "PASS means nothing here", "b": "another observation"},
            candidates={"idea": {"claim": "secret mechanism conjecture", "actor": actor,
                                  "premises": ["text:b", "text:a"], "goal": "statement"}}, **kwargs))

    def test_human_ai_conjectures_never_support_goals(self):
        for actor in ("human", "ai"):
            with self.subTest(actor=actor):
                graph = project_packet(self.packet(actor)).to_dict()
                _validate(graph)
                self.assertTrue(all(n["status"] == "UNKNOWN" for n in graph["nodes"]))
                self.assertTrue(all(e["status"] == "PROPOSED" for e in graph["hyperedges"]))
                result = analyze_hypergraph(graph)
                self.assertEqual(result["declared_supported_closure"], [])

    def test_multi_tail_edges_and_reference_only_sources(self):
        packet = self.packet()
        graph = project_packet(packet)
        multi = next(e for e in graph.hyperedges if len(e.premises) == 2)
        nodes = {n.id: n for n in graph.nodes}
        self.assertEqual({nodes[p].source.split("#/")[1] for p in multi.premises},
                         {"evidence/text:a", "evidence/text:b"})
        self.assertTrue(nodes[multi.conclusion].source.endswith("/candidates/idea"))
        self.assertNotIn("secret mechanism", json.dumps(graph.to_dict()))
        self.assertNotIn("PASS means", json.dumps(graph.to_dict()))
        self.assertTrue(all(n.source.startswith(packet.id + "#/") for n in graph.nodes))

    def test_projection_is_deterministic_pure_and_deeply_immutable(self):
        packet = self.packet()
        with patch("builtins.open", side_effect=AssertionError("no I/O")), \
                patch("pathlib.Path.open", side_effect=AssertionError("no I/O")):
            first = project_packet(packet)
            second = project_packet(DecisionPacket.from_json(packet.to_json()))
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.nodes = ()
        first.to_dict()["hyperedges"][0]["premises"].clear()
        self.assertTrue(first.hyperedges[0].premises)
        with self.assertRaises(ValueError):
            Hyperedge("e", [], "n", "s")

    def test_truncation_missing_evidence_and_empty_candidates(self):
        graph = project_packet(self.packet(limits={"max_sources": 1}))
        self.assertEqual(graph.status, "PARTIAL")
        _validate(graph.to_dict())
        empty = project_packet(DecisionPacket.from_json(import_research(ROOT)))
        self.assertEqual(empty.hyperedges, ())
        self.assertEqual(len(empty.goals), 1)
        self.assertEqual(empty.to_dict()["nodes"][0]["status"], "UNKNOWN")

    def test_new_modules_have_no_upward_imports_or_io_in_l2(self):
        allowed = {"rds_research_import": {"rds_bounded_io", "rds_core", "rds_source_documents"},
                   "rds_decision_packet": {"rds_core"}, "rds_packet_graph": {"rds_core", "rds_decision_packet"}}
        for module, imports in allowed.items():
            tree = ast.parse((ROOT / "scripts" / (module + ".py")).read_text(encoding="utf-8"))
            actual = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module.startswith("rds_")}
            self.assertEqual(actual, imports)
            if module != "rds_research_import":
                self.assertFalse(any(isinstance(n, ast.Import) and any(a.name in ("os", "pathlib", "subprocess") for a in n.names)
                                     for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
