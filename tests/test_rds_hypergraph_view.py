"""Real offline export boundaries for native and explicit dependency maps."""
from copy import deepcopy
import json
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
from rds_hypergraph_view import JS, D3_BUNDLE, graph_view, read_graph, render_html, large_demo
from rds_tms_store import current, save


def example():
    return json.loads((ROOT / "examples/hypergraph-view.json").read_text(encoding="utf-8"))


class HypergraphViewTests(unittest.TestCase):
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
        self.assertEqual(snapshot["source"], str(source))
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


if __name__ == "__main__":
    unittest.main()
