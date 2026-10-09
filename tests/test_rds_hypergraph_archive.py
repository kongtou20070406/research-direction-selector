"""Synthetic archive identity, byte binding and complete display regressions."""
import base64
from copy import deepcopy
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
import contextlib
import io
import shutil
import subprocess
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_hypergraph_archive import (archive_agent_input, build_archive_display,
                                  read_archive, render_archive_html, _packed)


def fixture():
    return {"schema": "rds-readonly-archive-v1", "nodes": [
        {"id": "raw:a", "type": "research", "label": {"zh": "自然名称_保留", "en": "Human label"},
         "summary": "真实声明 <img src=x>", "status": "UNKNOWN", "group": "current", "primary": True},
        {"id": "raw:b", "type": "evidence", "label": "example.json", "status": "COPIED_VERIFIED",
         "group": "current", "source_id": "source:fixture"}],
        "edges": [{"id": "edge:original", "source": "raw:a", "target": "raw:b",
                   "semantic": "provenance", "type": "cites_evidence_hash", "snapshot_id": "snapshot:1"}],
        "groups": [{"id": "current", "label": "当前研究", "summary": "已有分组"}]}


def encoded(graph):
    return (json.dumps(graph, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_zip(self, raw, *, binding=None, entries=None):
        path = self.root / "fixture.zip"
        entry = "ALL_NODES_AND_EDGES.json"
        binding = binding or {"path": entry, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(entry, raw)
            archive.writestr("MANIFEST.json", json.dumps({"files": [binding]}))
            # Imported script is data and must never run.
            archive.writestr("VERIFY.py", "raise RuntimeError('bundle code executed')")
            for name, value in entries or []:
                archive.writestr(name, value)
        return path

    def test_zip_preserves_selected_original_bytes_and_unknown_provenance(self):
        raw = encoded(fixture())
        bundle = read_archive(self.write_zip(raw))
        self.assertEqual(bundle["raw"], raw)
        self.assertEqual(bundle["graph"], fixture())
        self.assertEqual(bundle["graph_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(bundle["counts"], {"nodes": 2, "edges": 1, "hyperedges": 0})
        self.assertEqual(bundle["analysis_status"], "NOT_RUN_ARCHIVE")
        self.assertEqual(bundle["display"]["nodes"][0]["status"], "UNKNOWN")
        self.assertEqual(bundle["display"]["nodes"][0]["label"], "自然名称_保留")
        self.assertEqual(bundle["display"]["semantics"], ["provenance"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["fixture.zip"])

    def test_native_recorded_execution_results_reuse_viewer_semantics(self):
        rows = [{"id": "owned:run:" + outcome, "record_kind": "run", "status": "SUPPORTED",
                 "outcome": outcome, "label": {"zh": "执行 " + outcome}, "summary": "已有用途"}
                for outcome in ("SUCCEEDED", "FAILED", "TIMED_OUT")]
        rows.extend([
            {"id": "owned:run:conflict", "record_kind": "run", "status": "SUPPORTED",
             "outcome": "SUCCEEDED", "run_status": "FAILED"},
            {"id": "owned:run:unknown", "record_kind": "run", "status": "UNKNOWN", "outcome": "FAILED"},
            {"id": "owned:fact:run.demo.failed", "record_kind": "lifecycle_fact", "status": "SUPPORTED",
             "owned_fact": {"id": "run.demo.failed", "value": False, "reliable": True}}])
        graph = {"schema": 1, "nodes": rows, "hyperedges": [], "goals": []}
        before = deepcopy(graph)
        display = build_archive_display(graph)["nodes"]
        self.assertEqual([n["outline"]["state"] for n in display],
                         ["success", "failure", "failure", "neutral", "neutral", "neutral"])
        self.assertEqual([n["outline"]["color"] for n in display[:3]], ["#39b872", "#e65b63", "#e65b63"])
        self.assertEqual(display[1]["status_text"], "执行失败")
        self.assertEqual(display[0]["display_kind"], "执行")
        self.assertEqual(display[0]["shape"], "square")
        self.assertEqual(display[0]["label"], "执行 SUCCEEDED")
        self.assertEqual(display[0]["summary"], "已有用途")
        self.assertEqual(graph, before)

    def test_wrapped_original_records_keep_archive_binding_and_readable_results(self):
        graph = {"schema": "rds-readonly-archive-v1", "nodes": [
            {"id": "archive:run1", "original_id": "owned:run:job_deadbeef12345678", "type": "original_node",
             "record_kind": "run", "label": {"zh": "训练运行"}, "status": "SUPPORTED", "outcome": "FAILED",
             "summary": "原执行用途", "source_id": "source:fixture", "group": "history"},
            {"id": "archive:run2", "original_id": "owned:run:old_job", "type": "history_node",
             "status": "SUPPORTED", "outcome": "TIMED_OUT"},
            {"id": "source", "type": "source", "status": "COPIED_VERIFIED", "label": "来源副本"},
            {"id": "failed.json", "type": "record", "status": "SUPPORTED", "outcome": "FAILED"}], "edges": []}
        raw = encoded(graph)
        bundle = read_archive(self.write_zip(raw))
        node = archive_agent_input(bundle, node_id="archive:run1")
        self.assertEqual(node["raw"], graph["nodes"][0])
        self.assertEqual(node["readable"]["id"], "archive:run1")
        self.assertEqual(node["readable"]["label"], "训练运行")
        self.assertEqual(node["readable"]["display_kind"], "执行")
        self.assertEqual(node["readable"]["outline"]["state"], "failure")
        self.assertEqual(node["readable"]["source_id"], "source:fixture")
        self.assertEqual(node["readable"]["group"], "history")
        self.assertEqual(bundle["display"]["nodes"][1]["status_text"], "执行失败")
        self.assertEqual(bundle["display"]["nodes"][2]["status_text"], "副本已核验")
        self.assertEqual(bundle["display"]["nodes"][2]["outline"]["state"], "neutral")
        self.assertEqual(bundle["display"]["nodes"][3]["outline"]["state"], "neutral")
        self.assertEqual(bundle["raw"], raw)
        self.assertEqual(bundle["graph"], graph)

    def test_only_declared_native_goals_accept_explicit_finite_structural_weights(self):
        graph = {"schema": 1, "goals": ["goal", "missing"], "nodes": [
            {"id": "goal", "label": "已声明目标", "status": "UNKNOWN"},
            {"id": "candidate", "status": "UNKNOWN", "goal_weights": {"goal": 0.25, "missing": 8, "guessed": 99}}],
            "hyperedges": [{"id": "rule", "premises": ["candidate"], "conclusion": "goal", "status": "UNKNOWN",
                            "goal_weights": {"goal": 0.5}}]}
        before = deepcopy(graph)
        display = build_archive_display(graph)
        self.assertEqual(display["goals"], [{"id": "goal", "label": "已声明目标"}])
        self.assertEqual(display["nodes"][0]["goal_weights"], {})
        self.assertEqual(display["nodes"][1]["goal_weights"], {"goal": 0.25})
        self.assertEqual(display["nodes"][2]["goal_weights"], {"goal": 0.5})
        self.assertEqual(graph, before)
        for value in (-1, True, "0.5", float("inf"), float("nan"), 10 ** 1000):
            with self.subTest(value=str(value)[:20]):
                graph["nodes"][1]["goal_weights"] = {"goal": value}
                self.assertEqual(build_archive_display(graph)["nodes"][1]["goal_weights"], {})
        self.assertEqual(before["nodes"][1]["goal_weights"], {"goal": 0.25, "missing": 8, "guessed": 99})
        archive = fixture()
        archive["goals"] = ["raw:a"]
        archive["nodes"][0]["goal_weights"] = {"raw:a": 1}
        result = build_archive_display(archive)
        self.assertEqual(result["goals"], [])
        self.assertEqual(result["nodes"][0]["goal_weights"], {})

    def test_archive_categories_and_missing_original_outcomes_remain_distinct_and_neutral(self):
        graph = {"schema": "rds-readonly-archive-v1", "edges": [], "nodes": [
            {"id": "wrapped-run", "original_id": "owned:run:job", "type": "original_node",
             "status": "SUPPORTED", "label": "失败字样不是结果"},
            {"id": "wrapped-receipt", "original_id": "owned:receipt:job", "type": "history_node",
             "status": "SUPPORTED"},
            {"id": "and-rule", "type": "hyperedge", "status": "UNKNOWN"},
            {"id": "experiment", "type": "experiment", "status": "SUPPORTED"},
            {"id": "attempt", "type": "attempt", "status": "SUPPORTED"},
            {"id": "version", "type": "version", "status": "ARCHIVED_VERSION"}]}
        before = deepcopy(graph)
        nodes = build_archive_display(graph)["nodes"]
        self.assertEqual([(n["display_kind"], n["shape"]) for n in nodes],
                         [("执行", "square"), ("回执", "ring"), ("共同前提", "diamond"),
                          ("实验", "square"), ("执行尝试", "square"), ("历史版本", "ring")])
        self.assertTrue(all(n["outline"]["state"] == "neutral" for n in nodes))
        self.assertEqual(nodes[0]["status_text"], "未判定")
        self.assertEqual(nodes[5]["status_text"], "历史版本")
        self.assertEqual(graph, before)

    def test_manifest_sha_length_and_unique_entry_are_required(self):
        raw = encoded(fixture())
        for field, value in (("bytes", len(raw) + 1), ("sha256", "0" * 64)):
            binding = {"path": "ALL_NODES_AND_EDGES.json", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            binding[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "manifest length/SHA"):
                read_archive(self.write_zip(raw, binding=binding))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            path = self.write_zip(raw, entries=[("ALL_NODES_AND_EDGES.json", raw)])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            read_archive(path)
        with self.assertRaisesRegex(ValueError, "safe archive"):
            read_archive(path, entry="../graph.json")

    def test_two_graph_entries_are_selected_not_concatenated(self):
        original = fixture(); original["schema"] = "refrm-unified-evidence-graph-v1"
        raw, other = encoded(fixture()), encoded(original)
        path = self.root / "two.zip"
        values = {"ALL_NODES_AND_EDGES.json": raw, "ReFRM_RDS/unified_graph/graph.json": other}
        with zipfile.ZipFile(path, "w") as archive:
            for name, value in values.items():
                archive.writestr(name, value)
            archive.writestr("MANIFEST.json", json.dumps({"files": [
                {"path": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                for name, value in values.items()]}))
        self.assertEqual(read_archive(path)["raw"], raw)
        selected = read_archive(path, entry="ReFRM_RDS/unified_graph/graph.json")
        self.assertEqual(selected["raw"], other)
        self.assertEqual(selected["counts"]["nodes"], 2)

    def test_large_display_keeps_all_records_edges_and_leaf_membership(self):
        graph = {"schema": "rds-readonly-archive-v1", "nodes": [
            {"id": "n" + str(i), "type": "record", "status": "UNKNOWN",
             "group": "g" + str(i % 3), "source_id": "s" + str(i % 5)} for i in range(6001)],
            "edges": [{"id": "e" + str(i), "source": "n" + str(i), "target": "n" + str(i + 1),
                       "semantic": "history", "type": "snapshot_successor"} for i in range(6000)]}
        before = deepcopy(graph)
        display = build_archive_display(graph)
        self.assertEqual(len(display["nodes"]), 6001)
        self.assertEqual(len(display["edges"]), 6000)
        self.assertEqual([edge[4] for edge in display["edges"]], list(range(6000)))
        leaves = [c for c in display["clusters"] if c["level"] == 2]
        self.assertEqual(sorted(i for c in leaves for i in c["nodes"]), list(range(6001)))
        self.assertTrue(all(1 <= c["node_count"] <= 64 for c in leaves))
        for node in display["nodes"]:
            cluster = display["clusters"][node["parent"]]
            self.assertLessEqual(cluster["bounds"][0], node["x"])
            self.assertGreaterEqual(cluster["bounds"][2], node["x"])
            self.assertLessEqual(cluster["bounds"][1], node["y"])
            self.assertGreaterEqual(cluster["bounds"][3], node["y"])
        self.assertEqual(sum(display["clusters"][i]["node_count"] for i in display["roots"]), 6001)
        self.assertEqual(graph, before)
        self.assertEqual(display, build_archive_display(graph))

    def test_duplicates_dangling_and_malformed_schema_fail_before_render(self):
        cases = []
        duplicate = fixture(); duplicate["nodes"].append(deepcopy(duplicate["nodes"][0])); cases.append(duplicate)
        duplicate = fixture(); duplicate["edges"].append(deepcopy(duplicate["edges"][0])); cases.append(duplicate)
        dangling = fixture(); dangling["edges"][0]["target"] = "missing"; cases.append(dangling)
        dangling = fixture(); dangling["edges"][0]["target"] = {}; cases.append(dangling)
        for schema in ([], {}, True, "unsupported"):
            bad = fixture(); bad["schema"] = schema; cases.append(bad)
        for graph in cases:
            before = deepcopy(graph)
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                build_archive_display(graph)
            self.assertEqual(graph, before)

    def test_explicit_limits_reject_instead_of_truncating(self):
        with patch("rds_hypergraph_archive.MAX_NODES", 1), self.assertRaisesRegex(ValueError, "node limit"):
            build_archive_display(fixture())
        with patch("rds_hypergraph_archive.MAX_EDGES", 0), self.assertRaisesRegex(ValueError, "edge limit"):
            build_archive_display(fixture())
        path = self.root / "large.json"; path.write_bytes(encoded(fixture()))
        with patch("rds_hypergraph_archive.MAX_BYTES", 8), self.assertRaisesRegex(ValueError, "byte limit"):
            read_archive(path)

    def test_standard_schema_preserves_and_rule_identity_and_raw_status(self):
        graph = {"schema": 1, "nodes": [{"id": id, "status": "UNKNOWN"} for id in ("p", "q", "goal")],
                 "hyperedges": [{"id": "rule", "premises": ["p", "q"], "conclusion": "goal", "status": "PROPOSED"}],
                 "goals": ["goal"]}
        path = self.root / "native.json"; raw = encoded(graph); path.write_bytes(raw)
        bundle = read_archive(path)
        self.assertEqual(bundle["raw"], raw)
        self.assertEqual(bundle["graph"], graph)
        self.assertEqual(bundle["counts"], {"nodes": 4, "edges": 3, "hyperedges": 1})
        hub = bundle["display"]["nodes"][-1]
        self.assertEqual(hub["raw_hyperedge_id"], "rule")
        self.assertEqual(hub["status"], "PROPOSED")
        self.assertEqual(bundle["display"]["edge_types"], ["and_premise", "and_conclusion"])
        selected = archive_agent_input(bundle, edge_id="rule")
        self.assertEqual(selected["raw"], graph["hyperedges"][0])
        self.assertEqual(len(selected["readable"]["incidences"]), 3)

    def test_empty_graph_and_requested_agent_record_are_bound(self):
        display = build_archive_display({"schema": "rds-readonly-archive-v1", "nodes": [], "edges": []})
        self.assertEqual(display["roots"], [])
        self.assertEqual(display["bounds"], [0, 0, 1, 1])
        path = self.root / "graph.json"; path.write_bytes(encoded(fixture()))
        bundle = read_archive(path)
        node = archive_agent_input(bundle, node_id="raw:a")
        self.assertEqual(node["raw"], fixture()["nodes"][0])
        self.assertEqual(node["binding"]["graph_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(archive_agent_input(bundle)["raw"], fixture())
        with self.assertRaisesRegex(ValueError, "one archive"):
            archive_agent_input(bundle, node_id="raw:a", edge_id="edge:original")
        with self.assertRaisesRegex(ValueError, "Unknown raw"):
            archive_agent_input(bundle, node_id="missing")

    def test_disk_hierarchy_contains_members_and_disjoint_unequal_children(self):
        graph = {"schema": "rds-readonly-archive-v1", "nodes": [], "edges": []}
        for group, source, count in (("big", "source:human", 257), ("big", "tiny", 3),
                                     ("big", "other", 65), ("small", "source:" + "a" * 64, 2)):
            for i in range(count):
                graph["nodes"].append({"id": group + source + str(i), "group": group,
                                       "source_id": source, "type": "record", "status": "UNKNOWN"})
        graph["nodes"].append({"id": "source:human", "group": "big", "type": "source",
                               "label": "原始来源名称", "source_id": "source:human"})
        before = deepcopy(graph)
        display = build_archive_display(graph)
        self.assertEqual(graph, before)
        self.assertEqual(display, build_archive_display(graph))
        clusters, nodes = display["clusters"], display["nodes"]
        self.assertEqual(len({(n["x"], n["y"]) for n in nodes}), len(nodes))
        self.assertEqual(sorted(i for c in clusters for i in c["nodes"]), list(range(len(nodes))))
        self.assertIn("原始来源名称", [c["label"] for c in clusters if c["level"] == 1])
        self.assertFalse(any("a" * 64 in c["label"] or c["label"].startswith("source:")
                             for c in clusters if c["level"] == 1))
        for cluster in clusters:
            self.assertTrue(all(math.isfinite(cluster[key]) for key in ("x", "y", "radius")))
            for child_index in cluster["children"]:
                child = clusters[child_index]
                self.assertLessEqual(math.hypot(child["x"] - cluster["x"], child["y"] - cluster["y"])
                                     + child["radius"], cluster["radius"] + 1e-8)
                self.assertGreaterEqual(child["bounds"][0], cluster["bounds"][0] - 1e-8)
                self.assertGreaterEqual(child["bounds"][1], cluster["bounds"][1] - 1e-8)
                self.assertLessEqual(child["bounds"][2], cluster["bounds"][2] + 1e-8)
                self.assertLessEqual(child["bounds"][3], cluster["bounds"][3] + 1e-8)
            for node_index in cluster["nodes"]:
                node = nodes[node_index]
                self.assertLessEqual(math.hypot(node["x"] - cluster["x"], node["y"] - cluster["y"]), cluster["radius"])
                self.assertEqual(node["status"], "UNKNOWN")
        # Independent Euclidean geometry oracle, rather than the packing implementation.
        for siblings in [display["roots"]] + [c["children"] for c in clusters]:
            for ordinal, left_index in enumerate(siblings):
                left = clusters[left_index]
                for right_index in siblings[ordinal + 1:]:
                    right = clusters[right_index]
                    self.assertGreaterEqual(math.hypot(left["x"] - right["x"], left["y"] - right["y"]) + 1e-8,
                                            left["radius"] + right["radius"])

    def test_strict_json_and_packed_raw_keep_agent_layer_separate(self):
        path = self.root / "graph.json"
        for raw in (b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'{"schema":1,"weight":1e309}', b'\xff'):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                read_archive(path)
        raw = encoded(fixture())
        self.assertEqual(gzip.decompress(base64.b64decode(_packed(raw))), raw)
        self.assertNotIn("<img", _packed(raw))

    def test_renderer_rejects_unpinned_dependency_before_loading_template(self):
        root = self.root / "assets"; (root / "replica").mkdir(parents=True)
        (root / "replica/renderer.js").write_text("throw Error('untrusted')", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pinned commit"):
            render_archive_html({}, root, self.root / "missing-pixi.js")

    def test_duplicate_declared_groups_are_rejected_without_overwriting(self):
        graph = fixture()
        graph["groups"].append({"id": "current", "label": "Conflicting display group"})
        before = deepcopy(graph)
        with self.assertRaisesRegex(ValueError, "Duplicate declared group ID"):
            build_archive_display(graph)
        self.assertEqual(graph, before)

    def test_agent_edge_readable_names_and_and_members_preserve_raw_identity(self):
        path = self.root / "archive.json"; path.write_bytes(encoded(fixture()))
        bundle = read_archive(path)
        selected = archive_agent_input(bundle, edge_id="edge:original")
        self.assertEqual(selected["raw"], fixture()["edges"][0])
        self.assertEqual(selected["readable"]["source"], "自然名称_保留")
        self.assertEqual(selected["readable"]["target"], "example.json")
        self.assertEqual(selected["readable"]["type"], "cites_evidence_hash")
        self.assertEqual(selected["readable"]["status"], "UNKNOWN")
        self.assertTrue(selected["readable"]["label"])
        self.assertIn("来源", selected["readable"]["summary"])
        self.assertEqual(selected["readable"]["incidences"], bundle["display"]["edges"])
        native = {"schema": 1, "nodes": [{"id": id, "label": label, "status": "UNKNOWN"}
                  for id, label in (("p", "Premise one"), ("q", "Premise two"), ("g", "Conclusion"))],
                  "hyperedges": [{"id": "and", "premises": ["p", "q"], "conclusion": "g", "status": "PROPOSED"}]}
        path.write_bytes(encoded(native)); bundle = read_archive(path)
        selected = archive_agent_input(bundle, edge_id="and")
        self.assertEqual(selected["raw"], native["hyperedges"][0])
        self.assertEqual(selected["readable"]["premises"], ["Premise one", "Premise two"])
        self.assertEqual(selected["readable"]["conclusion"], "Conclusion")
        self.assertEqual(selected["readable"]["status"], "PROPOSED")
        self.assertEqual(len(selected["readable"]["incidences"]), 3)

    def test_real_crc_damage_and_encrypted_flags_have_friendly_rejection(self):
        # Stored synthetic bytes isolate CRC failure from deflate decoding.
        path = self.root / "crc.zip"; raw = encoded(fixture())
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("ALL_NODES_AND_EDGES.json", raw)
            archive.writestr("MANIFEST.json", json.dumps({"files": [{"path": "ALL_NODES_AND_EDGES.json",
                               "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]}))
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("ALL_NODES_AND_EDGES.json")
        original = path.read_bytes(); damaged = bytearray(original)
        filename_length = int.from_bytes(damaged[info.header_offset + 26:info.header_offset + 28], "little")
        extra_length = int.from_bytes(damaged[info.header_offset + 28:info.header_offset + 30], "little")
        damaged[info.header_offset + 30 + filename_length + extra_length + 1] ^= 1
        path.write_bytes(damaged)
        with self.assertRaisesRegex(ValueError, "damaged, encrypted"):
            read_archive(path)
        encrypted = bytearray(original)
        encrypted[info.header_offset + 6] |= 1
        central = encrypted.index(b"PK\x01\x02")
        encrypted[central + 8] |= 1
        path.write_bytes(encrypted)
        with self.assertRaisesRegex(ValueError, "damaged, encrypted"):
            read_archive(path)


class ArchiveCallerTests(unittest.TestCase):
    setUp = ArchiveTests.setUp
    def test_cli_selected_record_without_renderer_or_project_initialization(self):
        import rds_hypergraph_view as view
        path = self.root / "fixture.json"; raw = encoded(fixture()); path.write_bytes(raw)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = view.main(["--root", str(self.root / "uninitialized-project"),
                                "--archive", str(path), "--export-agent-input", "--archive-node", "raw:a"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["raw"], fixture()["nodes"][0])
        self.assertEqual(payload["readable"]["label"], "自然名称_保留")
        self.assertFalse((self.root / "uninitialized-project").exists())
        self.assertEqual(path.read_bytes(), raw)

    def test_cli_rejects_source_alias_and_incompatible_modes_before_write(self):
        import rds_hypergraph_view as view
        source = self.root / "input.html"; raw = encoded(fixture()); source.write_bytes(raw)
        alias = self.root / "alias.html"; alias.hardlink_to(source)
        attempts = [
            ["--archive", str(source), "--output", str(source)],
            ["--archive", str(source), "--output", str(alias)],
            ["--archive", str(source), "--demo"],
            ["--archive", str(source), "--archive-node", "raw:a"],
            ["--archive", str(source), "--export-agent-input", "--archive-node", "raw:a", "--archive-edge", "edge:original"],
            ["--archive", str(source), "--readable-json", str(source)],
        ]
        for args in attempts:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                view.main(args + ["--root", str(self.root), "--replica-root", str(self.root), "--pixi-js", str(self.root / "pixi.js")])
            self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(source.read_bytes(), raw)
        self.assertEqual(alias.read_bytes(), raw)
        self.assertFalse((self.root / ".rds").exists())

    def test_archive_browser_geometry_regressions(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node runtime is required for browser geometry checks")
        result = subprocess.run([node, str(ROOT / "tests/hypergraph_archive_checks.cjs")],
                                capture_output=True, text=True, timeout=30, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
