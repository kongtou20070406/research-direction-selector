"""Compact JSON equivalence and source-bound CLI evidence, using stdlib fixtures."""
import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import rds_core
import rds_cli


def legacy_canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def legacy_digest(value):
    raw = value if isinstance(value, bytes) else legacy_canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def outcome(function, value):
    try:
        return ("value", function(value))
    except Exception as exc:
        return (type(exc), str(exc))


class CoreSemanticsTests(unittest.TestCase):
    def test_json_values_are_byte_equivalent(self):
        vectors = [None, True, False, 0, -1, 2**4096, 0.0, -0.0, 1e-300,
                   1e300, "", "\u7a7a\u6295\U0001f600", "\x00\n\t\"\\",
                   "\ud800", [], {}, (1, 2), {1: "integer key", 2: None},
                   {"z": [True, None, -0.0], "a": {"nested": "\u96ea"}},
                   json.loads('{"a":1,"a":2}')]
        nested = 1
        for _ in range(60):
            nested = [nested]
        vectors.append(nested)
        for value in vectors:
            with self.subTest(value_type=type(value).__name__):
                expected = legacy_canonical(value)
                self.assertEqual(rds_core.canonical(value=value), expected)
                self.assertEqual(rds_cli.canonical(value=value), expected)
                self.assertEqual(outcome(rds_core.object_digest, value),
                                 outcome(legacy_digest, value))
                self.assertEqual(outcome(rds_cli.digest, value),
                                 outcome(legacy_digest, value))

    def test_golden_compact_encoding(self):
        self.assertEqual(rds_core.canonical({"z": "\u96ea", "a": [True, None, -0.0]}),
                         '{"a":[true,null,-0.0],"z":"\u96ea"}')

    def test_errors_match_without_new_validation_or_coercion(self):
        cycle = []
        cycle.append(cycle)
        deep = 0
        for _ in range(sys.getrecursionlimit() + 10):
            deep = [deep]
        vectors = [float("nan"), float("inf"), -float("inf"),
                   {"x": float("nan")}, b"json", bytearray(b"json"),
                   memoryview(b"json"), {1, 2}, object(), {(1, 2): 3},
                   {1: "number", "1": "text"}, cycle, deep, 10**5000]
        for index, value in enumerate(vectors):
            with self.subTest(vector=index):
                self.assertEqual(outcome(rds_core.canonical, value),
                                 outcome(legacy_canonical, value))
                self.assertEqual(outcome(rds_cli.canonical, value),
                                 outcome(legacy_canonical, value))
                self.assertEqual(outcome(rds_cli.digest, value),
                                 outcome(legacy_digest, value))

    def test_raw_buffers_are_hashed_unchanged(self):
        class BytesSubclass(bytes):
            pass
        for value in (b"", b"\x00\xff", b'{"a":1}', BytesSubclass(b"json"),
                      bytearray(b"buffer"), memoryview(b"buffer")):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(rds_core.bytes_digest(value), hashlib.sha256(value).hexdigest())
                self.assertEqual(outcome(rds_cli.digest, value), outcome(legacy_digest, value))
        for value in (None, "json", 1, {}, []):
            self.assertEqual(outcome(rds_core.bytes_digest, value),
                             outcome(lambda raw: hashlib.sha256(raw).hexdigest(), value))

    def test_object_and_bytes_contracts_stay_distinct(self):
        with self.assertRaises(TypeError):
            rds_core.object_digest(b"{}")
        with self.assertRaises(TypeError):
            rds_core.bytes_digest({})
        self.assertEqual(rds_core.object_digest({}), rds_core.bytes_digest(b"{}"))
        self.assertNotEqual(rds_core.object_digest("{}"), rds_core.bytes_digest(b"{}"))
        self.assertEqual(rds_core.object_digest("\u96ea"),
                         hashlib.sha256('"\u96ea"'.encode("utf-8")).hexdigest())

    def test_public_value_keywords_and_cli_canonical_seam(self):
        for function in (rds_cli.canonical, rds_cli.digest, rds_core.canonical,
                         rds_core.object_digest):
            self.assertEqual(str(inspect.signature(function)), "(value)")
        self.assertEqual(rds_cli.digest(value={"x": 1}), legacy_digest({"x": 1}))
        with patch.object(rds_cli, "canonical", return_value="frozen") as encoder:
            self.assertEqual(rds_cli.digest({}), rds_core.bytes_digest(b"frozen"))
            encoder.assert_called_once_with({})
            encoder.reset_mock()
            self.assertEqual(rds_cli.digest(b"raw"), rds_core.bytes_digest(b"raw"))
            encoder.assert_not_called()

    def test_duplicate_keys_and_nonfinite_parser_errors_remain_local(self):
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key: x"):
            rds_cli.strict_json('{"x":1,"x":2}')
        with self.assertRaisesRegex(ValueError, "Non-finite JSON value: NaN"):
            rds_cli.strict_json('{"x":NaN}')

    def test_leaf_imports_and_isolated_callers(self):
        tree = ast.parse((SCRIPTS / "rds_core.py").read_text(encoding="utf-8"))
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        self.assertEqual(imports, {"hashlib", "json"})
        self.assertFalse(any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)))
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "import rds_core, rds_cli; "
                "assert rds_cli.digest(value={}) == rds_core.object_digest({}); "
                "assert rds_cli.digest(value=b'{}') == rds_core.bytes_digest(b'{}'); "
                "print('isolated PASS')")
        for flags in (("-I",), ("-I", "-S")):
            run = subprocess.run([sys.executable, *flags, "-B", "-c", code, str(SCRIPTS)],
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("isolated PASS", run.stdout)

    def test_isolated_probe_roundtrip(self):
        payload = {"source": "def control(x): return x\ndef treatment(x): return 2*x\n",
                   "data": "sample_id,x,y\na,1,2\n", "hypothesis": {}}
        run = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPTS / "rds_probe.py")],
                             input=rds_core.canonical(payload), capture_output=True,
                             text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["gain"], "1")

    def test_quick_discovers_shared_leaf(self):
        from rds_quick import _inputs
        files, raw = _inputs(ROOT, [sys.executable, "scripts/rds_cli.py"], [])
        path = (SCRIPTS / "rds_core.py").resolve()
        self.assertIn("code", files[path])
        self.assertEqual(raw[path], path.read_bytes())

    def test_relocated_plugin_includes_and_imports_shared_leaf(self):
        import build_plugins
        with tempfile.TemporaryDirectory(prefix="rds-core-package-") as tmp:
            output = Path(tmp) / "export"
            build_plugins.build(output, ROOT)
            moved = Path(tmp) / "relocated"
            output.rename(moved)
            for host in ("claude", "codex", "omp"):
                with self.subTest(host=host):
                    scripts = moved / host / build_plugins.NAME / "skills" / build_plugins.NAME / "scripts"
                    self.assertEqual((scripts / "rds_core.py").read_bytes(),
                                     (SCRIPTS / "rds_core.py").read_bytes())
                    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                            "import rds_core, rds_cli; "
                            "assert rds_cli.digest({}) == rds_core.object_digest({}); "
                            "assert len(rds_cli.engine_id()) == 64")
                    run = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(scripts)],
                                         cwd=tmp, capture_output=True, text=True, timeout=15)
                    self.assertEqual(run.returncode, 0, run.stderr)


class CoreIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rds-core-identity-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.scripts = self.directory / "scripts"
        self.scripts.mkdir()
        for path in SCRIPTS.glob("*.py"):
            shutil.copyfile(path, self.scripts / path.name)
        self.root = self.directory / "project"
        self.root.mkdir()
        (self.root / "model.py").write_text(
            "def control(x): return x\ndef treatment(x): return 2*x\n", encoding="utf-8")
        (self.root / "dev.csv").write_text("sample_id,x,y\na,1,2\n", encoding="utf-8")
        self.contract = {
            "project_id": "core-source-fixture", "claim": "Synthetic source gate fixture",
            "primary_metric": {"name": "mse", "direction": "min", "min_useful_delta": "1/100"},
            "evaluation_scope": "finite_locked_dataset", "baseline_source": "model.py",
            "budget": {"limits": {"runtime_ms": 30000, "runs": 3},
                       "confirmation_floor": {"runtime_ms": 1000, "runs": 1}},
            "splits": {"dev": {"role": "development", "path": "dev.csv",
                               "cohort": "dev", "prior_exposure": "exposed"}}}
        self.hypothesis = {"id": "H1", "type": "task_gain", "proposition": "Fixture",
                           "falsifier": "Precommitted scalar comparison"}
        self.plan = {"id": "P1", "hypothesis_id": "H1", "split_id": "dev", "purpose": "explore",
                     "source": "model.py", "resources": {"runtime_ms": 10000, "runs": 1}}

    def call(self, *args, spec=None, flag="--spec", ok=True):
        if spec is not None:
            path = self.root / "input.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            args = (*args, flag, str(path))
        run = subprocess.run(
            [sys.executable, "-S", "-B", str(self.scripts / "rds_cli.py"), "--root", str(self.root), *args],
            env={**os.environ, "RDS_USAGE_DB": str(self.directory / "usage.sqlite3")},
            cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=15)
        if ok:
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            return json.loads(run.stdout)
        self.assertNotEqual(run.returncode, 0, run.stdout)
        return run.stderr

    def initialize(self):
        self.call("init", spec=self.contract, flag="--contract")
        self.call("hypothesis", "add", spec=self.hypothesis)

    def change_leaf(self):
        path = self.scripts / "rds_core.py"
        path.write_bytes(path.read_bytes() + b"\n# Source identity mutation fixture.\n")

    def test_leaf_bytes_change_engine_and_refuse_stale_admission(self):
        self.initialize()
        before = self.call("status")
        self.change_leaf()
        code = "import sys; sys.path.insert(0, sys.argv[1]); import rds_cli; print(rds_cli.engine_id())"
        identity = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(self.scripts)],
                                  capture_output=True, text=True, timeout=15)
        self.assertEqual(identity.returncode, 0, identity.stderr)
        self.assertNotEqual(identity.stdout.strip(), before["engine_sha256"])
        error = self.call("plan", "create", spec=self.plan, ok=False)
        self.assertIn("Verifier version changed; a new contract is required", error)
        after = self.call("status")
        self.assertEqual(after["plans"], before["plans"])
        self.assertEqual(after["budget"], before["budget"])

    def test_leaf_change_refuses_reserved_run_without_spending(self):
        self.initialize()
        self.call("plan", "create", spec=self.plan)
        before = self.call("status")
        self.change_leaf()
        error = self.call("run", "execute", "--id", "P1", ok=False)
        self.assertIn("Verifier changed after admission", error)
        after = self.call("status")
        self.assertEqual(after["plans"], before["plans"])
        self.assertEqual(after["budget"], before["budget"])

    def test_leaf_change_refuses_old_receipt_assessment(self):
        self.initialize()
        self.call("plan", "create", spec=self.plan)
        receipt = self.call("run", "execute", "--id", "P1")
        self.assertEqual(receipt["run_status"], "SUCCEEDED")
        before = self.call("status")
        self.change_leaf()
        error = self.call("decide", "--run", receipt["run_id"], ok=False)
        self.assertIn("Verifier changed; old receipts require versioned review", error)
        after = self.call("status")
        self.assertEqual(after["plans"], before["plans"])
        self.assertEqual(after["budget"], before["budget"])


if __name__ == "__main__":
    unittest.main()
