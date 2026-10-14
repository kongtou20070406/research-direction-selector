"""Frozen output contracts for the B7 rendering and scalar cycle cuts."""
import ast
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_hypergraph_archive as archive
import rds_hypergraph_view as view
import rds_probe as probe
import rds_scalar_verify as scalar


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False,
                           separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def graph_fixture():
    rows = [
        {"id": "claim", "label": "Quantity 12345678901234567890123456789012 <>&",
         "status": "SUPPORTED"},
        {"id": "owned:run:job", "record_kind": "run", "status": "SUPPORTED",
         "outcome": "SUCCEEDED"},
        {"id": "owned:receipt:job", "record_kind": "receipt", "status": "SUPPORTED",
         "outcome": "SUCCEEDED", "run_status": "FAILED"},
        {"id": "owned:fact:run.job.failed", "status": "SUPPORTED",
         "owned_fact": {"value": False, "reliable": True}},
        {"id": "owned:output:job", "status": "UNKNOWN", "interpretation": "MISSING",
         "run_id": "job", "source": {"locator": "missing declared output job:out.json"}},
        {"id": "owned:artifact:" + "a" * 64, "record_kind": "artifact",
         "status": "SUPPORTED", "source": {"file": "directory/.json",
             "sha256": "b" * 64, "locator": "synthetic:artifact"}},
    ]
    for row in rows:
        row.setdefault("source", "synthetic:b7")
    return {"schema": 1, "nodes": rows, "hyperedges": [
        {"id": "edge", "premises": ["claim", "owned:run:job"],
         "conclusion": "owned:receipt:job", "status": "PROPOSED",
         "source": "synthetic:b7"}], "goals": ["claim"]}


def scalar_fixture(source):
    return {"schema": 1, "kind": "scalar_threshold", "source": source,
            "formal": {"domain": ["0", "2"], "threshold": "1"}}


def frozen_outputs():
    graph = graph_fixture()
    result = view.graph_view(graph, "synthetic:b7")
    sources = ("def control(x): return 0\ndef treatment(x): return x\n",
               "def control(x): return 2\ndef treatment(x): return 0\n",
               "def control(x): return 0\ndef treatment(x): return 1/x\n")
    verification = [scalar.verify(scalar_fixture(source)) for source in sources]
    payload = {"source": sources[0], "data": "sample_id,x,y\na,1,1\nb,2,2\n",
               "hypothesis": {"formal": {"kind": "strict_algebraic_threshold",
                   "domain": ["0", "2"], "threshold": "1", "max_loss": "1"}}}
    return {"viewer_html": digest(view.render_html(result)),
            "missing_html": digest(view.render_html({"status": "MISSING", "graph": None})),
            "archive_display": digest(archive.build_archive_display(graph)),
            "verifiers": digest(verification), "execution": digest(probe.execute(payload))}


def archive_html_fixture(module=archive):
    """Synthetic pinned assets exercise template assembly without downloads."""
    renderer = b"synthetic renderer\r\n"
    pixi = b"synthetic pixi"
    original_read = Path.read_bytes

    def read_bytes(path):
        if path.name == "renderer.js":
            return renderer
        if path.name == "b7-pixi.js":
            return pixi
        return original_read(path)

    bindings = {"REPLICA_FILES": {"renderer.js": hashlib.sha256(
        renderer.replace(b"\r\n", b"\n")).hexdigest()},
        "PIXI_SHA256": hashlib.sha256(pixi).hexdigest(),
        "patch_replica_renderer": lambda source: "patched </script> " + source}

    graph = graph_fixture()
    bundle = {"display": module.build_archive_display(graph), "raw": b"original raw </script>"}
    with patch.object(Path, "read_bytes", read_bytes), patch.multiple(module, create=True, **bindings), \
            patch.multiple(view, **bindings):
        return module.render_archive_html(bundle, "synthetic", "b7-pixi.js")


