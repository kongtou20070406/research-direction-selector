"""Readable agent interface identity, authority and actual offline CLI boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_hypergraph_readable import agent_input, graph_digest, load_readable, validate_readable, with_readable, MAX_BYTES
from rds_hypergraph_view import graph_view, display_records, render_html, replica_view, JS, REPLICA_APP
import test_rds_hypergraph_view as boundary


def result():
    return graph_view({"schema": 1, "nodes": [
        {"id": ident, "status": "UNKNOWN", "source": "synthetic"} for ident in ("__proto__", "constructor", "goal")],
        "hyperedges": [{"id": "__proto__", "premises": ["__proto__", "constructor"], "conclusion": "goal",
                        "status": "PROPOSED", "weight": 3, "source": "synthetic"}], "goals": ["goal"]}, "synthetic")


class ReadableInterfaceTests(unittest.TestCase):
    def test_two_layers_copy_identity_and_empty_template(self):
        original = result()
        before = deepcopy(original)
        bundle = agent_input(original)
        self.assertEqual(set(bundle), {"raw", "readable"})
        self.assertEqual(bundle["raw"], original["graph"])
        self.assertEqual(bundle["readable"]["graph_sha256"], graph_digest(original["graph"]))
        self.assertEqual(validate_readable(bundle["readable"], original), bundle["readable"])
        bundle["raw"]["nodes"][0]["status"] = "SUPPORTED"
        bundle["readable"]["nodes"]["goal"]["title"] = "changed"
        self.assertEqual(original, before)

    def test_readable_display_does_not_change_raw_authority_or_geometry(self):
        original = result()
        before = deepcopy(original)
        value = agent_input(original)["readable"]
        value["nodes"]["__proto__"] = {"title": "方法对比", "summary": "比较候选与对照。"}
        value["nodes"]["constructor"] = {"title": "方法对比", "summary": "独立的观测。"}
        value["hyperedges"]["__proto__"] = {"title": "共同检验", "summary": "两个前提共同用于这个候选结论。"}
        overlay = with_readable(original, value)
        shown = display_records(overlay)
        self.assertEqual(shown["nodes"]["__proto__"]["label"], "方法对比")
        self.assertEqual(shown["nodes"]["constructor"]["label"], "方法对比")
        for key in ("nodes", "hyperedges"):
            for ident, info in shown[key].items():
                base = display_records(original)[key][ident]
                self.assertEqual({k: info[k] for k in ("status_text", "outline", "kind", "shape", "color")},
                                 {k: base[k] for k in ("status_text", "outline", "kind", "shape", "color")})
        replica, base = replica_view(overlay), replica_view(original)
        self.assertEqual(replica["links"], base["links"])
        self.assertEqual(replica["nodes"]["h0"]["label"], "共同检验")
        self.assertEqual(replica["nodes"]["h0"]["rds"], base["nodes"]["h0"]["rds"])
        self.assertEqual(replica["nodes"]["h0"]["rds"]["members"], ["c0", "c1", "c2"])
        self.assertEqual(overlay["graph"], before["graph"])
        self.assertEqual(overlay["analysis"], before["analysis"])
        self.assertEqual(original, before)
        value["nodes"]["__proto__"]["title"] = "modified later"
        self.assertEqual(display_records(overlay)["nodes"]["__proto__"]["label"], "方法对比")

    def test_full_raw_identity_and_snapshot_changes_reject_stale_explanations(self):
        original = result()
        value = agent_input(original)["readable"]
        for mutate in (lambda g: g["nodes"][0].update(status="SUPPORTED"),
                       lambda g: g["hyperedges"][0].update(weight=4),
                       lambda g: g["nodes"][0].update(source="different"),
                       lambda g: g["nodes"].reverse(),
                       lambda g: g["hyperedges"][0]["premises"].reverse()):
            changed = deepcopy(original)
            mutate(changed["graph"])
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, "stale"):
                validate_readable(value, changed)
        changed = {**original, "snapshot_sha256": "a" * 64}
        with self.assertRaisesRegex(ValueError, "snapshot"):
            validate_readable(value, changed)

    def test_strict_contract_version_namespaces_text_and_extra_fields(self):
        original = result()
        template = agent_input(original)["readable"]
        bad = [dict(template, schema=True), dict(template, schema=2), dict(template, schema=1.0),
               dict(template, raw=original["graph"]), dict(template, graph=None),
               dict(template, nodes={"new": {"title": "new"}}), dict(template, hyperedges={"goal": {}}),
               dict(template, nodes={"goal": {"status": "SUPPORTED"}}),
               dict(template, nodes={"goal": {"title": ["not text"]}}),
               dict(template, graph={"title": "x" * 121}), dict(template, graph={"summary": "x" * 601}),
               dict(template, graph={"title": "\0"}), dict(template, graph={"title": "\ud800"}),
               agent_input(original)]
        for value in bad:
            with self.subTest(value=str(value)[:120]), self.assertRaises(ValueError):
                validate_readable(value, original)
        partial = dict(template, nodes={}, hyperedges={})
        self.assertEqual(validate_readable(partial, original), partial)

    def test_loader_rejects_duplicate_nonfinite_malformed_and_oversize_json(self):
        original = result()
        value = agent_input(original)["readable"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "说明.json"
            text = json.dumps(value)
            for bad in (text.replace('"schema": 1', '"schema": 1, "schema": 1'),
                        text.replace('"summary": ""', '"summary": NaN', 1),
                        '{"nodes":{"goal":{"title":"a","title":"b"}}}',
                        '[' * 1500 + ']' * 1500, '{', '"\\ud800"'):
                path.write_text(bad, encoding="utf-8")
                with self.subTest(bad=bad[:70]), self.assertRaises(ValueError):
                    load_readable(path, original)
            path.write_bytes(b' ' * (MAX_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "8 MiB"):
                load_readable(path, original)
            path.write_text(text, encoding="utf-8-sig")
            self.assertEqual(load_readable(path, original), value)

    def test_agent_text_is_escaped_and_survives_as_literal_text(self):
        original = result()
        value = agent_input(original)["readable"]
        attack = '</script><img src=x onerror=alert(1)> __GRAPH_JS__ __RDS_WORKER__ &\u2028\u2029 literal'
        value["nodes"]["goal"] = {"title": attack, "summary": attack}
        value["hyperedges"]["__proto__"] = {"title": attack, "summary": attack}
        page = render_html(with_readable(original, value))
        self.assertNotIn('</script><img', page)
        data = json.loads(re.search(r'<script id="snapshot" type="application/json">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(data["display"]["nodes"]["goal"]["summary"], attack)
        self.assertEqual(data["graph"], original["graph"])

    def test_both_renderers_use_summary_for_details_hover_search_without_worker_mutation(self):
        self.assertIn("display(r).summary,'readable-summary'", JS)
        self.assertIn("display(item.record)?.summary", JS)
        self.assertIn("summaryFor(n).toLowerCase().includes(query)", REPLICA_APP)
        self.assertIn("el('p',summaryFor(n),'readable-summary')", REPLICA_APP)
        self.assertIn("summaryFor(n).slice(0,140)", REPLICA_APP)
        self.assertIn("Object.hasOwn(map,id)", REPLICA_APP)
        self.assertNotIn("summary:n.", REPLICA_APP)

    def test_real_cli_two_layer_roundtrip_readonly_and_bad_annotation_atomic_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "原始.json"
            raw.write_text(json.dumps(result()["graph"]), encoding="utf-8")
            before = raw.read_bytes()
            command = [sys.executable, "-B", str(ROOT / "scripts/rds_hypergraph_view.py"),
                       "--root", str(root), "--hypergraph", str(raw)]
            run = subprocess.run(command + ["--export-agent-input"], cwd=root, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse((root / ".rds").exists())
            self.assertFalse((root / "rds-hypergraph.html").exists())
            bundle = json.loads(run.stdout)
            self.assertEqual(bundle["raw"], json.loads(before))
            bundle["readable"]["nodes"]["goal"] = {"title": "研究目的", "summary": "用两个共同前提评估候选解释。"}
            readable = root / "可读.json"
            readable.write_text(json.dumps(bundle["readable"], ensure_ascii=False), encoding="utf-8")
            output = root / "网页.html"
            run = subprocess.run(command + ["--readable-json", str(readable), "--output", str(output)],
                                 capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stderr)
            page = output.read_text(encoding="utf-8")
            self.assertIn("研究目的", page)
            self.assertIn("用两个共同前提评估候选解释", page)
            bundle["readable"]["nodes"]["bad"] = {"title": "unknown"}
            readable.write_text(json.dumps(bundle["readable"]), encoding="utf-8")
            bad_output = root / "invalid.html"
            run = subprocess.run(command + ["--readable-json", str(readable), "--output", str(bad_output)],
                                 capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse(bad_output.exists())
            self.assertEqual(raw.read_bytes(), before)
            self.assertFalse((root / ".rds").exists())

    def test_cli_protects_readable_input_and_hardlink_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, readable = root / "raw.json", root / "readable.html"
            original = result()
            raw.write_text(json.dumps(original["graph"]), encoding="utf-8")
            readable.write_text(json.dumps(agent_input(original)["readable"]), encoding="utf-8")
            before = readable.read_bytes()
            alias = root / "alias.html"
            os.link(readable, alias)
            for output in (readable, alias):
                run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_hypergraph_view.py"),
                    "--root", str(root), "--hypergraph", str(raw), "--readable-json", str(readable), "--output", str(output)],
                    capture_output=True, text=True, encoding="utf-8")
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("must not overwrite readable", run.stderr)
                self.assertEqual(readable.read_bytes(), before)


@unittest.skipUnless(shutil.which("node"), "Node required for production browser scripts")
class ReadableBrowserBoundaryTests(boundary._JSBoundaryTests):
    def test_production_canvas_reads_summary_as_text_and_searches_it(self):
        original = result()
        value = agent_input(original)["readable"]
        value["nodes"]["goal"] = {"title": "目的", "summary": "解释用途 </script><img src=x>"}
        value["hyperedges"]["__proto__"] = {"title": "联合检查", "summary": "两个前提缺一不可。"}
        overlay = with_readable(original, value)
        payload = {**overlay, "display": display_records(overlay)}
        self.run_js(JS, "const payload=JSON.parse(" + json.dumps(json.dumps(payload)) + ");\n" + r'''
const b=browser(payload);vm.runInContext(production,b.scope,{timeout:10000});b.flush();
const search=b.ids['hg-search'];search.value='解释用途';search.events.input();b.flush();
let found=b.ids['hg-search-results'].children.find(e=>e.tagName==='BUTTON');assert.ok(found);found.events.click();b.flush();
assert.match(b.text(b.ids['hg-detail']),/解释用途 <\/script><img src=x>/);
assert.ok(!b.descendants(b.ids['hg-detail']).some(e=>e.tagName==='IMG'));
search.value='联合检查';search.events.input();b.flush();found=b.ids['hg-search-results'].children.find(e=>e.tagName==='BUTTON');found.events.click();b.flush();
assert.match(b.text(b.ids['hg-detail']),/两个前提缺一不可/);
const links=b.descendants(b.ids['hg-detail']).filter(e=>e.tagName==='BUTTON');
assert.ok(links.some(e=>e.textContent==='proto'));assert.ok(links.some(e=>e.textContent==='constructor'));assert.ok(links.some(e=>e.textContent==='目的'));
console.log('Readable Canvas summary search, literal text and complete AND navigation PASS');
''')

    def test_production_replica_reads_summary_and_keeps_filter_navigation(self):
        original = result()
        value = agent_input(original)["readable"]
        value["nodes"]["goal"] = {"title": "目的", "summary": "解释用途 </script><img src=x>"}
        value["hyperedges"]["__proto__"] = {"title": "联合检查", "summary": "两个前提缺一不可。"}
        overlay = with_readable(original, value)
        payload = {**overlay, "display": display_records(overlay), "replica_view": replica_view(overlay)}
        self.run_js(REPLICA_APP, "const payload=JSON.parse(" + json.dumps(json.dumps(payload)) + ");\n" + r'''
const b=browser(payload);b.scope.localStorage={getItem:()=>JSON.stringify({search:'解释用途',growth:false}),setItem(){}};
b.scope.SIM_WORKER_MAIN='';b.scope.PIXI={Texture:{WHITE:{}}};
b.scope.GraphRenderer=class{
 constructor(){b.renderer=this;this.nodes=[];this.links=[];this.nodeLookup=new Map();this.width=1000;this.height=700;this.worker={onmessage(){},postMessage:d=>{this.message=structuredClone(d)},terminate(){}};}
 setData({nodes,links}){this.nodes=Object.entries(nodes).map(([id,n])=>({...n,id,x:0,y:0,getSize(){return 10}}));this.nodeLookup=new Map(this.nodes.map(n=>[n.id,n]));this.links=links.map(([s,t,rds])=>({source:this.nodeLookup.get(s),target:this.nodeLookup.get(t),rds}));}
 setForces(p){this.forces=structuredClone(p)}setOptions(){}changed(){}resetPan(){}zoomTo(){}setScale(){}setPan(){}
};
vm.runInContext(production,b.scope,{timeout:5000});
assert.ok(b.renderer.nodeLookup.has('c2'),'summary finds original goal');
b.renderer.onNodeClick(b.renderer.nodeLookup.get('c2'));
assert.match(b.text(b.ids['note-card']),/解释用途 <\/script><img src=x>/);
assert.ok(!b.descendants(b.ids['note-card']).some(e=>e.tagName==='IMG'));
const hub=b.descendants(b.ids['note-card']).find(e=>e.tagName==='BUTTON'&&e.textContent==='联合检查');assert.ok(hub);hub.events.click();
assert.match(b.text(b.ids['note-card']),/两个前提缺一不可/);
assert.ok(b.renderer.nodeLookup.has('c0'));assert.ok(b.renderer.nodeLookup.has('c1'),'all AND premises retained');
assert.ok(!JSON.stringify(b.renderer.message).includes('解释用途'),'summary excluded from worker payload');
console.log('Readable Replica summary search, literal text and complete AND navigation PASS');
''')


if __name__ == "__main__":
    unittest.main()
