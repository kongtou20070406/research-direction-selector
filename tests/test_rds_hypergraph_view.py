"""Real offline export boundaries for native and explicit dependency maps."""
from copy import deepcopy
import json
import hashlib
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
    replica_view, dependency_levels, render_replica_html, patch_replica_worker,
    patch_replica_renderer, REPLICA_APP, REPLICA_FILES)
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


class ReplicaViewTests(unittest.TestCase):
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
            {"id":"owned:receipt:r","status":"SUPPORTED","source":"receipt r","receipt_sha256":"receipt-sha"},
            {"id":"artifact","status":"SUPPORTED","source":{"file":"out/a.json","sha256":"a"*64,"locator":"artifact"},"run_id":"r","interpretation":"UNPARSED"},
            {"id":"fact","status":"UNKNOWN","source":{"file":"out/a.json","path":"out/a.json","sha256":"a"*64,"receipt_id":"receipt-sha","locator":"fact"}},
            {"id":"similar","status":"SUPPORTED","source":"out/a.json receipt-sha"},
            {"id":"other-path","status":"UNKNOWN","source":{"file":"out/b.json","path":"out/b.json","sha256":"a"*64,"locator":"different"}},
            {"id":"bad-extra","status":"UNKNOWN","source":{"locator":"unknown","receipt_id":{}},"run_id":[]}],
            "hyperedges":[{"id":"done","premises":["owned:run:r"],"conclusion":"owned:receipt:r","status":"SUPPORTED","source":"receipt"}],"goals":["fact"]}
        result=graph_view(spec,"fixture");view=replica_view(result)
        provenance=[l for l in view["links"] if l[2]["family"]=="provenance"]
        self.assertEqual({(s,t) for s,t,_ in provenance},{("c0","c2"),("c1","c3"),("c2","c3")})
        self.assertTrue(all(l[2]["binding"] for l in provenance))
        self.assertIsNone(view["nodes"]["c4"]["rds"]["group"])
        self.assertEqual(view["nodes"]["c3"]["rds"]["group"],"r")
        self.assertEqual(result["graph"],spec)

    def test_cycle_ranks_are_shared_and_long_chain_does_not_recurse(self):
        ids=["a","b","c","orphan"]
        ranks=dependency_levels(ids,[["a","b",{}],["b","a",{}],["b","c",{}]])
        self.assertEqual(ranks["a"],ranks["b"])
        self.assertLess(ranks["b"],ranks["c"])
        self.assertIsNone(ranks["orphan"])
        chain=[str(i) for i in range(4096)]
        ranks=dependency_levels(chain,[[chain[i],chain[i+1],{}] for i in range(4095)])
        self.assertLess(ranks[chain[0]],ranks[chain[-1]])

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
function run(mode,extras={},strength=1){
 const q=new Map();let timer=0,output,steps=0;
 const math=Object.create(Math);math.random=()=>.5;
 const scope={performance,Float32Array,Map,Math:math,Date,
  setTimeout:fn=>{q.set(++timer,fn);return timer},clearTimeout:id=>q.delete(id),setInterval:()=>0,clearInterval:()=>{},postMessage:x=>output=x};scope.self=scope;
 vm.createContext(scope);vm.runInContext(library+';'+worker,scope);
 scope.onmessage({data:{nodes:{a:[-80,-120],b:[80,120]},links:[['a','b',{relation:'R'}]],
  forces:{centerStrength:0,repelStrength:1,linkStrength:strength,linkDistance:mode==='attract'?60:350,flowStrength:0,groupStrength:0,relations:{R:{mode,strength:1}},...extras},
  layoutTargets:{a:{flowX:-300,group:'same'},b:{flowX:300,group:'same'}},alpha:1,run:true}});
 while(q.size&&steps<400){const [id,fn]=q.entries().next().value;q.delete(id);fn();steps++;}
 if(q.size||steps>302||!output)throw Error('Worker did not cool');
 const p=Array.from(new Float32Array(output.buffer));if(!p.every(Number.isFinite))throw Error('Nonfinite');
 return {x:p[2]-p[0],y:p[3]-p[1],distance:Math.hypot(p[2]-p[0],p[3]-p[1]),steps};
}
const none=run('none'),attract=run('attract'),repel=run('repel'),zero=run('repel',{},0),flow=run('attract',{flowStrength:.08,linkStrength:0,linkDistance:350}),group=run('none',{groupStrength:.06});
if(!(attract.distance<none.distance&&repel.distance>none.distance&&zero.distance===none.distance&&flow.x>none.x&&group.distance<none.distance))throw Error(JSON.stringify({none,attract,repel,zero,flow,group}));
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
console.log(JSON.stringify({none,attract,repel,zero,flow,group,dragLow,dragHigh,baselineField,repulsiveField,memberField}));
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
        # Runtime scripts themselves are parsed by Node without loading a browser.
        if shutil.which("node"):
            with tempfile.TemporaryDirectory() as tmp:
                for i,script in enumerate(re.findall(r'<script>(.*?)</script>',page,re.S)):
                    path=Path(tmp)/f"part-{i}.js";path.write_text(script,encoding="utf-8")
                    check=subprocess.run([shutil.which("node"),"--check",str(path)],capture_output=True,text=True,encoding="utf-8",timeout=10)
                    self.assertEqual(check.returncode,0,check.stderr)


if __name__ == "__main__":
    unittest.main()
