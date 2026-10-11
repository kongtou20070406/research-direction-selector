"""External resources preserve the original presentation bytes and relocation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_dashboard as dashboard
import rds_hypergraph_view as viewer
from rds_quick import _inputs, MAX_FILES, MAX_TOTAL_BYTES


class PresentationAssetTests(unittest.TestCase):
    def test_public_strings_match_pre_extraction_bytes(self):
        # Hashes of evaluated literals at 9154f2f, including leading/trailing LF.
        expected = {
            (dashboard, "HTML"): "574422bc977986fd2fb78be81b7c01d1732630c472d0f4d458be5b8125c1d013",
            (viewer, "D3_BUNDLE"): "e74d82ae076d680d9d0220089f2014163a1807fe95da2fcaebd7a664d393a289",
            (viewer, "HTML"): "fe8b25688de5d9f926475e2010b42aec4488559c4b5887372b8b2fb4d44c1ea2",
            (viewer, "CSS"): "4966c7ec3cc48de888aa32912916046c17b7b9fddcca663202ebdfb826ad27db",
            (viewer, "JS"): "220388005e5cce03e39e57d510ca459c99dc12b5682b18012217f7a141eaba8a",
            (viewer, "PIXI_LICENSE"): "7ab03846880e67d8cefb2e1b62a049b23233c851cce42e56883d6efc1f9ebf8b",
            (viewer, "REPLICA_HTML"): "14a179803567168adfdf42b8b08e8fc0e5112a9c8464bd2ba84d9e56f2c3f143",
            (viewer, "REPLICA_EXTRA_CSS"): "67ddb13deba3723af5061ee060a89bfdf98907c6131abcd10ba1bd1e911d5d66",
            (viewer, "REPLICA_APP"): "694f089901f754cdb166aefa0c6fd11360122d6e4f102561c7aa74b5266317ec",
        }
        for (module, name), sha in expected.items():
            with self.subTest(module=module.__name__, name=name):
                value = getattr(module, name)
                self.assertIsInstance(value, str)
                self.assertEqual(hashlib.sha256(value.encode("utf-8")).hexdigest(), sha)

    def test_json_patch_resources_preserve_order_and_exact_source_bytes(self):
        for kind, patches, sha in (
            ("renderer", viewer._REPLICA_RENDERER_PATCHES,
             "223d7a643bf63e7b0afb0ec33d76d2cffa5f37ae50cffa175ca1c9239689ad7f"),
            ("worker", viewer._REPLICA_WORKER_PATCHES,
             "ef04458006efdb80a47033e1a09548521d852733ba50593f5eefef571aad3c50"),
        ):
            with self.subTest(kind=kind):
                raw = (ROOT / "scripts/assets/replica" / (kind + "-patches.json")).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
                self.assertEqual(json.loads(raw), patches)
                self.assertTrue(all(len(pair) == 2 and all(isinstance(s, str) for s in pair)
                                    for pair in patches))

    def test_isolated_import_and_export_from_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            program = (
                "import sys;sys.path.insert(0,sys.argv[1]);"
                "import rds_dashboard as d,rds_hypergraph_view as v;"
                "assert '__RDS_SNAPSHOT__' not in d.render_html({'state':{}});"
                "assert '__GRAPH_DATA__' not in v.render_html({'graph':None});"
                "print('PASS')")
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program,
                                     str(ROOT / "scripts")], cwd=directory,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "PASS")

    def test_quick_requires_explicit_asset_bindings_with_unchanged_bounds(self):
        assets = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "scripts/assets").rglob("*")
                        if p.is_file() and p.name != ".gitattributes")
        automatic, _ = _inputs(ROOT, [sys.executable, "scripts/rds_hypergraph_view.py"], [])
        self.assertFalse(any(path.relative_to(ROOT).as_posix().startswith("scripts/assets/")
                             for path in automatic))
        bound, raw = _inputs(ROOT, [sys.executable, "scripts/rds_hypergraph_view.py"],
                             ["data=" + path for path in assets])
        for path in assets:
            self.assertEqual(bound[ROOT / path], {"data"})
            self.assertEqual(raw[ROOT / path], (ROOT / path).read_bytes())
        self.assertEqual(MAX_FILES, 128)
        self.assertEqual(MAX_TOTAL_BYTES, 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
