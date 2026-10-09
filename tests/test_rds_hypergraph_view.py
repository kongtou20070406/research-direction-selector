"""Real offline export boundaries for native and explicit dependency maps."""
from copy import deepcopy
import json
import hashlib
import io
from contextlib import redirect_stderr
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_hypergraph_view import (JS, D3_BUNDLE, graph_view, read_graph, render_html, large_demo,
    replica_view, dependency_levels, render_replica_html, patch_replica_worker, _legacy_record_report,
    patch_replica_renderer, REPLICA_APP, REPLICA_FILES, display_record, display_records,
    readable_diagnostics, write_html_atomic)
from rds_tms_store import current, save


def example():
    return json.loads((ROOT / "examples/hypergraph-view.json").read_text(encoding="utf-8"))


class HypergraphViewTests(unittest.TestCase):
    def test_short_names_are_presentation_only_and_preserve_human_labels(self):
        rows = [
            {"id": "run.root_upper.succeeded", "owned_fact": {"value": True, "reliable": True}},
            {"id": "owned:run:root_upper", "label": {"zh": "根边界_核查", "en": "Root check"}},
            {"id": "observation.loss_value", "label": {"zh": [], "en": "Loss_value <>&"}},
            {"id": "owned:artifact:a", "source": {"file": "C:\\results\\模型_out.json"}},
            {"id": "owned:artifact:b", "source": {"path": "/results/模型_out.json"}},
            {"id": "natural_name_with_underscores"},
            {"id": "owned:fact:run.root_upper.failed", "label": "Human.failed_name"},
            {"id": "owned:output:" + "a1" * 32, "interpretation": "MISSING", "run_id": "example_run",
             "source": {"locator": "missing declared output example_run:outputs/example.json"}},
            {"id": "owned:artifact:" + "a2" * 32, "run_id": "example_run"},
            {"id": "human-number", "label": "N=12345678901234567890123456789012"},
            {"id": "decimal-file", "record_kind": "artifact",
             "source": {"path": "/results/12345678901234567890123456789012.json"}},
            {"id": "owned:artifact:" + "b1" * 32, "source": {"path": "/results/" + "b2" * 32 + ".json"}},
            {"id": "12345678901234567890123456789012", "label": "12345678901234567890123456789012"},
            {"id": "human-claim", "claim": "Count = 12345678901234567890123456789012"},
        ]
        for row in rows:
            row.setdefault("source", "synthetic:labels")
            if isinstance(row["source"], dict):
                row["source"].setdefault("locator", "synthetic fixture")
                if "file" in row["source"]:
                    row["source"]["sha256"] = "a" * 64
            row["status"] = "UNKNOWN"
        spec = {"schema": 1, "nodes": rows, "hyperedges": [], "goals": [rows[0]["id"]]}
        before = deepcopy(spec)
        result = graph_view(spec, "synthetic:labels")
        payload = self.payload(render_html(result))
        view = replica_view(result)
        expected = ["root upper", "根边界_核查", "Loss_value <>&", "模型_out.json",
                    "模型_out.json", "natural_name_with_underscores", "Human.failed_name",
                    "example.json", "example run · 产物", "N=12345678901234567890123456789012",
                    "12345678901234567890123456789012.json", "产物.json",
                    "12345678901234567890123456789012", "Count = 12345678901234567890123456789012"]
        self.assertEqual([payload["display"]["nodes"][row["id"]]["label"] for row in rows], expected)
        self.assertEqual([view["nodes"][f"c{i}"]["label"] for i in range(len(rows))], expected)
        self.assertNotEqual(view["nodes"]["c3"]["rds"]["record"], view["nodes"]["c4"]["rds"]["record"])
        self.assertEqual(view["nodes"]["c7"]["rds"]["status_text"], "输出缺失")
        self.assertEqual(view["nodes"]["c7"]["rds"]["outline"]["state"], "neutral")
        self.assertEqual(spec, before)
        self.assertEqual(result["graph"], before)
        self.assertEqual(payload["graph"], before)
        self.assertIn("\\u003c", render_html(result))

    def test_status_outlines_distinguish_execution_predicates_from_declarations(self):
        cases = [
            ({"id": "run.r.succeeded", "owned_fact": {"value": True, "reliable": True}}, "success"),
            ({"id": "run.r.succeeded", "owned_fact": {"value": False, "reliable": True}}, "neutral"),
            ({"id": "run.r.failed", "owned_fact": {"value": False, "reliable": True}}, "neutral"),
            ({"id": "run.r.failed", "owned_fact": {"value": True, "reliable": True}}, "failure"),
            ({"id": "run.r.timed_out", "owned_fact": {"value": True, "reliable": True}}, "failure"),
            ({"id": "run.r.succeeded", "owned_fact": {"value": 1, "reliable": True}}, "neutral"),
            ({"id": "run.r.failed", "owned_fact": {"value": 0, "reliable": True}}, "neutral"),
            ({"id": "run.r.succeeded", "owned_fact": {"value": True, "reliable": False}}, "neutral"),
            ({"id": "run.r.succeeded", "owned_fact": {"id": "run.other.succeeded", "value": True, "reliable": True}}, "neutral"),
            ({"id": "run.r.completed", "owned_fact": {"value": True, "reliable": True}}, "neutral"),
            ({"id": "owned:run:r", "lifecycle_status": "COMPLETED"}, "neutral"),
            ({"id": "owned:run:r", "lifecycle_status": "RUNNING"}, "neutral"),
            ({"id": "owned:run:r", "lifecycle_status": "NOT_REGISTERED"}, "neutral"),
            ({"id": "owned:receipt:r", "outcome": "SUCCEEDED", "scientific_support": "UNKNOWN"}, "success"),
            ({"id": "receipt.r", "outcome": "FAILED"}, "failure"),
            ({"id": "owned:receipt:r", "outcome": "SUCCEEDED", "run_status": "FAILED"}, "neutral"),
            ({"id": "owned:receipt:r", "outcome": "SUCCEEDED", "run_status": []}, "neutral"),
            ({"id": "owned:receipt:r", "outcome": "SUCCEEDED", "status": "UNKNOWN"}, "neutral"),
            ({"id": "owned:receipt:r", "outcome": "SUCCEEDED", "status": "CONTRADICTED"}, "neutral"),
            ({"id": "run.r.succeeded", "owned_fact": {"value": True, "reliable": True}, "status": "CONTRADICTED"}, "neutral"),
            ({"id": "run.r.succeeded", "owned_fact": {"value": True, "reliable": True}, "status": "UNKNOWN"}, "neutral"),
            ({"id": "owned:fact:loss", "owned_fact": {"value": False, "reliable": True}}, "neutral"),
            ({"id": "claim"}, "success"),
            ({"id": "claim", "status": "CONTRADICTED"}, "failure"),
            ({"id": "claim", "status": "UNKNOWN"}, "neutral"),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                row = {"status": "SUPPORTED", "source": "synthetic:status", **fields}
                before = deepcopy(row)
                shown = display_record(row)
                self.assertEqual(shown["outline"]["state"], expected)
                self.assertIn("不表示科研证明", shown["outline"]["basis"])
                self.assertEqual(row, before)
                graph = {"schema": 1, "nodes": [row], "hyperedges": [], "goals": [row["id"]]}
                result = graph_view(graph, "synthetic:status")
                canvas = self.payload(render_html(result))["display"]["nodes"][row["id"]]
                replica = replica_view(result)["nodes"]["c0"]["rds"]
                self.assertEqual(canvas["outline"], shown["outline"])
                self.assertEqual(replica["outline"], shown["outline"])
                self.assertEqual(result["graph"], graph)
        self.assertIn("false", display_record({"id": "run.r.failed", "status": "SUPPORTED",
                                               "owned_fact": {"value": False, "reliable": True}})["outline"]["basis"])
        for ident in ("receipt.geometry", "observation.geometry", "run.geometry.succeeded"):
            row = {"id": ident, "status": "SUPPORTED", "claim": "Human readable statement"}
            shown = display_record(row)
            self.assertEqual(shown["kind"], "声明")
            self.assertEqual(shown["label"], row["claim"])
            self.assertEqual(display_record({"id": ident, "status": "SUPPORTED"})["label"], ident)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rds-hypergraph-view-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def export(self, *arguments):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_hypergraph_view.py"),
                               "--root", str(self.root), *map(str, arguments)],
                              capture_output=True, text=True, encoding="utf-8", timeout=20)

    @staticmethod
    def payload(page):
        return json.loads(re.search(r'<script id="snapshot" type="application/json">(.*?)</script>',
                                    page, re.S).group(1))

    def test_saved_tms_without_execution_contract_exports_read_only(self):
        spec = example()
        sha = save(self.root, spec, expected=None)
        originals = {p.relative_to(self.root): p.read_bytes()
                     for p in self.root.rglob("*") if p.is_file()}
        output = self.root / "研究超图.html"
        result = self.export("--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = self.payload(output.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["status"], "AVAILABLE")
        self.assertNotIn("state", snapshot)
        self.assertNotIn("receipts", snapshot)
        self.assertEqual(snapshot["snapshot_sha256"], sha)
        self.assertEqual(snapshot["graph"], spec)
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*")
                          if p.is_file() and p != output}, originals)

    def test_large_export_reports_analysis_status_to_cli(self):
        output = self.root / "large.html"
        result = self.export("--demo", "--large", "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout).get("analysis_status"), "NOT_RUN_LARGE_GRAPH")

    def test_resolution_loops_are_bounded_cli_errors_and_unavailable_reads(self):
        from rds_hypergraph_view import main
        original_resolve = Path.resolve
        loop = self.root / "loop"
        def resolve(path, *args, **kwargs):
            if path == loop:
                raise RuntimeError("Symlink loop from synthetic path-resolution fixture")
            return original_resolve(path, *args, **kwargs)
        cases = (["--root", str(loop)], ["--output", str(loop)],
                 ["--hypergraph", str(loop)], ["--demo", "--readable-json", str(loop)])
        for arguments in cases:
            with self.subTest(arguments=arguments), patch.object(Path, "resolve", resolve):
                stderr = io.StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                    main(arguments)
                self.assertEqual(error.exception.code, 2)
                self.assertIn("Symlink loop", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
        with patch.object(Path, "resolve", resolve):
            self.assertEqual(read_graph(self.root, loop)["status"], "UNAVAILABLE")
            self.assertEqual(read_graph(loop)["status"], "UNAVAILABLE")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_graph_reader_leaves_coexisting_reference_runner_untouched(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_rds_dashboard import DashboardTests
        fixture = DashboardTests()
        self.addCleanup(fixture.doCleanups)
        root, path = fixture.project()
        save(root, example(), expected=None)
        before = path.read_bytes()
        snapshot = read_graph(root)
        self.assertEqual(snapshot["status"], "AVAILABLE")
        self.assertNotIn("receipts", snapshot)
        self.assertEqual(path.read_bytes(), before)

    def test_explicit_bom_fenced_input_and_repairs_without_creating_state(self):
        source = self.root / "声明.json"
        # Use the existing adapter; retain its repair report for inspection.
        source.write_text("```json\n" + json.dumps(example()) + "\n```", encoding="utf-8-sig")
        output = self.root / "view.html"
        result = self.export("--hypergraph", source, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = self.payload(output.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["status"], "AVAILABLE")
        self.assertTrue(Path(snapshot["source"]).samefile(source))
        self.assertIn("removed JSON code fence", str(snapshot["input_review"]))
        self.assertFalse((self.root / ".rds").exists())

    def test_declared_status_and_source_remain_distinct_from_closure(self):
        spec = example()
        before = deepcopy(spec)
        with patch("rds_hypergraph.audit_sources", side_effect=AssertionError("No source audit")):
            result = graph_view(spec, "synthetic")
        self.assertEqual(spec, before)
        comparison = next(n for n in result["graph"]["nodes"] if n["id"] == "fair-comparison")
        self.assertEqual(comparison["status"], "UNKNOWN")
        self.assertIn("fair-comparison", result["analysis"]["declared_supported_closure"])
        self.assertNotIn("research-goal", result["analysis"]["declared_supported_closure"])
        self.assertEqual(result["receipt_audit"], "NOT_RUN")
        self.assertEqual(result["assurance"], "INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF")
        routes = [e for e in result["graph"]["hyperedges"] if e["conclusion"] == "task-benefit"]
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0]["premises"], ["fair-comparison", "baseline-evidence"])

    def test_corrupt_saved_cas_is_unavailable_without_modification(self):
        save(self.root, example(), expected=None)
        saved = current(self.root)
        cas = self.root / saved["map"]["path"]
        cas.write_bytes(b"corrupt synthetic CAS")
        result = read_graph(self.root)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertIn("integrity failure", result["reason"].lower())
        self.assertEqual(cas.read_bytes(), b"corrupt synthetic CAS")

    def test_saved_export_replaces_hardlink_without_writing_ledger_or_cas(self):
        save(self.root, example(), expected=None)
        saved = current(self.root)
        for source in (self.root / ".rds/project.sqlite3", self.root / saved["map"]["path"]):
            with self.subTest(source=source.name):
                output = self.root / "alias.html"
                output.unlink(missing_ok=True)
                os.link(source, output)
                before = source.read_bytes()
                result = self.export("--output", output)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(source.read_bytes(), before)
                self.assertFalse(source.samefile(output))
                self.assertEqual(self.payload(output.read_text(encoding="utf-8"))["graph"], example())
                self.assertFalse(list(self.root.glob(".rds-hypergraph-*.tmp")))

    def test_linked_ledger_rejects_both_output_aliases_before_writing(self):
        project, ledger = self.root / "project", self.root / "storage/.rds"
        project.mkdir()
        ledger.parent.mkdir()
        save(ledger.parent, example(), expected=None)
        link = project / ".rds"
        if os.name == "nt":
            created = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(ledger)],
                                     capture_output=True, text=True)
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        else:
            link.symlink_to(ledger, target_is_directory=True)
        self.root = project
        (ledger / "existing.html").write_bytes(b"original ledger-side file")
        originals = {p.relative_to(ledger): p.read_bytes() for p in ledger.rglob("*") if p.is_file()}
        for directory in (link, ledger):
            for suffix in ("existing.html", "new/subdir/view.html"):
                with self.subTest(directory=directory, suffix=suffix):
                    result = self.export("--output", directory / suffix)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("outside the .rds ledger", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual({p.relative_to(ledger): p.read_bytes()
                                      for p in ledger.rglob("*") if p.is_file()}, originals)
                    self.assertFalse((ledger / "new").exists())
                    self.assertFalse(list(ledger.rglob(".rds-hypergraph-*.tmp")))

    def test_explicit_graph_identity_tracks_contents_without_mutating_raw(self):
        from rds_hypergraph_readable import graph_digest
        source = self.root / "graph.json"
        spec = example()
        source.write_text(json.dumps(spec), encoding="utf-8")
        first = read_graph(self.root, source)
        self.assertIsNone(first["snapshot_sha256"])
        self.assertEqual(first["graph_sha256"], graph_digest(spec))
        self.assertEqual(read_graph(self.root, source)["graph_sha256"], first["graph_sha256"])
        replacement = deepcopy(spec)
        replacement["nodes"][0]["label"] = "Replacement graph"
        source.write_text(json.dumps(replacement), encoding="utf-8")
        changed = read_graph(self.root, source)
        self.assertNotEqual(changed["graph_sha256"], first["graph_sha256"])
        self.assertEqual(changed["graph"], replacement)
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), replacement)
        self.assertFalse((self.root / ".rds").exists())

    def test_atomic_export_failure_preserves_output_and_cleans_temporary(self):
        output = self.root / "existing.html"
        output.write_bytes(b"original")
        with patch("rds_hypergraph_view.os.replace", side_effect=OSError("replace unavailable")):
            with self.assertRaisesRegex(OSError, "replace unavailable"):
                write_html_atomic(output, "new page")
        self.assertEqual(output.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".rds-hypergraph-*.tmp")))

    def test_malformed_limits_fail_cleanly_without_output(self):
        source, output = self.root / "invalid.json", self.root / "invalid.html"
        for limits in (None, [], "invalid", 1, True):
            with self.subTest(limits=limits):
                spec = {**example(), "limits": limits}
                source.write_text(json.dumps(spec), encoding="utf-8")
                result = read_graph(self.root, source)
                self.assertEqual(result["status"], "UNAVAILABLE")
                self.assertIn("limits must be an object", result["reason"])
                cli = self.export("--hypergraph", source, "--output", output)
                self.assertNotEqual(cli.returncode, 0)
                self.assertNotIn("Traceback", cli.stderr)
                self.assertFalse(output.exists())
                self.assertEqual(json.loads(source.read_text(encoding="utf-8")), spec)

    def test_missing_and_malformed_graph_never_become_synthetic_data(self):
        self.assertEqual(read_graph(self.root)["status"], "MISSING")
        self.assertFalse((self.root / ".rds").exists())
        source, output = self.root / "bad.json", self.root / "bad.html"
        for content in ('{"nodes": [], "nodes": []}', '{"nodes": NaN}', '[]', '{broken'):
            with self.subTest(content=content):
                source.write_text(content, encoding="utf-8")
                result = self.export("--hypergraph", source, "--output", output)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                self.assertFalse((self.root / ".rds").exists())

    def test_oversize_input_is_rejected_before_parsing(self):
        source = self.root / "large.json"
        with source.open("wb") as handle:
            handle.truncate(8 * 1024 * 1024 + 1)
        result = read_graph(self.root, source)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertIn("exceeds 8 MiB", result["reason"])

    def test_display_limit_is_explicit_and_does_not_analyze_partial_graph(self):
        spec = {"nodes": [{"id": "n", "status": "UNKNOWN", "source": "synthetic"}],
                "hyperedges": [{"id": f"e{i}", "premises": [], "conclusion": "n",
                                "status": "PROPOSED", "source": "synthetic"} for i in range(8193)],
                "goals": [], "limits": {"max_hyperedges": 16384}}
        before = deepcopy(spec)
        with patch("rds_hypergraph_view.analyze_hypergraph", side_effect=AssertionError("Over cap")):
            result = graph_view(spec, "synthetic")
        self.assertEqual(result["status"], "DISPLAY_LIMIT")
        self.assertEqual(result["counts"]["hyperedges"], 8193)
        self.assertIsNone(result["graph"])
        self.assertIsNone(result["analysis"])
        self.assertEqual(spec, before)

    def test_incomplete_analysis_retains_limit_and_original_declarations(self):
        spec = example()
        spec["limits"] = {"max_combinations": 1}
        result = graph_view(spec, "synthetic")
        self.assertTrue(result["analysis"]["truncated"])
        self.assertFalse(result["analysis"]["goals"]["research-goal"]["blocker_sets_complete"])
        self.assertEqual(result["graph"], spec)

    def test_graph_fields_cannot_escape_json_or_execute_html(self):
        attack = '</script><img src=x onerror=alert(1)>&\u2028\u2029__GRAPH_JS__'
        spec = example()
        spec["nodes"][0].update(label=attack, source=attack)
        snapshot = graph_view(spec, "synthetic")
        page = render_html(snapshot)
        self.assertEqual(self.payload(page)["graph"]["nodes"][0]["label"], attack)
        self.assertNotIn('<img src=x', page)
        self.assertNotIn("innerHTML", page)
        self.assertIn("connect-src 'none'", page)

    def test_input_cannot_be_overwritten_and_demo_cannot_mask_explicit_data(self):
        source = self.root / "graph.html"
        original = json.dumps(example())
        source.write_text(original, encoding="utf-8")
        result = self.export("--hypergraph", source, "--output", source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        result = self.export("--hypergraph", source, "--demo", "--output", self.root / "view.html")
        self.assertNotEqual(result.returncode, 0)

    def test_demo_graph_is_available_and_marked_synthetic_without_runs(self):
        result = read_graph(self.root, demo=True)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertTrue(result["demo"])
        self.assertTrue(all(n["source"].startswith("synthetic:") for n in result["graph"]["nodes"]))
        self.assertNotIn("receipts", result)
        self.assertFalse((self.root / ".rds").exists())

    def test_hardlink_output_alias_is_rejected_without_touching_input(self):
        source, output = self.root / "snapshot.json", self.root / "alias.html"
        original = json.dumps(example()).encode("utf-8")
        source.write_bytes(original)
        os.link(source, output)
        self.assertTrue(source.samefile(output))
        result = self.export("--hypergraph", source, "--output", output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not overwrite", result.stderr)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(output.read_bytes(), original)
        self.assertFalse((self.root / ".rds").exists())

    @unittest.skipUnless(os.name == "nt", "Windows 8.3 file aliases")
    def test_windows_short_source_path_reports_the_same_file(self):
        import ctypes
        source = self.root / "long-synthetic-dependency-snapshot.json"
        original = json.dumps(example()).encode("utf-8")
        source.write_bytes(original)
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(source), buffer, len(buffer))
        self.assertGreater(length, 0)
        short = Path(buffer.value)
        self.assertTrue(short.samefile(source))
        if str(short).lower() == str(source).lower():
            self.skipTest("8.3 name generation disabled on this volume")
        output = self.root / "short-path-view.html"
        result = self.export("--hypergraph", short, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = self.payload(output.read_text(encoding="utf-8"))
        self.assertTrue(Path(snapshot["source"]).samefile(source))
        self.assertEqual(source.read_bytes(), original)

    def test_thousand_node_graph_is_complete_without_combinatorial_analysis(self):
        spec = large_demo()
        with patch("rds_hypergraph_view.analyze_hypergraph", side_effect=AssertionError("Large analysis")):
            result = graph_view(spec, "synthetic", demo=True)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["counts"]["nodes"], 1200)
        self.assertGreater(result["counts"]["hyperedges"], 1000)
        self.assertEqual(result["graph"], spec)
        self.assertEqual(result["analysis_status"], "NOT_RUN_LARGE_GRAPH")
        self.assertIsNone(result["analysis"])

    def test_large_demo_cli_is_standalone_and_reports_actual_counts(self):
        output = self.root / "large.html"
        result = self.export("--demo", "--large", "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["counts"]["nodes"], 1200)
        snapshot = self.payload(output.read_text(encoding="utf-8"))
        self.assertTrue(snapshot["demo"])
        self.assertNotIn("state", snapshot)
        self.assertNotIn("receipts", snapshot)
        self.assertFalse((self.root / ".rds").exists())

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to execute the actual force worker")
    def test_actual_worker_distinguishes_attraction_repulsion_and_no_force(self):
        # Execute the shipped worker, not a Python reimplementation. Isolate a
        # two-node relation with background repulsion and center disabled.
        start, end = JS.index(" function forceWorker(){"), JS.index(" function runLayout(")
        worker = JS[start:end]
        script = self.root / "force-check.cjs"
        script.write_text("const vm=require('node:vm'); const {performance}=require('node:perf_hooks');\n"
                          + "const library=" + json.dumps(D3_BUNDLE) + ";\nconst worker=" + json.dumps(worker) + ";\n" + r'''
function execute(mode,strength=1,dynamic=false){
 let output,closed=false;const queue=new Map();let timer=0;
 const scope={performance,Float32Array,Map,Math,Date,
  setTimeout:fn=>{queue.set(++timer,fn);return timer},clearTimeout:id=>queue.delete(id),
  setInterval:()=>0,clearInterval:()=>{},postMessage:x=>output=x,close:()=>{closed=true}};
 vm.createContext(scope);vm.runInContext(library+';'+worker+';forceWorker();',scope);
 scope.onmessage({data:{positions:new Float32Array([0,0,200,0]),
  links:new Float32Array([0,1,strength,mode<0?350:60,mode]),degree:[1,1],
  options:{center:0,repulsion:0,spring:.08,length:65},dynamic,warm:true}});
 for(let steps=0;dynamic&&!closed&&queue.size&&steps<500;steps++){
  const [id,fn]=queue.entries().next().value;queue.delete(id);fn();
 }
 if(dynamic&&(!closed||!output.done||output.alpha>=.001||output.ticks>310))throw Error('Simulation did not settle');
 if((!output.ready&&!output.done)||!Array.from(output.positions).every(Number.isFinite))throw Error('Invalid worker result');
 return Math.abs(output.positions[2]-output.positions[0]);
}
const result={attract:execute(1),repel:execute(-1),none:execute(0),zeroStrength:execute(-1,0),settledAttract:execute(1,1,true),settledRepel:execute(-1,1,true)};
if(!(result.attract<100&&result.repel>300&&result.none===200&&result.zeroStrength===200&&result.settledAttract<100&&result.settledRepel>300))throw Error(JSON.stringify(result));
console.log(JSON.stringify(result));
''', encoding="utf-8")
        result = subprocess.run([shutil.which("node"), str(script)], capture_output=True,
                                text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        observations = json.loads(result.stdout)
        self.assertLess(observations["attract"], observations["none"])
        self.assertGreater(observations["repel"], observations["none"])


class ReplicaViewTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for renderer transport contract")
    def test_status_outline_transport_contract_without_local_assets(self):
        changes = []
        def capture(source, old, new):
            changes.append((old, new))
            return source
        with patch("rds_hypergraph_view._replace_once", side_effect=capture):
            patch_replica_renderer("independent contract fixture")
        methods = next(new for old, new in changes if old.startswith("getSize()"))
        self.assertTrue(any("statusOutline" in new for old, new in changes if old == "['circle', 'highlight', 'text']"))
        self.assertTrue(any("statusOutline.visible=false" in new for old, new in changes if old.startswith("const r = this.r, { x, y }")))
        # Production methods executed against an independently authored Graphics
        # transport. Full pinned GNode lifecycle is separately bound locally.
        section = "class ContractNode { constructor(r){this.r=r;this.weight=0;} " + methods + " };globalThis.ContractNode=ContractNode;"
        driver = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
class Graphic {
 constructor(){this.visible=true;this.clears=0;this.scale={set:v=>this.scale.value=v};this.anchor={set(){}};}
 clear(){this.clears++;return this}beginFill(){return this}endFill(){return this}
 lineStyle(){return this}drawCircle(){return this}drawPolygon(){return this}drawRect(){return this}on(){return this}
 destroy(){this.destroyed=true}
}
const children=new Set(),hanger={addChild:c=>{children.add(c);c.parent=hanger},removeChild:c=>{children.delete(c);c.parent=null}};
const scope={PIXI:{Graphics:Graphic}};
vm.createContext(scope);
''' + "\nvm.runInContext(" + json.dumps(section) + ",scope);\n" + r'''
const r={opts:{nodeSize:1},hanger};
const n=new scope.ContractNode(r);n.type='';n.rds={shape:'square',size:1,radius:12,outline:{color:'#39b872'}};
const render=(x=20,y=30,ns=1,alpha=1,visible=true)=>n.renderStatusOutline(x,y,n.getSize(),ns,alpha,visible);
render();const outline=n.statusOutline;assert.ok(outline);assert.equal(outline.tint,0x39b872);assert.equal(outline.eventMode,'none');
assert.ok(outline.zIndex>1&&outline.zIndex<2,'status is above body/hover ring and below labels');
assert.equal(outline.scale.value,.12);assert.equal(children.size,1);
const clears=outline.clears;
for(let i=0;i<20;i++){render(20+i,30-i);assert.equal(outline.x,20+i);assert.equal(outline.y,30-i);}
assert.equal(outline.clears,clears,'static shape and dragging do not redraw outline geometry');
r.opts.nodeSize=1.8;n.rds.radius=26;render(0,0,2,.2,false);
assert.ok(Math.abs(outline.scale.value-.936)<1e-9);assert.equal(outline.alpha,.2);assert.equal(outline.visible,false);assert.equal(outline.clears,clears);
for(const shape of ['circle','ring','triangle','hexagon','square']){n.rds.shape=shape;render();assert.equal(n.statusOutlineShape,shape);assert.equal(outline.visible,true);}
n.rds.outline.color='#e65b63';render();assert.equal(outline.tint,0xe65b63);
n.rds.outline=null;render();assert.equal(outline.visible,false);
const hub=new scope.ContractNode(r);hub.type='hyperedge';hub.rds={outline:{color:'#e65b63'}};hub.renderStatusOutline(0,0,8,1,1,true);assert.equal(hub.statusOutlineShape,'diamond');
console.log('Production outline methods: independent Graphics transport, cached geometry, transform/visibility/tint contract PASS');
'''
        run = subprocess.run([shutil.which("node"), "-"], input=driver, capture_output=True,
                             text=True, encoding="utf-8", timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        print(run.stdout.strip())

    def test_and_or_routes_remain_separate_with_original_status(self):
        spec=example()
        before=deepcopy(spec)
        result=graph_view(spec,"fixture")
        view=replica_view(result)
        hubs=[n for n in view["nodes"].values() if n["type"]=="hyperedge"]
        self.assertEqual(len(hubs),len(spec["hyperedges"]))
        self.assertEqual(len(view["links"]),sum(len(e["premises"])+1 for e in spec["hyperedges"]))
        for i,edge in enumerate(spec["hyperedges"]):
            legs=[l for l in view["links"] if l[2].get("hyperedge")==f"h{i}"]
            self.assertEqual(len(legs),len(edge["premises"])+1)
            self.assertEqual(sum(l[2]["arrow"] for l in legs),1)
            self.assertEqual(len(view["nodes"][f"h{i}"]["rds"]["members"]),len(edge["premises"])+1)
        self.assertEqual(spec,before)
        self.assertEqual(result["graph"],before)

    def test_provenance_uses_exact_bindings_not_text_or_status(self):
        spec={"schema":1,"nodes":[
            {"id":"owned:run:r","status":"SUPPORTED","source":"run r","manifest_sha256":"m"},
            {"id":"owned:receipt:r","status":"SUPPORTED","source":"receipt r","receipt_sha256":"b"*64},
            {"id":"artifact","status":"SUPPORTED","source":{"file":"out/a.json","sha256":"a"*64,"locator":"artifact"},"run_id":"r","interpretation":"UNPARSED"},
            {"id":"fact","status":"UNKNOWN","source":{"file":"out/a.json","path":"out/a.json","sha256":"a"*64,"receipt_id":"b"*64,"locator":"fact"}},
            {"id":"similar","status":"SUPPORTED","source":"out/a.json receipt-sha"},
            {"id":"other-path","status":"UNKNOWN","source":{"file":"out/b.json","path":"out/b.json","sha256":"a"*64,"locator":"different"}},
            {"id":"bad-extra","status":"UNKNOWN","source":{"locator":"unknown","receipt_id":{}},"run_id":[]}],
            "hyperedges":[{"id":"done","premises":["owned:run:r"],"conclusion":"owned:receipt:r","status":"SUPPORTED","source":"receipt"}],"goals":["fact"]}
        result=graph_view(spec,"fixture");view=replica_view(result)
        provenance=[l for l in view["links"] if l[2]["family"]=="provenance"]
        self.assertEqual({(s,t) for s,t,_ in provenance},{("c0","c1"),("c0","c2"),("c1","c2"),("c0","c3"),("c2","c3")})
        self.assertTrue(all(l[2]["binding"] for l in provenance))
        self.assertIsNone(view["nodes"]["c4"]["rds"]["group"])
        self.assertEqual(view["nodes"]["c3"]["rds"]["group"],"r")
        self.assertEqual(result["graph"],spec)

    @staticmethod
    def record_fixture(legacy=False):
        nodes = []
        for run, receipt in (("a", "b" * 64), ("b", "c" * 64)):
            nodes.extend([
                {"id": f"owned:run:{run}", "status": "SUPPORTED", "source": "fixture",
                 "record_kind": "run", "run_id": run, "manifest_sha256": "d" * 64},
                {"id": f"owned:receipt:{run}", "status": "SUPPORTED", "source": "fixture",
                 "record_kind": "receipt", "run_id": run, "receipt_id": receipt, "receipt_sha256": receipt},
                {"id": f"artifact:{run}", "status": "SUPPORTED", "record_kind": "artifact", "run_id": run,
                 "receipt_id": receipt, "interpretation": "UNPARSED",
                 "source": {"file": "out/shared.json", "sha256": "a" * 64, "locator": "fixture"}}])
        nodes.extend([
            {"id": "observation:a", "status": "UNKNOWN", "record_kind": "observation", "run_id": "a",
             "source": {"file": "out/shared.json", "path": "out/shared.json", "sha256": "a" * 64, "receipt_id": "b" * 64, "locator": "fixture"}},
            {"id": "declared:a", "status": "UNKNOWN", "record_kind": "declared_output", "run_id": "a",
             "output_path": "out/pending.json", "interpretation": "PENDING", "source": "fixture"},
            {"id": "ordinary-goal", "status": "UNKNOWN", "source": "fixture"}])
        if legacy:
            for row in nodes:
                row.pop("record_kind", None)
                row.pop("receipt_id", None)
                if row["id"].startswith(("owned:run:", "owned:receipt:", "observation:")):
                    row.pop("run_id", None)
        edges = [{"id": "claim", "premises": ["owned:run:a"], "conclusion": "ordinary-goal",
                  "status": "PROPOSED", "source": "fixture"},
                 {"id": "completion", "premises": ["owned:run:a"], "conclusion": "owned:receipt:a",
                  "status": "SUPPORTED", "source": "fixture"}]
        return {"schema": 1, "nodes": nodes, "hyperedges": edges, "goals": ["ordinary-goal", "observation:a"]}

    def test_typed_and_legacy_artifacts_do_not_cross_runs_or_supply_support(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.record_fixture(legacy)
                result = graph_view(spec, "fixture")
                before = deepcopy(result)
                view = replica_view(result)
                pairs = {(r["from"], r["to"]) for r in view["record_relations"] if r["kind"] == "artifact_observation"}
                self.assertEqual(pairs, {("artifact:a", "observation:a")})
                declaration = next(l for l in view["links"] if l[2]["family"] == "declaration")
                self.assertEqual(declaration[2]["relation"], "声明输出")
                self.assertEqual(view["declaration_count"], 1)
                self.assertTrue(all(r["scientific_support"] == "UNKNOWN" for r in view["record_relations"]))
                self.assertIsNone(view["nodes"]["c8"]["rds"]["group"])
                self.assertEqual(view["nodes"]["c1"]["rds"]["group"], "a")
                self.assertIsNone(view["nodes"]["c4"]["rds"]["group"])
                self.assertIsNone(view["nodes"]["h0"]["rds"]["group"])
                self.assertEqual(result, before)
                self.assertNotIn("observation:a", result["analysis"]["declared_supported_closure"])

    def test_legacy_display_uses_effective_kind_without_rewriting_raw_records(self):
        from rds_hypergraph_readable import agent_input, with_readable
        spec = self.record_fixture(legacy=True)
        result = graph_view(spec, "fixture")
        before = deepcopy(result)
        display = display_records(result)
        expected = {"artifact:a": "产物", "artifact:b": "产物", "observation:a": "观测", "declared:a": "产物"}
        for ident, kind in expected.items():
            self.assertEqual(display["nodes"][ident]["kind"], kind)
        self.assertEqual(result, before)
        readable = agent_input(result)["readable"]
        readable["nodes"]["artifact:a"] = {"title": "消元步骤输出", "summary": "记录候选计算。"}
        annotated = with_readable(result, readable)
        self.assertEqual(display_records(annotated)["nodes"]["artifact:a"]["label"], "消元步骤输出")
        for ident, kind in expected.items():
            visual = next(n for n in replica_view(annotated)["nodes"].values() if n["rds"].get("record") == ident)
            self.assertEqual(visual["rds"]["kind"], kind)
        payload = HypergraphViewTests.payload(render_html(annotated))
        self.assertEqual(payload["display"]["nodes"]["observation:a"]["kind"], "观测")
        self.assertEqual(payload["graph"], before["graph"])

    def test_rejected_provenance_keeps_type_and_human_diagnostics(self):
        spec = self.record_fixture(legacy=True)
        duplicate = deepcopy(spec["nodes"][1])
        duplicate["id"] = "receipt-duplicate"
        duplicate["record_kind"] = "receipt"
        duplicate["run_id"] = "a"
        spec["nodes"].append(duplicate)
        result = graph_view(spec, "fixture")
        before = deepcopy(result)
        display = display_records(result)
        info = display["nodes"]["observation:a"]
        self.assertEqual(info["kind"], "观测")
        self.assertGreater(info["diagnostics"]["count"], 0)
        self.assertIn("多个匹配", str(info["diagnostics"]))
        self.assertGreater(display["diagnostics"]["count"], 0)
        self.assertEqual(result, before)
        bounded = readable_diagnostics([{"field": "run_id", "reason": "AMBIGUOUS_BINDING", "candidates": [str(i) for i in range(12)]} for _ in range(11)])
        self.assertEqual(len(bounded["items"]), 8)
        self.assertEqual(bounded["omitted"], 3)
        self.assertEqual(bounded["items"][0]["omitted_candidates"], 4)

    def test_node_and_edge_same_id_keep_independent_status_and_readable_text(self):
        from rds_hypergraph_readable import agent_input, with_readable
        for node_status, edge_status, outline, status_text in (("SUPPORTED", "CONTRADICTED", "failure", "声明反驳"), ("CONTRADICTED", "PROPOSED", "neutral", "候选")):
            with self.subTest(node_status=node_status):
                spec = {"nodes": [{"id": "same", "status": node_status, "source": "fixture"}, {"id": "goal", "status": "UNKNOWN", "source": "fixture"}],
                        "hyperedges": [{"id": "same", "premises": ["same"], "conclusion": "goal", "status": edge_status, "source": "fixture"}], "goals": ["goal"]}
                result = graph_view(spec, "fixture")
                readable = agent_input(result)["readable"]
                readable["nodes"]["same"] = {"title": "前提声明"}
                readable["hyperedges"]["same"] = {"title": "推导关系"}
                result = with_readable(result, readable)
                before = deepcopy(result)
                display = display_records(result)
                self.assertEqual(display["nodes"]["same"]["label"], "前提声明")
                self.assertEqual(display["hyperedges"]["same"]["label"], "推导关系")
                self.assertEqual(display["hyperedges"]["same"]["outline"]["state"], outline)
                self.assertEqual(display["hyperedges"]["same"]["status_text"], status_text)
                hub = replica_view(result)["nodes"]["h0"]["rds"]
                self.assertEqual(hub["outline"]["state"], outline)
                self.assertEqual(hub["status_text"], status_text)
                payload = HypergraphViewTests.payload(render_html(result))
                self.assertEqual(payload["display"]["hyperedges"]["same"]["outline"]["state"], outline)
                self.assertEqual(payload["graph"], spec)
                self.assertEqual(result, before)

    def test_fallback_diagnoses_missing_required_record_identities(self):
        required = {"run": {"run_id"}, "receipt": {"run_id", "receipt_id"},
                    "artifact": {"run_id", "path", "sha256"}, "declared_output": {"run_id", "path"},
                    "lifecycle_fact": {"run_id"}, "observation": {"run_id"}}
        for kind, fields in required.items():
            with self.subTest(kind=kind), patch("rds_hypergraph.record_topology", None, create=True):
                spec = {"nodes": [{"id": "opaque", "status": "UNKNOWN", "source": "fixture", "record_kind": kind}],
                        "hyperedges": [], "goals": ["opaque"]}
                result = graph_view(spec, "fixture")
                before = deepcopy(result)
                view = replica_view(result)
                self.assertEqual({i["field"] for i in view["record_topology"]["issues"]
                                  if i["reason"] == "MISSING_BINDING"}, fields)
                self.assertEqual(view["record_relations"], [])
                self.assertEqual(result, before)
                self.assertEqual(result["analysis"]["goals"]["opaque"]["status"], "UNKNOWN")

    @staticmethod
    def field_fixture():
        spec = ReplicaViewTests.record_fixture()
        spec["nodes"].append({"id": "lifecycle:a", "status": "UNKNOWN", "source": "fixture",
                              "record_kind": "lifecycle_fact", "run_id": "a"})
        return spec

    def test_fallback_unreferenced_run_origins_report_ambiguity_without_self_links(self):
        spec = {"nodes": [{"id": ident, "status": "UNKNOWN", "source": "fixture",
                            "record_kind": "run", "run_id": "same-run"}
                           for ident in ("run-a", "run-b")], "hyperedges": [], "goals": ["run-a"]}
        original = deepcopy(spec)
        with patch("rds_hypergraph.record_topology", None, create=True):
            result = graph_view(spec, "synthetic unreferenced origins")
            before = deepcopy(result)
            view = replica_view(result)
            self.assertEqual([(i["node_id"], i["field"], i["reason"], i["candidates"])
                              for i in view["record_topology"]["issues"]],
                             [(ident, "run_id", "AMBIGUOUS_BINDING", ["run-a", "run-b"])
                              for ident in ("run-a", "run-b")])
            self.assertEqual(view["record_relations"], [])
            self.assertFalse(any(link[0] == link[1] for link in view["links"]))
            self.assertEqual(HypergraphViewTests.payload(render_html(result))["graph"], original)
            self.assertEqual(result, before)
            self.assertEqual(spec, original)
            self.assertEqual(result["analysis"]["goals"]["run-a"]["status"], "UNKNOWN")

    def test_fallback_run_origin_candidates_are_bounded_with_bad_extras_and_consumer(self):
        for consumer in (False, True):
            with self.subTest(consumer=consumer), patch("rds_hypergraph.record_topology", None, create=True):
                spec = {"nodes": [{"id": f"run-{i}", "status": "UNKNOWN", "source": "fixture",
                                    "record_kind": "run", "run_id": "same-run"} for i in range(5)],
                        "hyperedges": [], "goals": ["run-0"]}
                spec["nodes"][4]["receipt_id"] = []
                if consumer:
                    spec["nodes"].append({"id": "observation", "status": "UNKNOWN", "source": "fixture",
                                          "record_kind": "observation", "run_id": "same-run"})
                    spec["goals"] = ["observation"]
                original = deepcopy(spec)
                result = graph_view(spec, "synthetic bounded origins")
                before = deepcopy(result)
                view = replica_view(result)
                ambiguous = [i for i in view["record_topology"]["issues"] if i["reason"] == "AMBIGUOUS_BINDING"]
                self.assertEqual({i["node_id"] for i in ambiguous}, {n["id"] for n in spec["nodes"]})
                self.assertTrue(all(i["field"] == "run_id" and i["candidates"] == ["run-0", "run-1", "run-2"]
                                    and i["omitted_candidates"] == 2 for i in ambiguous))
                self.assertEqual(view["record_relations"], [])
                self.assertEqual(HypergraphViewTests.payload(render_html(result))["graph"], original)
                self.assertEqual(result, before)
                self.assertEqual(spec, original)
                self.assertEqual(result["analysis"]["goals"][spec["goals"][0]]["status"], "UNKNOWN")

    def test_fallback_review_four_production_counterexamples_keep_membership(self):
        for position, field, relation in ((6, "output_path", "run_observation"),
                                         (6, "receipt_id", "run_observation"),
                                         (9, "receipt_id", "run_lifecycle_fact"),
                                         (7, "receipt_id", "declared_output")):
            with self.subTest(position=position, field=field), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.field_fixture()
                spec["nodes"][position][field] = "out/conflict.json" if field == "output_path" else []
                original = deepcopy(spec)
                result = graph_view(spec, "synthetic production fallback")
                before = deepcopy(result)
                view = replica_view(result)
                self.assertIn(relation, [r["kind"] for r in view["record_relations"]
                                        if r["to"] == spec["nodes"][position]["id"]])
                page = render_html(result)
                self.assertEqual(HypergraphViewTests.payload(page)["graph"], original)
                self.assertEqual(result, before)
                self.assertEqual(spec, original)
                self.assertTrue(all(r["scientific_support"] == "UNKNOWN" for r in view["record_relations"]))

    def test_fallback_field_matrix_preserves_inference_and_required_diagnostics(self):
        cases = ((1, "receipt_id", [], {"run_receipt"}),
                 (1, "output_path", [], {"run_receipt"}),
                 (2, "artifact_path", [], {"run_artifact", "receipt_artifact"}),
                 (2, "receipt_id", [], {"run_artifact"}),
                 (6, "output_path", [], {"run_observation"}),
                 (6, "receipt_id", [], {"run_observation"}),
                 (7, "receipt_id", [], {"declared_output"}),
                 (7, "output_path", [], set()),
                 (9, "receipt_id", [], {"run_lifecycle_fact"}),
                 (9, "output_path", [], {"run_lifecycle_fact"}),
                 (2, "artifact_path", "bad\0path", {"run_artifact", "receipt_artifact"}),
                 *((position, "run_id", [], set()) for position in (1, 2, 6, 7, 9)))
        baseline = graph_view(self.field_fixture(), "fixture")
        for position, field, value, expected in cases:
            with self.subTest(position=position, field=field), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.field_fixture()
                target = spec["nodes"][position]
                target[field] = value
                result = graph_view(spec, "fixture")
                before = deepcopy(result)
                view = replica_view(result)
                self.assertEqual({r["kind"] for r in view["record_relations"] if r["to"] == target["id"]}, expected)
                for field in baseline["analysis"].keys() - {"reported_nodes"}:
                    self.assertEqual(result["analysis"][field], baseline["analysis"][field], field)
                self.assertEqual(result, before)
        required = {"run": {"run_id"}, "receipt": {"run_id", "receipt_id"},
                    "artifact": {"run_id", "path", "sha256"}, "declared_output": {"run_id", "path"},
                    "lifecycle_fact": {"run_id"}, "observation": {"run_id"}}
        for kind, fields in required.items():
            with self.subTest(kind=kind), patch("rds_hypergraph.record_topology", None, create=True):
                row = {"id": "record", "status": "UNKNOWN", "source": "fixture", "record_kind": kind}
                row["output_path" if kind == "receipt" else "receipt_id"] = []
                spec = {"nodes": [row], "hyperedges": [], "goals": ["record"]}
                result = graph_view(spec, "fixture")
                view = replica_view(result)
                self.assertEqual({i["field"] for i in view["record_topology"]["issues"]
                                  if i["reason"] == "MISSING_BINDING"}, fields)
                self.assertTrue(any(i["reason"] == "INVALID_BINDING" for i in view["record_topology"]["issues"]))
                self.assertEqual(view["record_relations"], [])
                self.assertEqual(result["analysis"]["goals"]["record"]["status"], "UNKNOWN")

    def test_fallback_membership_and_origin_identity_are_separate(self):
        with patch("rds_hypergraph.record_topology", None, create=True):
            spec = self.field_fixture()
            spec["nodes"][2]["source"] = "artifact without path or byte identity"
            view = replica_view(graph_view(spec, "fixture"))
            self.assertEqual({r["kind"] for r in view["record_relations"] if r["to"] == "artifact:a"},
                             {"run_artifact", "receipt_artifact"})
            self.assertFalse(any(r["kind"] == "artifact_observation" for r in view["record_relations"]))
            self.assertEqual({i["field"] for i in view["record_topology"]["issues"]
                              if i["node_id"] == "artifact:a" and i["reason"] == "MISSING_BINDING"}, {"path", "sha256"})

    def test_fallback_invalid_receipt_is_never_compatible_absence(self):
        for position in (None, 2, 6):
            with self.subTest(position=position), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.field_fixture()
                spec["nodes"][2].pop("receipt_id")
                spec["nodes"][6]["source"].pop("receipt_id")
                if position is not None:
                    spec["nodes"][position]["receipt_id"] = []
                view = replica_view(graph_view(spec, "fixture"))
                self.assertEqual(any(r["kind"] == "artifact_observation" for r in view["record_relations"]), position is None)
                self.assertTrue(any(r["kind"] == "run_observation" for r in view["record_relations"]))
                self.assertTrue(any(r["kind"] == "run_artifact" and r["to"] == "artifact:a" for r in view["record_relations"]))
        for value in (None, []):
            with self.subTest(receipt_run=value), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.field_fixture()
                if value is None:
                    spec["nodes"][1].pop("run_id")
                else:
                    spec["nodes"][1]["run_id"] = value
                view = replica_view(graph_view(spec, "fixture"))
                self.assertFalse(any(r["kind"] in {"receipt_artifact", "artifact_observation"}
                                     and r["to"] in {"artifact:a", "observation:a"} for r in view["record_relations"]))
                self.assertTrue(any(r["kind"] == "run_observation" for r in view["record_relations"]))

    def test_fallback_bad_extras_keep_valid_origin_indexes_and_ambiguity(self):
        for position, field, relation in ((0, "receipt_id", "run_observation"),
                                          (1, "output_path", "receipt_artifact"),
                                          (2, "receipt_id", "artifact_observation")):
            for duplicate in (False, True):
                with self.subTest(position=position, duplicate=duplicate), patch("rds_hypergraph.record_topology", None, create=True):
                    spec = self.field_fixture()
                    row = spec["nodes"][position]
                    if duplicate:
                        row = deepcopy(row); row["id"] = "duplicate-origin"; spec["nodes"].append(row)
                    row[field] = []
                    view = replica_view(graph_view(spec, "fixture"))
                    matches = [r for r in view["record_relations"] if r["kind"] == relation
                               and r["to"] in {"artifact:a", "observation:a"}]
                    self.assertEqual(bool(matches), not duplicate and position != 2)
                    if duplicate:
                        self.assertTrue(any(i["reason"] == "AMBIGUOUS_BINDING" and "duplicate-origin" in i["candidates"]
                                            for i in view["record_topology"]["issues"]))
                    if duplicate and position == 1:
                        self.assertTrue(any(r["kind"] == "artifact_observation" for r in view["record_relations"]))

    def test_fallback_conflicting_receipt_aliases_keep_independent_membership(self):
        for position, expected in ((0, None), (1, "run_receipt"), (2, "run_artifact"),
                                   (6, "run_observation"), (7, "declared_output"), (9, "run_lifecycle_fact")):
            with self.subTest(position=position), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.field_fixture()
                target = spec["nodes"][position]
                target.update(receipt_id="b" * 64, receipt_sha256="e" * 64)
                view = replica_view(graph_view(spec, "fixture"))
                if expected is None:
                    self.assertTrue(any(r["kind"] == "run_observation" for r in view["record_relations"]))
                else:
                    self.assertEqual({r["kind"] for r in view["record_relations"] if r["to"] == target["id"]}, {expected})
                self.assertTrue(any(i["node_id"] == target["id"] and i["field"] == "receipt_id"
                                    and i["reason"] == "CONFLICTING_BINDINGS" for i in view["record_topology"]["issues"]))

    def test_fallback_cross_run_receipt_keeps_independent_run_links(self):
        for position, kind in ((2, "run_artifact"), (6, "run_observation"), (7, "declared_output"),
                               (9, "run_lifecycle_fact")):
            with self.subTest(kind=kind), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.record_fixture()
                spec["nodes"].append({"id": "lifecycle:a", "status": "UNKNOWN", "source": "fixture",
                                      "record_kind": "lifecycle_fact", "run_id": "a"})
                target = spec["nodes"][position]
                target["receipt_id"] = "c" * 64
                if isinstance(target["source"], dict):
                    target["source"]["receipt_id"] = "c" * 64
                result = graph_view(spec, "fixture")
                before = deepcopy(result)
                view = replica_view(result)
                target_relations = [r for r in view["record_relations"] if r["to"] == target["id"]]
                self.assertEqual([r["kind"] for r in target_relations], [kind])
                self.assertEqual(target_relations[0]["from"], "owned:run:a")
                self.assertTrue(any(i["node_id"] == target["id"] and i["field"] == "receipt.run_id"
                                    for i in view["record_topology"]["issues"]))
                self.assertEqual(result, before)

    def test_fallback_explicit_saved_source_base_reconciles_path_aliases(self):
        with tempfile.TemporaryDirectory() as directory, patch("rds_hypergraph.record_topology", None, create=True):
            spec = self.record_fixture()
            expected = _legacy_record_report(spec)["record_relations"]
            base = Path(directory).resolve()
            spec["record_source_base_dir"] = str(base)
            for row in spec["nodes"]:
                if isinstance(row["source"], dict) and "file" in row["source"]:
                    row["artifact_path"] = row["source"]["file"]
                    row["source"]["path"] = row["source"]["file"]
                    row["source"]["file"] = str(base / row["source"]["file"])
            result = graph_view(spec, "fixture")
            before = deepcopy(result)
            view = replica_view(result)
            self.assertEqual(view["record_relations"], expected)
            self.assertEqual(view["record_topology"]["issues"], [])
            self.assertEqual(result, before)
            spec["nodes"][6]["source"]["path"] = "out/different.json"
            conflict = _legacy_record_report(spec)
            self.assertTrue(any(i["node_id"] == "observation:a" and i["field"] == "path"
                                and i["reason"] == "CONFLICTING_BINDINGS" for i in conflict["record_topology"]["issues"]))
            spec["nodes"][2]["artifact_path"] = "out/invalid\0.json"
            invalid = _legacy_record_report(spec)
            self.assertTrue(any(i["node_id"] == "artifact:a" and i["field"] == "path"
                                and i["reason"] == "INVALID_BINDING" for i in invalid["record_topology"]["issues"]))

    def test_saved_source_base_reaches_display_without_changing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch("rds_hypergraph.record_topology", None, create=True):
            root = Path(directory) / "project"
            root.mkdir()
            base = Path(directory).resolve() / "actual-source"
            spec = self.record_fixture()
            expected = _legacy_record_report(spec)["record_relations"]
            for row in spec["nodes"]:
                if isinstance(row["source"], dict) and "file" in row["source"]:
                    row["artifact_path"] = row["source"]["file"]
                    row["source"]["path"] = row["source"]["file"]
                    row["source"]["file"] = str(base / row["source"]["file"])
            snapshot = save(root, spec, expected=None, source_base=base)
            originals = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = read_graph(root)
            before = deepcopy(result)
            view = replica_view(result)
            self.assertEqual(view["record_relations"], expected)
            self.assertEqual(view["record_topology"]["issues"], [])
            self.assertEqual(HypergraphViewTests.payload(render_html(result))["display"]["diagnostics"]["count"], 0)
            self.assertEqual(result, before)
            self.assertEqual(result["snapshot_sha256"], snapshot)
            self.assertEqual(result["graph"], spec)
            self.assertNotIn("record_source_base_dir", spec)
            self.assertEqual({p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}, originals)
            explicit = deepcopy(spec)
            explicit["record_source_base_dir"] = str(base)
            save(root, explicit, expected=snapshot, source_base=root)
            explicit_result = read_graph(root)
            self.assertEqual(replica_view(explicit_result)["record_relations"], expected)
            self.assertEqual(replica_view(explicit_result)["record_topology"]["issues"], [])
            self.assertEqual(explicit_result["graph"], explicit)
            conflicting = deepcopy(result)
            conflicting["graph"]["nodes"][6]["source"]["path"] = "out/different.json"
            self.assertTrue(any(i["reason"] == "CONFLICTING_BINDINGS" for i in replica_view(conflicting)["record_topology"]["issues"]))

    def test_fallback_invalid_reported_base_is_diagnostic_without_inference_changes(self):
        spec = self.record_fixture()
        expected = _legacy_record_report(spec)["record_relations"]
        baseline = graph_view(spec, "fixture")
        for base in ("relative/source", [], "Z:\\invalid\0base"):
            with self.subTest(base=base), patch("rds_hypergraph.record_topology", None, create=True):
                value = {**deepcopy(spec), "record_source_base_dir": base}
                result = graph_view(value, "fixture")
                before = deepcopy(result)
                view = replica_view(result)
                self.assertEqual(view["record_relations"], expected)
                self.assertTrue(any(i.get("scope") == "dependency_map" and i["node_id"] is None
                                    and i["field"] == "record_source_base_dir" and i["reason"] == "INVALID_BINDING"
                                    for i in view["record_topology"]["issues"]))
                self.assertEqual(result["analysis"], baseline["analysis"])
                self.assertEqual(result, before)

    def test_ambiguous_and_conflicting_artifact_bindings_block_only_that_relation(self):
        for mutation, reason in (("duplicate_artifact", "AMBIGUOUS_BINDING"),
                                 ("artifact_receipt", "CONFLICTING_BINDINGS"),
                                 ("observation_path", "CONFLICTING_BINDINGS"),
                                 ("duplicate_receipt", "AMBIGUOUS_BINDING")):
            with self.subTest(mutation=mutation), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.record_fixture()
                if mutation == "duplicate_artifact":
                    row = deepcopy(spec["nodes"][2]); row["id"] = "duplicate-artifact"; spec["nodes"].append(row)
                elif mutation == "artifact_receipt":
                    # Same run has another explicit receipt; local run binding
                    # survives while artifact->observation fails receipt identity.
                    spec["nodes"][4]["run_id"] = "a"
                    spec["nodes"][2]["receipt_id"] = "c" * 64
                elif mutation == "observation_path":
                    spec["nodes"][6]["source"]["file"] = "out/conflict.json"
                else:
                    row = deepcopy(spec["nodes"][1]); row["id"] = "duplicate-receipt"; spec["nodes"].append(row)
                view = replica_view(graph_view(spec, "fixture"))
                artifact_links = [r for r in view["record_relations"] if r["kind"] == "artifact_observation"]
                if mutation == "duplicate_receipt":
                    # Explicit run/path/sha/receipt strings still match locally;
                    # no ambiguous receipt origin supplies receipt->artifact.
                    # Each receipt record still has independent run membership.
                    self.assertFalse(any(r["kind"] == "receipt_artifact" and r["to"] == "artifact:a" for r in view["record_relations"]))
                    self.assertEqual({r["to"] for r in view["record_relations"] if r["kind"] == "run_receipt"},
                                     {"owned:receipt:a", "owned:receipt:b", "duplicate-receipt"})
                else:
                    self.assertFalse(artifact_links)
                self.assertTrue(any(i["reason"] == reason for i in view["record_topology"]["issues"]))
                if mutation == "artifact_receipt":
                    self.assertTrue(any(r["kind"] == "run_observation" for r in view["record_relations"]))

    def test_large_graph_calls_available_record_backend_without_analysis(self):
        spec = self.record_fixture()
        spec["nodes"].extend({"id": f"extra:{i}", "status": "UNKNOWN", "source": "fixture"} for i in range(210))
        report = {"record_relations": [{"kind": "run_observation", "from": "owned:run:a", "to": "observation:a",
                   "binding": "REPORTED_EXACT_ID_MATCH", "scientific_support": "UNKNOWN"}],
                  "record_topology": {"issues": [], "assurance": "REPORTED_RECORD_IDENTITIES_NOT_SCIENTIFIC_SUPPORT"}}
        with patch("rds_hypergraph.record_topology", return_value=report, create=True) as backend:
            result = graph_view(spec, "fixture")
            self.assertEqual(result["analysis_status"], "NOT_RUN_LARGE_GRAPH")
            view = replica_view(result)
        backend.assert_called_once()
        self.assertEqual(view["record_relations"], report["record_relations"])
        self.assertEqual(result["graph"], spec)

    def test_locale_labels_survive_both_exports_including_owned_nodes(self):
        spec = self.record_fixture()
        spec["nodes"][0]["label"] = {"zh": "执行甲", "en": "Run A"}
        spec["nodes"][6]["label"] = {"en": "Uninterpreted observation"}
        result = graph_view(spec, "fixture")
        view = replica_view(result)
        self.assertEqual(view["nodes"]["c0"]["label"], "执行甲")
        self.assertEqual(view["nodes"]["c6"]["label"], "Uninterpreted observation")
        self.assertEqual(HypergraphViewTests.payload(render_html(result))["graph"], spec)

    def test_malformed_extra_record_kind_reports_issue_without_changing_unknown(self):
        for kind in ({}, []):
            with self.subTest(kind=kind), patch("rds_hypergraph.record_topology", None, create=True):
                spec = self.record_fixture()
                spec["nodes"][6]["record_kind"] = kind
                before = deepcopy(spec)
                result = graph_view(spec, "fixture")
                view = replica_view(result)
                self.assertEqual(result["graph"], before)
                self.assertEqual(result["graph"]["nodes"][6]["status"], "UNKNOWN")
                self.assertEqual(view["nodes"]["c6"]["rds"]["kind"], "声明")
                self.assertTrue(any(i["node_id"] == "observation:a" and i["reason"] == "INVALID_RECORD_KIND"
                                    for i in view["record_topology"]["issues"]))

    def test_large_record_graph_without_limits_validates_only_an_adapted_copy(self):
        spec = large_demo()
        spec.pop("limits", None)
        spec["nodes"].extend(self.record_fixture()["nodes"])
        before = deepcopy(spec)

        def strict_backend(adapted):
            # Match the actual backend entry point's strict validation step.
            from rds_hypergraph import _validate
            _validate(adapted)
            self.assertEqual(adapted["limits"]["max_nodes"], 4096)
            self.assertEqual(adapted["limits"]["max_hyperedges"], 8192)
            return _legacy_record_report(adapted)

        with patch("rds_hypergraph_view.analyze_hypergraph", side_effect=AssertionError("No combinatorial analysis")), \
             patch("rds_hypergraph.record_topology", side_effect=strict_backend, create=True) as backend:
            result = graph_view(spec, "fixture")
            self.assertEqual(result["analysis_status"], "NOT_RUN_LARGE_GRAPH")
            view = replica_view(result)
        backend.assert_called_once()
        self.assertTrue(any(r["kind"] == "artifact_observation" for r in view["record_relations"]))
        self.assertEqual(result["graph"], before)
        self.assertEqual(spec, before)
        self.assertNotIn("limits", result["graph"])

    def test_cycle_ranks_are_shared_and_long_chain_does_not_recurse(self):
        ids=["a","b","c","orphan"]
        ranks=dependency_levels(ids,[["a","b",{}],["b","a",{}],["b","c",{}]])
        self.assertEqual(ranks["a"],ranks["b"])
        self.assertLess(ranks["b"],ranks["c"])
        self.assertIsNone(ranks["orphan"])
        chain=[str(i) for i in range(4096)]
        ranks=dependency_levels(chain,[[chain[i],chain[i+1],{}] for i in range(4095)])
        self.assertLess(ranks[chain[0]],ranks[chain[-1]])

    def test_project_scope_connects_components_without_inventing_dependencies(self):
        spec={"schema":1,"nodes":[{"id":x,"status":"UNKNOWN","source":"fixture"} for x in ["a","b","lonely"]],
              "hyperedges":[{"id":"route","premises":["a"],"conclusion":"b","status":"SUPPORTED","source":"fixture"}],"goals":["b"]}
        before=deepcopy(spec);result=graph_view(spec,"source.json",snapshot_sha256="saved-sha");view=replica_view(result)
        self.assertEqual(view["scope"]["components"],2)
        self.assertEqual(view["scope"]["unlinked_records"],1)
        self.assertTrue(view["scope"]["node"]["rds"]["virtual"])
        self.assertEqual({t for _,t,_ in view["scope"]["links"]},{"c1","c2"})
        adj={uid:set() for uid in [*view["nodes"],"scope"]}
        for s,t,meta in view["links"]+view["scope"]["links"]:
            adj[s].add(t);adj[t].add(s)
            if meta["family"]=="membership":
                self.assertTrue(meta["dash"])
                self.assertNotIn("hyperedge",meta)
                self.assertEqual(meta["binding"]["sha256"],"saved-sha")
        seen={"scope"};queue=["scope"]
        for uid in queue:
            for other in adj[uid]-seen:seen.add(other);queue.append(other)
        self.assertEqual(seen,set(adj))
        self.assertEqual(result["graph"],before)
        self.assertEqual(len(view["links"]),2)  # Original incidence stays separate.
        self.assertIsNone(view["nodes"]["c2"]["rds"]["flowX"])

    @unittest.skipUnless(shutil.which("node"),"Node required to execute actual goal-weight calculation")
    def test_actual_goal_weights_split_and_or_and_bound_cycles(self):
        function=REPLICA_APP.split("(() => {",1)[0]
        driver="const vm=require('node:vm');const scope={};vm.createContext(scope);vm.runInContext("+json.dumps(function)+",scope);\n"+r'''
const ids=['goal','p','q','r','shared','bad','orphan'];
const edge=(id,p,c,weight=1,status='SUPPORTED')=>({id,premises:p,conclusion:c,weight,status});
const spec={nodes:ids.map(id=>({id,status:'UNKNOWN'})),goals:['goal'],hyperedges:[
 edge('and',['p','q'],'goal',3),edge('or',['r'],'goal'),edge('p-from-shared',['shared'],'p'),
 edge('q-from-shared',['shared'],'q'),edge('cycle',['goal'],'p'),edge('bad',['bad'],'goal',100,'CONTRADICTED'),edge('zero',['orphan'],'goal',0)]};
const before=JSON.stringify(spec),c=scope.goalCredits(spec,'goal');
const eq=(got,want)=>{if(Math.abs(got-want)>1e-10)throw Error(`${got} != ${want}`);};
eq(c.nodes.get('goal'),1);eq(c.edges.get('and'),.75);eq(c.nodes.get('p'),.375);eq(c.nodes.get('q'),.375);eq(c.nodes.get('r'),.25);eq(c.nodes.get('shared'),.75);
if(c.nodes.has('bad')||c.nodes.has('orphan')||c.edges.has('cycle')||JSON.stringify(spec)!==before)throw Error('Status, cycle or input boundary broken');
if(scope.goalCredits(spec,'missing').nodes.size)throw Error('Invented missing goal');
const mixed={nodes:['goal','p','q','r','s'].map(id=>({id,status:'UNKNOWN'})),goals:['goal'],hyperedges:[edge('main',['p','q'],'goal'),edge('alternative',['r'],'goal'),edge('mixed-depth',['q','s'],'p')]};
const m=scope.goalCredits(mixed,'goal');eq(m.edges.get('main'),.5);eq(m.nodes.get('p'),.25);eq(m.nodes.get('q'),.25);eq(m.nodes.get('r'),.5);
if(m.edges.has('mixed-depth')||m.nodes.has('s'))throw Error('A partially shortest AND route lost a premise share');
const empty={nodes:['goal','p','q'].map(id=>({id})),goals:['goal'],hyperedges:[edge('assumption',[],'goal'),edge('and',['p','q'],'goal',3)]};
const a=scope.goalCredits(empty,'goal');eq(a.edges.get('assumption'),.25);eq(a.edges.get('and'),.75);eq(a.nodes.get('p'),.375);eq(a.nodes.get('q'),.375);
const huge={nodes:['goal','p','q'].map(id=>({id})),hyperedges:[edge('large',['p'],'goal',1e9),edge('small',['q'],'goal',1e8)]};
const h=scope.goalCredits(huge,'goal');eq(h.edges.get('large'),10/11);eq(h.nodes.get('q'),1/11);
huge.hyperedges[0].weight=1e308;huge.hyperedges[1].weight=1e308;
const overflow=scope.goalCredits(huge,'goal');eq(overflow.edges.get('large'),.5);eq(overflow.edges.get('small'),.5);
mixed.hyperedges[0].weight=1e9;mixed.hyperedges[1].weight=1e8;mixed.hyperedges[2].weight=1e308;
const excluded=scope.goalCredits(mixed,'goal');eq(excluded.edges.get('main'),10/11);if(excluded.edges.has('mixed-depth'))throw Error('Ineligible huge route entered normalization');
const tricky={nodes:[{id:'__proto__'},{id:'constructor'}],hyperedges:[edge('toString',['constructor'],'__proto__')],goals:['__proto__']};
eq(scope.goalCredits(tricky,'__proto__').nodes.get('constructor'),1);
const chain={nodes:Array.from({length:4096},(_,i)=>({id:String(i)})),hyperedges:Array.from({length:4095},(_,i)=>edge(String(i),[String(i)],String(i+1))),goals:['4095']};
eq(scope.goalCredits(chain,'4095').nodes.get('0'),1);
console.log('AND/OR credit, huge ratios and overflow, whole-route mixed-depth eligibility, assumption-free route, shared premise, contradicted/zero route, cycle, missing goal, prototype IDs and long chain PASS');
'''
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/"weights.cjs";script.write_text(driver,encoding="utf-8")
            result=subprocess.run([shutil.which("node"),str(script)],capture_output=True,text=True,encoding="utf-8",timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_replica_large_map_is_complete_and_no_status_drives_repulsion(self):
        spec=large_demo();view=replica_view(graph_view(spec,"synthetic",demo=True))
        self.assertEqual(len(view["nodes"]),1200+1426)
        self.assertEqual(len(view["links"]),3104)
        self.assertEqual(view["provenance_count"],0)
        self.assertFalse(any(l[2].get("mode")=="repel" for l in view["links"]))

    def test_external_renderer_mismatch_fails_before_any_script_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/"replica").mkdir()
            (root/"replica/renderer.js").write_text("throw Error('untrusted')",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"pinned commit"):
                render_replica_html(graph_view(example(),"fixture"),root,root/"pixi.js")
        with self.assertRaisesRegex(ValueError,"no longer matches"):
            patch_replica_renderer("window.GraphRenderer = unrecognized;")

    @unittest.skipUnless(os.environ.get("RDS_REPLICA_ROOT") and shutil.which("node"),
                         "Local pinned replica checkout and Node required for integration")
    def test_pinned_replica_worker_cools_and_extra_forces_have_correct_direction(self):
        upstream=Path(os.environ["RDS_REPLICA_ROOT"])
        for name,expected in REPLICA_FILES.items():
            raw=(upstream/"replica"/name).read_bytes().replace(b"\r\n",b"\n")
            self.assertEqual(hashlib.sha256(raw).hexdigest(),expected)
        worker=patch_replica_worker((upstream/"replica/sim-worker.js").read_text(encoding="utf-8"))
        # Execute the actual pinned worker with virtual time; isolate each force.
        driver="const vm=require('node:vm'); const {performance}=require('node:perf_hooks');\nconst library="+json.dumps(D3_BUNDLE)+";\nconst worker="+json.dumps(worker)+";\n"+r'''
function run(mode,extras={},strength=1,nodeWeight=1,radius=60){
 const q=new Map();let timer=0,output,steps=0;
 const math=Object.create(Math);math.random=()=>.5;
 const scope={performance,Float32Array,Map,Math:math,Date,
  setTimeout:fn=>{q.set(++timer,fn);return timer},clearTimeout:id=>q.delete(id),setInterval:()=>0,clearInterval:()=>{},postMessage:x=>output=x};scope.self=scope;
 vm.createContext(scope);vm.runInContext(library+';'+worker,scope);
 scope.onmessage({data:{nodes:{a:[-80,-120],b:[80,120]},links:[['a','b',{relation:'R'}]],
  forces:{centerStrength:0,repelStrength:1,linkStrength:strength,linkDistance:mode==='attract'?60:350,flowStrength:0,groupStrength:0,relations:{R:{mode,strength:1}},...extras},
  layoutTargets:{a:{flowX:-300,group:'same',chargeWeight:nodeWeight,collisionRadius:radius},b:{flowX:300,group:'same',collisionRadius:radius}},alpha:1,run:true}});
 while(q.size&&steps<400){const [id,fn]=q.entries().next().value;q.delete(id);fn();steps++;}
 if(q.size||steps>302||!output)throw Error('Worker did not cool');
 const p=Array.from(new Float32Array(output.buffer));if(!p.every(Number.isFinite))throw Error('Nonfinite');
 return {x:p[2]-p[0],y:p[3]-p[1],distance:Math.hypot(p[2]-p[0],p[3]-p[1]),steps};
}
const none=run('none'),attract=run('attract'),repel=run('repel'),zero=run('repel',{},0),flow=run('attract',{flowStrength:.08,linkStrength:0,linkDistance:350}),group=run('none',{groupStrength:.06});
if(!(attract.distance<none.distance&&repel.distance>none.distance&&zero.distance===none.distance&&flow.x>none.x&&group.distance<none.distance))throw Error(JSON.stringify({none,attract,repel,zero,flow,group}));
const light=run('none',{repelStrength:300},1,1),heavy=run('none',{repelStrength:300},1,3),capped=run('none',{repelStrength:300},1,300);
if(!(heavy.distance>light.distance&&Math.abs(capped.distance-heavy.distance)<.001))throw Error('Goal weight did not strengthen bounded repulsion');
const smallBody=run('attract',{repelStrength:1},1,1,24),largeBody=run('attract',{repelStrength:1},1,1,120);
if(!(largeBody.distance>smallBody.distance+50))throw Error('Collision radius did not reserve room for large nodes');
function drag(damping){
 const q=new Map();let timer=0,output;
 const scope={performance,Float32Array,Map,Math,Date,setTimeout:fn=>{q.set(++timer,fn);return timer},clearTimeout:id=>q.delete(id),setInterval:()=>0,clearInterval:()=>{},postMessage:x=>output=x};scope.self=scope;
 vm.createContext(scope);vm.runInContext(library+';'+worker,scope);
 const step=()=>{const [id,fn]=q.entries().next().value;q.delete(id);fn();};
 const point=()=>Array.from(new Float32Array(output.buffer));
 scope.onmessage({data:{nodes:{a:[-200,0],b:[0,0],c:[200,0],orphan:[2000,800]},links:[['a','b',{relation:'R'}],['b','c',{relation:'R'}]],forces:{centerStrength:0,repelStrength:1,linkStrength:.4,linkDistance:200,flowStrength:0,groupStrength:0,damping},alpha:1,run:true}});
 while(q.size)step();const before=point();
 scope.onmessage({data:{forceNode:{id:'a',x:500,y:80},alpha:.3,alphaTarget:.3,run:true}});
 for(let i=0;i<10;i++)step();const early=point();
 for(let i=0;i<90;i++)step();const held=point();
 if(held[0]!==500||held[1]!==80||held[2]-before[2]<40||Math.abs(held[6]-before[6])>5)throw Error('Dragged node did not elastically move neighbors');
 scope.onmessage({data:{forceNode:{id:'a',x:null,y:null},alphaTarget:0}});
 let ticks=0;while(q.size&&ticks<310){step();ticks++;}if(q.size||!point().every(Number.isFinite))throw Error('Drag did not release/cool');
 return {earlyMove:Math.abs(early[2]-before[2]),neighborMove:held[2]-before[2],orphanMove:held[6]-before[6],releaseTicks:ticks};
}
const dragLow=drag(.15),dragHigh=drag(.8);if(!(dragLow.earlyMove>dragHigh.earlyMove))throw Error('Damping did not slow response');
function field(strength,member=false,empty=false){
 const q=new Map();let timer=0,output;const scope={performance,Float32Array,Map,Math,Date,setTimeout:fn=>{q.set(++timer,fn);return timer},clearTimeout:id=>q.delete(id),setInterval:()=>0,clearInterval:()=>{},postMessage:x=>output=x};scope.self=scope;
 vm.createContext(scope);vm.runInContext(library+';'+worker,scope);
 scope.onmessage({data:{nodes:empty?{}:{a:[-200,0],b:[200,0],h:[0,700],n:[0,18]},links:empty?[]:[['a','b',{hyperedge:'h',relation:'R'}]],layoutTargets:{h:{members:member?['a','b','n']:['a','b']}},forces:{centerStrength:0,repelStrength:1,linkStrength:0,flowStrength:0,groupStrength:0,edgeRepulsion:strength,edgeClearance:45},alpha:1,run:true}});
 const [id,fn]=q.entries().next().value;q.delete(id);fn();if(empty){if(q.size)throw Error('Empty graph kept scheduling');return;}
 return Array.from(new Float32Array(output.buffer));
}
field(0,false,true);const baselineField=field(0),repulsiveField=field(.5),memberField=field(.5,true);
if(!(repulsiveField[7]>baselineField[7]+3&&repulsiveField[1]<baselineField[1]&&memberField[7]===baselineField[7]))throw Error('Whole-edge field did not repel foreign node or exempt members');
console.log(JSON.stringify({none,attract,repel,zero,flow,group,light,heavy,capped,smallBody,largeBody,dragLow,dragHigh,baselineField,repulsiveField,memberField}));
'''
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/"worker.cjs";script.write_text(driver,encoding="utf-8")
            result=subprocess.run([shutil.which("node"),str(script)],capture_output=True,text=True,encoding="utf-8",timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(os.environ.get("RDS_REPLICA_ROOT") and os.environ.get("RDS_PIXI_JS"),
                         "Local pinned replica/Pixi assets required for offline export integration")
    def test_offline_replica_export_contains_real_map_and_escaped_data(self):
        spec=example();spec["nodes"][0]["label"]="</script><script>alert('owned')</script> __APP__"
        result=graph_view(spec,"fixture",snapshot_sha256="original-snapshot")
        page=render_replica_html(result,os.environ["RDS_REPLICA_ROOT"],os.environ["RDS_PIXI_JS"])
        payload=HypergraphViewTests.payload(page)
        self.assertEqual(payload["graph"],spec)
        self.assertEqual(payload["snapshot_sha256"],"original-snapshot")
        self.assertFalse(payload["renderer"]["runtime_network"])
        self.assertNotIn("<script>alert('owned')",page)
        self.assertNotIn('src="https://',page)
        self.assertNotIn("importScripts(",page)
        self.assertIn("Copyright (c) 2013-2023 Mathew Groves, Chad Engler",page)
        self.assertIn(REPLICA_APP,page)
        large_page = render_replica_html(graph_view(large_demo(), "synthetic:large"),
                                        os.environ["RDS_REPLICA_ROOT"], os.environ["RDS_PIXI_JS"])
        large_payload = HypergraphViewTests.payload(large_page)
        self.assertEqual(large_payload["analysis_status"], "NOT_RUN_LARGE_GRAPH")
        self.assertIn("分析未运行", large_payload["analysis_notice"])
        self.assertIn('<div id="analysis-note" role="note">', large_page)
        # Runtime scripts themselves are parsed by Node without loading a browser.
        if shutil.which("node"):
            with tempfile.TemporaryDirectory() as tmp:
                for i,script in enumerate(re.findall(r'<script>(.*?)</script>',page,re.S)):
                    path=Path(tmp)/f"part-{i}.js";path.write_text(script,encoding="utf-8")
                    check=subprocess.run([shutil.which("node"),"--check",str(path)],capture_output=True,text=True,encoding="utf-8",timeout=10)
                    self.assertEqual(check.returncode,0,check.stderr)


class _JSBoundaryTests(unittest.TestCase):
    """Execute production scripts; stub only browser/renderer transport boundaries."""

    DOM_STUB = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
function browser(payload,blocked=false){
 const ids={},created=[],frames=[],workers=[],listeners={};let clock=0;
 function context(){return new Proxy({paths:[],path:[],beginPath(){this.path=[]},moveTo(x,y){this.path.push([x,y])},lineTo(x,y){this.path.push([x,y])},stroke(){this.paths.push(this.path.slice())},measureText(){return {width:40}},createRadialGradient(){return {addColorStop(){}}}},{get(t,k){return k in t?t[k]:()=>{}}});}
 class Element{
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.events={};this.dataset={};this.style={setProperty(){}};this._text='';this.ctx=context();const classes=new Set();this.classList={add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:x=>classes.has(x)?classes.delete(x):classes.add(x)};created.push(this);}
  set id(v){this._id=v;ids[v]=this}get id(){return this._id}
  set textContent(v){this._text=String(v)}get textContent(){return this._text}
  append(...cs){this.children.push(...cs)}replaceChildren(...cs){this.children=cs}
  setAttribute(k,v){this[k]=v}addEventListener(k,f){const old=this.events[k];this.events[k]=old?e=>{old(e);f(e)}:f}
  getContext(){return this.ctx}getBoundingClientRect(){return {left:0,top:0,width:1000,height:700}}setPointerCapture(){}
 }
 const get=id=>ids[id]||Object.assign(new Element('div'),{id});
 get('snapshot').textContent=JSON.stringify(payload);get('app');get('d3-worker-library').textContent='';
 const document={hidden:false,getElementById:get,createElement:t=>new Element(t),addEventListener:(k,f)=>listeners[k]=f,body:new Element('body')};
 const scope={document,window:{addEventListener(){}},devicePixelRatio:1,performance:{now:()=>clock+=16},requestAnimationFrame:f=>frames.push(f),ResizeObserver:class{observe(){}disconnect(){}},Blob:class{},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},Worker:class{constructor(){if(blocked)throw Error('blocked');workers.push(this)}postMessage(d){this.sent=structuredClone(d)}terminate(){this.dead=true}},setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},console};
 vm.createContext(scope);
 const flush=()=>{let left=1000;while(frames.length&&left--)frames.shift()();assert.equal(frames.length,0,'draw must quiesce')};
 const visibility=hidden=>{document.hidden=hidden;listeners.visibilitychange()};
 const descendants=e=>[e,...e.children.flatMap(c=>typeof c==='object'?descendants(c):[])];
 const text=e=>[e.textContent,...e.children.map(c=>typeof c==='object'?text(c):String(c))].join(' ');
 return {scope,ids,created,workers,flush,visibility,descendants,text};
}
'''

    def run_js(self, production, driver):
        script = self.DOM_STUB + "\nconst production=" + json.dumps(production) + ";\n" + driver
        result = subprocess.run([shutil.which("node"), "-"], input=script,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        print(result.stdout.strip())


@unittest.skipUnless(shutil.which("node"), "Node required for actual Canvas interaction boundaries")
class CanvasBoundaryTests(_JSBoundaryTests):
    def test_parallel_or_routes_remain_pointer_selectable_after_layout_update(self):
        self.run_js(JS, r'''
const graph={nodes:[{id:'p',label:'Premise',status:'UNKNOWN'},{id:'g',label:'Goal',status:'UNKNOWN'}],hyperedges:[{id:'a',label:'Route A',premises:['p'],conclusion:'g',relation:'R',status:'SUPPORTED',weight:1},{id:'b',label:'Route B',premises:['p'],conclusion:'g',relation:'R',status:'PROPOSED',weight:2}],goals:['g']};
const original=JSON.stringify(graph),b=browser({status:'AVAILABLE',source:'synthetic',graph});vm.runInContext(production,b.scope,{timeout:5000});
const c=b.ids['hg-canvas'],draws=[],stroke=c.ctx.stroke;c.ctx.stroke=function(){draws.push(this.path.slice());stroke.call(this);};
function verify(){b.flush();const path=draws[0];assert.equal(path.length,8,'two OR routes retain all four incidence links');const junctions=[path[1],path[5]];assert.ok(Math.hypot(junctions[0][0]-junctions[1][0],junctions[0][1]-junctions[1][1])>12,'OR junctions must have distinct hit targets');for(let i=0;i<2;i++){c.events.pointerdown({button:0,clientX:junctions[i][0],clientY:junctions[i][1],pointerId:1});c.events.pointerup();assert.match(b.text(b.ids['hg-detail']),/超边详情/);assert.ok(b.text(b.ids['hg-detail']).includes('Route '+(i?'B':'A')),'pointer opens its own route');}assert.equal(JSON.stringify(graph),original);}
verify();c.events.keydown({key:'Escape'});b.flush();draws.length=0;b.workers[0].onmessage({data:{positions:new Float32Array([-160,0,200,0]),ticks:180,ms:10,ready:true,done:true}});verify();
console.log('Canvas: parallel OR routes independently selected before/after actual worker update; raw records unchanged PASS');
''')

    def test_short_labels_status_strokes_hover_and_raw_id_search(self):
        spec = {"schema": 1, "nodes": [
            {"id": "run.good.succeeded", "status": "SUPPORTED", "source": "synthetic",
             "owned_fact": {"value": True, "reliable": True}},
            {"id": "run.bad.failed", "status": "SUPPORTED", "source": "synthetic",
             "owned_fact": {"value": True, "reliable": True}},
            {"id": "run.wait.failed", "status": "SUPPORTED", "source": "synthetic",
             "owned_fact": {"value": False, "reliable": True}}], "hyperedges": [], "goals": ["run.good.succeeded"]}
        result = graph_view(spec, "synthetic:canvas-status")
        payload = {**result, "display": display_records(result)}
        self.run_js(JS, "const payload=" + json.dumps(payload) + ";\n" + r'''
const b=browser(payload);vm.runInContext(production,b.scope,{timeout:10000});
const canvas=b.created.find(e=>e.tagName==='CANVAS'&&e.id!=='hg-minimap'),ctx=canvas.ctx,strokes=[],labels=[];
assert.ok(!b.created.some(e=>e.tagName==='BUTTON'&&e['aria-label']==='研究说明'),'empty graph summary has no entry');
const stroke=ctx.stroke;ctx.stroke=function(){strokes.push(this.strokeStyle);stroke.call(this)};
ctx.fillText=(...args)=>labels.push(args);b.flush();
for(const color of ['#39b872','#e65b63','#9299a6'])assert.ok(strokes.includes(color),'independent status stroke '+color);
assert.ok(labels.some(([text])=>text==='good'));assert.ok(!labels.some(([text])=>text.startsWith('run.')));
const good=labels.find(([text])=>text==='good'),radius=Math.max(1.2,3*Math.sqrt(Number(canvas.dataset.scale)));
canvas.events.pointermove({clientX:good[1],clientY:good[2]-radius-5});b.flush();
const tip=b.created.find(e=>e.className==='hg-tip');assert.equal(tip.hidden,false);assert.ok(tip.textContent.startsWith('good\n'));
assert.ok(!tip.textContent.includes('run.good.succeeded'),'hover first line remains readable');
const search=b.ids['hg-search'];search.value='run.wait.failed';search.events.input();b.flush();
assert.ok(b.text(b.ids['hg-search-results']).includes('wait'),'original ID finds readable result');
assert.ok(!b.text(b.ids['hg-search-results']).includes('run.wait.failed'),'result does not expose raw ID');
const button=b.ids['hg-search-results'].children.find(e=>e.tagName==='BUTTON');button.events.click();b.flush();
const detail=b.ids['hg-detail'];assert.ok(b.text(detail).includes('未判定'));
assert.ok(!b.text(detail).includes('failed=false'));assert.ok(!b.text(detail).includes('run.wait.failed'));
assert.ok(!b.created.some(e=>e.tagName==='PRE'),'technical records are not rendered or collapsed');
assert.equal(JSON.stringify(payload.graph),JSON.stringify(JSON.parse(b.ids.snapshot.textContent).graph));
console.log('Actual Canvas: readable labels/status, green/red/neutral strokes, ID lookup without visible IDs/technical records PASS');
''')

    def test_layout_junction_failure_and_visibility_interactions(self):
        self.run_js(JS, r'''
const payload={status:'AVAILABLE',source:'synthetic',graph:{nodes:[{id:'g',label:'Conclusion',status:'UNKNOWN',source:'fixture'}],hyperedges:[{id:'e',premises:[],conclusion:'g',relation:'R',status:'UNKNOWN',source:'fixture'}],goals:['g']}};
function run(blocked=false){const b=browser(payload,blocked);vm.runInContext(production,b.scope,{timeout:5000});return b;}
function finish(b){b.workers.at(-1).onmessage({data:{positions:new Float32Array([0,0]),ticks:180,ms:10,ready:true,done:true}});b.flush();}
const settled=run();finish(settled);
const segment=settled.ids['hg-canvas'].ctx.paths.find(p=>p.length>=2);
assert.ok(Math.hypot(segment[1][0]-segment[0][0],segment[1][1]-segment[0][1])>10,'premise-free rule needs a visible incidence segment');
// A real pointer selection must reach the premise-free rule rather than its conclusion.
const c=settled.ids['hg-canvas'];c.events.pointerdown({button:0,clientX:segment[0][0],clientY:segment[0][1],pointerId:1});c.events.pointerup();
assert.match(settled.text(settled.ids['hg-detail']),/超边详情/);
settled.visibility(true);settled.visibility(false);assert.equal(settled.workers.length,1,'settled graph must not reheat on tab return');
const active=run();active.visibility(true);assert.ok(active.workers[0].dead);active.visibility(false);active.visibility(false);assert.equal(active.workers.length,2,'running layout resumes exactly once');
const paused=run();paused.ids['hg-pause'].events.click();paused.visibility(true);paused.visibility(false);assert.equal(paused.workers.length,1,'manual pause survives visibility change');
for(const blocked of [true,false]){const b=run(blocked);if(!blocked)b.workers[0].onerror();b.flush();b.ids['hg-zoom-in'].events.click();b.ids['hg-canvas'].events.keydown({key:'ArrowRight',preventDefault(){}});b.flush();assert.equal(b.ids['hg-canvas'].dataset.layoutState,'unavailable');assert.match(b.ids['hg-performance'].textContent,/布局线程不可用/);assert.doesNotMatch(b.ids['hg-performance'].textContent,/已稳定/);}
console.log('Canvas: selectable empty-premise route, settled/running/manual pause visibility, sync/async worker failure after pan and zoom PASS');
''')

    def test_many_relations_bound_live_controls_and_edit_last_page(self):
        self.run_js(JS, r'''
const graph={nodes:[{id:'g',status:'UNKNOWN',source:'fixture'}],hyperedges:Array.from({length:8192},(_,i)=>({id:'e'+i,premises:[],conclusion:'g',relation:'R'+i,status:'UNKNOWN',source:'fixture'})),goals:['g']};
const b=browser({status:'AVAILABLE',source:'synthetic',graph});vm.runInContext(production,b.scope,{timeout:10000});
assert.ok(b.created.length<2000,'first render must not materialize thousands of relation editors');
const next=b.created.find(e=>e.tagName==='BUTTON'&&e.textContent==='下一页');assert.ok(next);
for(let i=0;i<255;i++)next.events.click();assert.equal(next.disabled,true);
let live=b.descendants(b.ids.app);assert.ok(live.length<2000,'last page also bounds retained DOM');
const mode=live.find(e=>e.tagName==='SELECT'&&e['aria-label']==='R8191 · 力学作用');assert.ok(mode,'last allowed relation remains editable');
mode.value='repel';mode.events.change();
const previous=live.find(e=>e.tagName==='BUTTON'&&e.textContent==='上一页');previous.events.click();next.events.click();
live=b.descendants(b.ids.app);assert.equal(live.find(e=>e['aria-label']==='R8191 · 力学作用').value,'repel','paging retains edits');
console.log('Canvas: 8192 relation editors bounded on first/last page and tail relation edit retained PASS');
''')


@unittest.skipUnless(shutil.which("node"), "Node required for actual Replica interaction boundaries")
class ReplicaBoundaryTests(_JSBoundaryTests):
    def test_preferences_are_isolated_by_explicit_graph_and_saved_snapshot(self):
        self.run_js(REPLICA_APP, r'''
const graph={nodes:[{id:'fact',label:'Current fact',status:'UNKNOWN',source:'fixture'}],hyperedges:[],goals:[]},base={status:'AVAILABLE',source:'/same/graph.json',snapshot_sha256:null,graph_sha256:'graph-one',counts:{nodes:1,hyperedges:0},graph,replica_view:{nodes:{f:{label:'Current fact',type:'claim',rds:{record:'fact',kind:'声明',size:1}}},links:[],relations:[],provenance_count:0}};
const storage=new Map([['rds-replica-v2:/same/graph.json',JSON.stringify({search:'stale pathname',growth:false})]]),reads=[];
function run(identity,snapshot=null){const payload={...base,graph_sha256:identity,snapshot_sha256:snapshot},b=browser(payload);b.scope.localStorage={getItem:k=>{reads.push(k);return storage.get(k)||null;},setItem:(k,v)=>storage.set(k,v)};b.scope.SIM_WORKER_MAIN='';b.scope.PIXI={Texture:{WHITE:{}}};b.scope.GraphRenderer=class{constructor(){b.renderer=this;this.nodes=[];this.links=[];this.nodeLookup=new Map();this.width=1000;this.height=700;this.worker={onmessage(){},postMessage(){},terminate(){}};}setData({nodes,links}){this.nodes=Object.entries(nodes).map(([id,n])=>({...n,id,x:0,y:0,getSize(){return 10;}}));this.nodeLookup=new Map(this.nodes.map(n=>[n.id,n]));this.links=links;}setForces(){}setOptions(){}changed(){}resetPan(){}zoomTo(){}setScale(){}setPan(){}};vm.runInContext(production,b.scope,{timeout:5000});return b;}
let b=run('graph-one');assert.ok(b.renderer.nodeLookup.has('f'),'old pathname preferences must not hide fresh graph');storage.set('rds-replica-v2:graph-one',JSON.stringify({search:'missing old term',growth:false}));
b=run('graph-one');assert.equal(b.renderer.nodes.length,0,'same contents reloads its preferences');b=run('graph-two');assert.ok(b.renderer.nodeLookup.has('f'),'replacement contents resets the filter');
storage.set('rds-replica-v2:saved-one',JSON.stringify({search:'missing saved term',growth:false}));assert.equal(run('graph-two','saved-one').renderer.nodes.length,0);assert.ok(run('graph-two','saved-two').renderer.nodeLookup.has('f'),'saved snapshots retain separate identities');assert.deepEqual(reads,['rds-replica-v2:graph-one','rds-replica-v2:graph-one','rds-replica-v2:graph-two','rds-replica-v2:saved-one','rds-replica-v2:saved-two']);
console.log('Replica: content identity ignores legacy pathname, reuses same graph, isolates replacement and saved snapshots PASS');
''')

    def test_large_analysis_notice_persists_in_actual_both_renderers(self):
        result = graph_view(large_demo(), "synthetic:large")
        payload = HypergraphViewTests.payload(render_html(result))
        payload["replica_view"] = replica_view(result)
        common = "const payload=" + json.dumps(payload) + ";\n" + r'''
const before=JSON.stringify(payload.graph),b=browser(payload);
function verify(){assert.equal(payload.analysis_status,'NOT_RUN_LARGE_GRAPH');const note=b.ids['analysis-note'];assert.ok(note,'analysis has its own visible DOM node');assert.match(note.textContent,/分析未运行/);assert.match(note.textContent,/超过分析上限/);assert.ok(!note.hidden);assert.equal(JSON.stringify(payload.graph),before);}
'''
        self.run_js(JS, common + r'''
vm.runInContext(production,b.scope,{timeout:10000});b.flush();verify();
b.visibility(true);b.visibility(false);b.flush();verify();
console.log('Actual Canvas: persistent large-graph analysis notice PASS');
''')
        self.run_js(REPLICA_APP, common + r'''
b.scope.localStorage={getItem:()=>JSON.stringify({growth:false}),setItem(){}};b.scope.SIM_WORKER_MAIN='';b.scope.PIXI={Texture:{WHITE:{}}};
b.scope.GraphRenderer=class{
 constructor(){b.renderer=this;this.nodes=[];this.links=[];this.nodeLookup=new Map();this.width=1000;this.height=700;this.worker={onmessage(){},postMessage(){},terminate(){}};}
 setData({nodes,links}){this.nodes=Object.entries(nodes).map(([id,n])=>({...n,id,x:0,y:0,getSize(){return 10}}));this.nodeLookup=new Map(this.nodes.map(n=>[n.id,n]));this.links=links.map(([s,t,rds])=>({source:this.nodeLookup.get(s),target:this.nodeLookup.get(t),rds}));}
 setForces(){}setOptions(){}changed(){}resetPan(){}zoomTo(){}setScale(){}setPan(){}
};
vm.runInContext(production,b.scope,{timeout:10000});verify();
b.renderer.worker.onmessage({data:{}});verify();
console.log('Actual Replica: persistent large-graph analysis notice PASS');
''')

    def test_actual_both_renderers_show_bounded_diagnostics_and_safe_candidates(self):
        from rds_hypergraph_view import readable_diagnostics
        digest = "a" * 64
        target = "private:" + digest
        candidate_ids = ["candidate:" + str(i) for i in range(10)]
        unsafe_label = '<img src=x onerror="throw Error(1)"> & candidate'
        spec = {"schema": 1, "nodes": [
            {"id": target, "label": "Target", "status": "UNKNOWN", "source": "private:" + digest},
            *[{"id": id, "label": unsafe_label if i == 0 else "Candidate " + str(i),
               "status": "UNKNOWN", "source": "fixture"} for i, id in enumerate(candidate_ids)]
        ], "hyperedges": [], "goals": [target]}
        result = graph_view(spec, "synthetic:diagnostics")
        reasons = ["MISSING_BINDING", "UNMATCHED_BINDING", "AMBIGUOUS_BINDING",
                   "CONFLICTING_BINDINGS", "INVALID_BINDING", "RUN_NOT_REGISTERED"]
        issues = [{"node_id": target, "field": "run_id" if i < 6 else "receipt_id",
                   "reason": reasons[i % 6], "candidates": candidate_ids,
                   "raw_sha256": digest, "raw_value": '<script>throw Error(2)</script>'}
                  for i in range(10)]
        result["display"] = display_records(result)
        result["display"]["nodes"][target]["diagnostics"] = readable_diagnostics(issues)
        result["display"]["diagnostics"] = readable_diagnostics(issues, graph=True)
        result["replica_view"] = replica_view(result)
        common = "const payload=" + json.dumps(result) + ";const target=" + json.dumps(target) + ";const unsafeLabel=" + json.dumps(unsafe_label) + ";\n" + r'''
const before=JSON.stringify(payload.graph),b=browser(payload);
function verify(card){const text=b.text(card);assert.match(text,/来源待核对/);assert.match(text,/另有 2 项待核对/);assert.ok(b.descendants(card).filter(e=>e.className==='source-diagnostic').length<=8);assert.ok(!text.includes('a'.repeat(64)),'raw digest stays out of human diagnostics');assert.ok(!text.includes('raw_sha256')&&!text.includes('<script>'),'raw resolver JSON is not rendered');assert.ok(!b.descendants(card).some(e=>['IMG','SCRIPT','PRE'].includes(e.tagName)),'candidate text cannot create HTML or raw JSON nodes');}
'''
        self.run_js(JS, common + r'''
vm.runInContext(production,b.scope,{timeout:10000});b.flush();
const search=b.ids['hg-search'];search.value=target;search.events.input();b.flush();
b.ids['hg-search-results'].children.find(e=>e.tagName==='BUTTON').events.click();b.flush();
const detail=b.ids['hg-detail'];verify(detail);assert.match(b.text(detail),/另有 2 个候选/);
assert.equal(b.descendants(detail).filter(e=>e.tagName==='BUTTON'&&e['aria-label']?.startsWith('查看 ')).length,8*8,'bounded candidates retained for each visible diagnostic');
const candidate=b.descendants(detail).find(e=>e.tagName==='BUTTON'&&e.textContent===unsafeLabel);assert.ok(candidate);candidate.events.click();b.flush();assert.ok(b.text(detail).includes(unsafeLabel),'candidate navigation retains literal label');assert.ok(!b.descendants(detail).some(e=>e.tagName==='IMG'));
b.created.find(e=>e.tagName==='BUTTON'&&e['aria-label']==='研究说明').events.click();verify(detail);
assert.equal(JSON.stringify(payload.graph),before);console.log('Actual Canvas diagnostics: scoped/graph visibility, bounded omissions, candidate navigation and literal text safety PASS');
''')
        self.run_js(REPLICA_APP, common + r'''
b.scope.localStorage={getItem:()=>JSON.stringify({growth:false,search:'Target'}),setItem(){}};b.scope.SIM_WORKER_MAIN='';b.scope.PIXI={Texture:{WHITE:{}}};
b.scope.GraphRenderer=class{
 constructor(){b.renderer=this;this.nodes=[];this.links=[];this.nodeLookup=new Map();this.width=1000;this.height=700;this.worker={onmessage(){},postMessage(){},terminate(){}};}
 setData({nodes,links}){this.nodes=Object.entries(nodes).map(([id,n])=>({...n,id,x:0,y:0,getSize(){return 10}}));this.nodeLookup=new Map(this.nodes.map(n=>[n.id,n]));this.links=links.map(([s,t,rds])=>({source:this.nodeLookup.get(s),target:this.nodeLookup.get(t),rds}));}
 setForces(){}setOptions(){}changed(){}resetPan(){}zoomTo(){}setScale(){}setPan(){}
};
vm.runInContext(production,b.scope,{timeout:10000});const card=b.ids['note-card'];assert.equal(b.renderer.nodes.length,1,'candidate starts outside filtered graph');
b.renderer.onNodeClick(b.renderer.nodes[0]);verify(card);assert.match(b.text(card),/另有 2 个候选/);
const candidate=b.descendants(card).find(e=>e.tagName==='BUTTON'&&e.textContent===unsafeLabel);assert.ok(candidate);candidate.events.click();assert.ok(b.renderer.nodes.some(n=>n.label===unsafeLabel),'candidate navigation rebuilds filtered graph');assert.ok(b.text(card).includes(unsafeLabel));assert.ok(!b.descendants(card).some(e=>e.tagName==='IMG'));
assert.equal(b.ids['graph-info'].hidden,false,'diagnostics alone expose graph information');b.ids['graph-info'].events.click();verify(card);
assert.equal(JSON.stringify(payload.graph),before);console.log('Actual Replica diagnostics: scoped/graph visibility, bounded omissions, filtered candidate navigation and literal text safety PASS');
''')

    def test_active_ranks_follow_relation_options_and_filtered_display(self):
        self.run_js(REPLICA_APP, r'''
const records=['a','b','c','orphan'].map((id,i)=>({id,label:['Alpha','Beta','Gamma','Lonely'][i],status:'UNKNOWN',source:'fixture'}));
const nodes=Object.fromEntries(records.map(r=>[r.id,{label:r.label,type:'claim',rds:{record:r.id,kind:'声明',size:1}}]));
const link=(s,t,relation,family='provenance')=>[s,t,{relation,family,color:'#aabbcc',width:1}];
const payload={status:'AVAILABLE',source:'synthetic',counts:{nodes:4,hyperedges:0},graph:{nodes:records,hyperedges:[],goals:[]},replica_view:{nodes,links:[link('a','b','forward'),link('b','a','back'),link('b','c','next'),link('c','orphan','scope','membership')],relations:['forward','back','next','scope'],provenance_count:3}};
const before=JSON.stringify(payload),b=browser(payload);b.scope.localStorage={getItem:()=>JSON.stringify({growth:false}),setItem(){}};b.scope.SIM_WORKER_MAIN='';b.scope.PIXI={Texture:{WHITE:{}}};b.scope.setTimeout=f=>{f();return 1};
b.scope.GraphRenderer=class{
 constructor(){b.renderer=this;this.nodes=[];this.links=[];this.nodeLookup=new Map();this.width=1000;this.height=700;this.messages=[];this.worker={onmessage(){},postMessage:d=>this.messages.push(structuredClone(d)),terminate(){}};}
 setData({nodes,links}){this.nodes=Object.entries(nodes).map(([id,n])=>({...n,id,x:0,y:0,getSize(){return 10}}));this.nodeLookup=new Map(this.nodes.map(n=>[n.id,n]));this.links=links.map(([s,t,rds])=>({source:this.nodeLookup.get(s),target:this.nodeLookup.get(t),rds}));}
 setForces(p){this.forces=structuredClone(p)}setOptions(){}changed(){}resetPan(){}zoomTo(){}setScale(){}setPan(){}
};
vm.runInContext(production,b.scope,{timeout:5000});
const rank=id=>b.renderer.nodeLookup.get(id).rds.flowX,mode=(name,value)=>{const e=b.created.find(e=>e['aria-label']===name+' 力学');e.value=value;e.events.change();};
assert.equal(rank('a'),rank('b'));assert.ok(rank('c')>rank('b'));assert.equal(rank('orphan'),null,'membership must not supply a direction');
mode('back','none');assert.ok(rank('a')<rank('b')&&rank('b')<rank('c'),'disabled return edge splits the SCC');
let targets=b.renderer.messages.at(-1).layoutTargets;assert.equal(targets.a.flowX,rank('a'));assert.equal(targets.c.flowX,rank('c'),'fresh ranks reach the worker');
mode('back','repel');assert.equal(rank('a'),rank('b'),'active signed relation retains its directed structure');mode('back','none');mode('forward','none');assert.equal(rank('a'),null);
const search=b.created.find(e=>e['aria-label']==='搜索节点');search.value='Alpha';search.events.input();assert.equal(b.renderer.nodes.length,1);assert.equal(rank('a'),null,'filtered neighbors do not leave stale ranks');
assert.equal(JSON.stringify(payload),before,'display ranks do not mutate the source snapshot');
console.log('Actual Replica options: SCC split/restore, signed relation, membership exclusion, worker targets, filtered ranks and source immutability PASS');
''')

    def test_active_rank_hubs_long_chain_and_reachability_oracle(self):
        self.run_js(REPLICA_APP.split("(() => {",1)[0], r'''
const scope={};vm.createContext(scope);vm.runInContext(production,scope);
const ids=['a','hub','b','c','isolated'],base=[['a','hub'],['hub','b'],['b','c'],['c','a'],['b','hub'],['a','b'],['c','hub']];
for(let mask=0;mask<128;mask++){
 const pairs=base.filter((p,i)=>mask&(1<<i)),links=pairs.map(([s,t])=>[s,t,{relation:'active',family:'incidence'}]);
 const levels=scope.activeDependencyLevels(ids,links,{active:{mode:'attract'}});
 // Independent small transitive-closure oracle checks SCC equality and DAG direction.
 const reach=new Map(ids.map(id=>[id,new Set([id])]));for(const [s,t] of pairs)reach.get(s).add(t);
 for(const k of ids)for(const s of ids)if(reach.get(s).has(k))for(const t of reach.get(k))reach.get(s).add(t);
 for(const s of ids)for(const t of ids)if(s!==t&&reach.get(s).has(t)){if(reach.get(t).has(s))assert.equal(levels.get(s),levels.get(t));else assert.ok(levels.get(s)<levels.get(t));}
 assert.equal(levels.get('isolated'),null);
}
const links=[['a','hub',{relation:'on',family:'incidence'}],['hub','b',{relation:'off',family:'incidence'}],['b','c',{relation:'zero',family:'provenance'}]];
const ranks=scope.activeDependencyLevels(ids,links,{on:{mode:'attract'},off:{mode:'none'},zero:{mode:'attract',strength:0}});assert.ok(ranks.get('a')<ranks.get('hub'));assert.equal(ranks.get('b'),null);assert.equal(ranks.get('c'),null);
const chain=Array.from({length:4096},(_,i)=>String(i)),chainLinks=chain.slice(1).map((id,i)=>[String(i),id,{relation:'r',family:'incidence'}]);
const long=scope.activeDependencyLevels(chain,chainLinks,{r:{mode:'attract'}});assert.equal(long.get('4095')-long.get('0'),4095*160);
console.log('Actual rank function: 128 independent reachability cases, incidence hubs, none/zero gate and 4096-node iterative chain PASS');
''')

    def test_proto_settings_clone_reload_and_filtered_related_navigation(self):
        self.run_js(REPLICA_APP, r'''
const binding={run_id:'owned-run',receipt_id:'receipt-1'},records=[{id:'fact',label:'needle fact',status:'UNKNOWN',source:'fixture'},{id:'run',label:'Owned execution',status:'UNKNOWN',source:'fixture'}];
const nodes={f:{label:'needle fact',type:'claim',rds:{record:'fact',kind:'声明',size:1}},r:{label:'Owned execution',type:'claim',rds:{record:'run',kind:'执行',size:1}}};
const payload={status:'AVAILABLE',source:'synthetic',snapshot_sha256:'fixture',counts:{nodes:2,hyperedges:0},graph:{nodes:records,hyperedges:[],goals:[]},replica_view:{nodes,links:[['r','f',{relation:'__proto__',family:'provenance',binding,color:'#aabbcc',width:1}]],relations:['__proto__','constructor','toString'],provenance_count:1}};
let saved=JSON.stringify({search:'needle',growth:false});
function run(){const b=browser(payload);b.scope.localStorage={getItem:()=>saved,setItem:(k,v)=>saved=v};b.scope.SIM_WORKER_MAIN='';b.scope.PIXI={Texture:{WHITE:{}}};
 b.scope.GraphRenderer=class{
  constructor(){b.renderer=this;this.nodes=[];this.links=[];this.nodeLookup=new Map();this.width=1000;this.height=700;this.worker={onmessage(){},postMessage:d=>{this.message=structuredClone(d)},terminate(){}};}
  setData({nodes,links}){this.nodes=Object.entries(nodes).map(([id,n])=>({...n,id,x:0,y:0,getSize(){return 10}}));this.nodeLookup=new Map(this.nodes.map(n=>[n.id,n]));this.links=links.map(([s,t,rds])=>({source:this.nodeLookup.get(s),target:this.nodeLookup.get(t),rds}));}
  setForces(p){this.forces=structuredClone(p)}setOptions(){}changed(){}resetPan(){}zoomTo(){}setScale(){}setPan(){}
 };
 vm.runInContext(production,b.scope,{timeout:5000});return b;
}
const b=run();assert.ok(b.renderer.nodeLookup.has('f'));assert.ok(!b.renderer.nodeLookup.has('r'),'fixture must filter out provenance target');
assert.equal(b.ids['graph-info'].hidden,true,'empty graph summary has no visible entry');
const mode=b.created.find(e=>e['aria-label']==='__proto__ 力学');assert.ok(mode);mode.value='none';mode.events.change();
assert.equal(Object.hasOwn(b.renderer.forces.relations,'__proto__'),true);assert.equal(b.renderer.forces.relations.__proto__.mode,'none','worker clone preserves selected force');
assert.equal(Object.hasOwn(JSON.parse(saved).relations,'__proto__'),true);
b.renderer.onNodeClick(b.renderer.nodeLookup.get('f'));
assert.doesNotMatch(b.text(b.ids['note-card']),/receipt-1/,'binding is not exposed in the card');
assert.equal(JSON.stringify(payload.replica_view.links[0][2].binding),JSON.stringify(binding),'internal binding survives filtering');
assert.ok(!b.created.some(e=>e.tagName==='PRE'),'technical records are not rendered or collapsed');
const link=b.descendants(b.ids['note-card']).find(e=>e.tagName==='BUTTON'&&e.textContent.includes('Owned execution'));assert.ok(link,'complete related target remains navigable');link.events.click();
assert.ok(b.renderer.nodeLookup.has('r'));assert.equal(JSON.parse(saved).search,'');assert.match(b.text(b.ids['note-card']),/Owned execution/,'navigation rebuilds then opens related target');
const reloaded=run();assert.equal(reloaded.created.find(e=>e['aria-label']==='__proto__ 力学').value,'none','reload preserves prototype-key relation');
console.log('Replica: prototype-key force own property through worker clone/JSON/reload, full binding and filtered related-record navigation PASS');
''')


if __name__ == "__main__":
    unittest.main()