class CycleExtractionTests(unittest.TestCase):
    def test_frozen_rendering_and_verifier_bytes(self):
        self.assertEqual(frozen_outputs(), FROZEN_OUTPUTS)

    def test_frozen_archive_html_bytes(self):
        self.assertEqual(digest(archive_html_fixture()), FROZEN_ARCHIVE_HTML)

    def test_legacy_exports_share_the_extracted_implementations(self):
        import rds_hypergraph_render as render
        import rds_scalar_values as values
        self.assertIs(probe.parse_source, values.parse_source)
        self.assertIs(probe.rational, values.rational)
        self.assertIs(scalar.parse_source, values.parse_source)
        for name in ("D3_BUNDLE", "PIXI_LICENSE", "REPLICA_FILES", "readable_text",
                     "display_record", "_replace_once", "_REPLICA_RENDERER_PATCHES"):
            self.assertIs(getattr(view, name), getattr(render, name))
        self.assertIs(archive.display_record, render.display_record)
        self.assertIs(archive.patch_replica_renderer, render.patch_replica_renderer)

    def test_extracted_modules_do_not_depend_on_their_consumers(self):
        for name, forbidden in (("rds_hypergraph_render", {"rds_hypergraph_view", "rds_hypergraph_archive"}),
                                ("rds_scalar_values", {"rds_probe", "rds_scalar_verify"})):
            tree = ast.parse((ROOT / "scripts" / (name + ".py")).read_text(encoding="utf-8"))
            imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                           for alias in node.names)
            self.assertFalse(imports & forbidden)
            self.assertTrue(all(module.split(".")[0] in sys.stdlib_module_names for module in imports))

    def test_scalar_certificate_replay_and_tampering(self):
        spec = scalar_fixture("def control(x): return 0\ndef treatment(x): return x\n")
        before = deepcopy(spec)
        answer = scalar.verify(spec)
        self.assertEqual(answer["status"], "PASS")
        self.assertTrue(scalar.check_certificate(spec, answer["certificate"]))
        changed = deepcopy(answer["certificate"])
        changed["verdict"] = "FAIL"
        self.assertFalse(scalar.check_certificate(spec, changed))
        self.assertEqual(spec, before)

    def test_parser_preserves_rejections_and_limits(self):
        valid = "def control(x): return 0\ndef treatment(x): return x\n"
        for source in (valid.replace("return x", "return x**5"),
                       valid.replace("return x", "return True"),
                       valid.replace("treatment(x)", "treatment(x=1)"),
                       valid.replace("return x", "return abs(x)"),
                       valid + "#" * 8192, valid.replace("return x", "return 1000001")):
            with self.subTest(source=source[:80]), self.assertRaises(ValueError):
                probe.parse_source(source)
        for value in (True, 0.5, "nan", "1/0", "1" * 81):
            with self.subTest(value=value), self.assertRaises((ValueError, ZeroDivisionError)):
                probe.rational(value)
        self.assertEqual(probe.rational("1/2"), Fraction(1, 2))
        self.assertEqual(ast.dump(probe.parse_source(valid)["treatment"]),
                         "Name(id='x', ctx=Load())")

    def test_rendering_does_not_change_source_records(self):
        graph = graph_fixture()
        before = deepcopy(graph)
        result = view.graph_view(graph, "synthetic:b7")
        view.render_html(result)
        archive.build_archive_display(graph)
        self.assertEqual(graph, before)


FROZEN_OUTPUTS = {
    'viewer_html': 'affb6440599a5eb048fcdd2b1108440ee1187ebcbee9963f1d616e9c7f399aaa',
    'missing_html': '14dc5921359f80f171cf349cfb834a1d627cb1470177fb17fd2113b6bec5c39b',
    'archive_display': 'e3a468a921fa5c63113e63f7af99cc86043204648ac2c614b30407e17a3b194e',
    'verifiers': '1f1b82e1ddbcf880a015a23594cbd94fcad193b308436921e6af176773e5b0de',
    'execution': '6588719f2fed1d0da3ec8f5ac618487d06b598bcf1185dd948837a934ae2f792',
}

FROZEN_ARCHIVE_HTML = 'e84cf2350af3e23a8995d7879f933a05b33229eff21051fe4679161bdfc00d2b'


if __name__ == "__main__":
    unittest.main()
