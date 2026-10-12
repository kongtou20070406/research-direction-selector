"""Portable extraction regression against parent 7fe4810; no runtime Git."""
import ast
from contextlib import closing
import hashlib
import inspect
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import rds_cli as cli
import rds_reference_values as values
import rds_state as state_module

# Original definition AST hashes include bodies, signatures and decorators.
OLD_AST = {
    "canonical": "c893bd33fce469cb6999d65408a4c94f1498ae413e3adac315e8f7c5d00b8151",
    "digest": "e72d2ba961e1f64da1d7b2133a5f4f73decd22f7682be314b366fb08f17aa7e3",
    "require": "a412b18a09b9cef4f5da8b5fbce2f5fb6fc485ef18cedb75a2534631ea952fe8",
    "strict_json": "9ff06053ef79f6732e72fde5e103839bc4755116af933377032bd80ee4f7164b",
    "RDSState": "469144348318f1c573f2b455fc053d9f55aff997e6f6972387aa733e7cb59bf0",
}


def frozen_ast(node):
    if isinstance(node, ast.AST):
        # Python 3.12 adds empty type_params to unchanged function/class ASTs.
        return [type(node).__name__, [[key, frozen_ast(value)]
                for key, value in ast.iter_fields(node) if key != "type_params" or value]]
    if isinstance(node, list):
        return [frozen_ast(value) for value in node]
    return node


class ExtractionTests(unittest.TestCase):
    def test_original_definition_asts_are_unchanged(self):
        for name, expected in OLD_AST.items():
            module = state_module if name == "RDSState" else values
            with self.subTest(name=name):
                node = ast.parse(inspect.getsource(getattr(module, name))).body[0]
                raw = json.dumps(frozen_ast(node), ensure_ascii=True,
                                 separators=(",", ":")).encode("ascii")
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        node = ast.parse(inspect.getsource(cli.digest)).body[0]
        raw = json.dumps(frozen_ast(node), ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), OLD_AST["digest"])

    def test_facade_identity_and_version_literal(self):
        self.assertIs(cli.RDSState, state_module.RDSState)
        for name in ("canonical", "require", "strict_json", "READABLE_STATE_VERSIONS", "RESOURCES"):
            with self.subTest(name=name):
                self.assertIs(getattr(cli, name), getattr(values, name))
        self.assertIs(state_module.digest, values.digest)
        self.assertIsNot(cli.digest, values.digest)
        literal = re.search(r'^VERSION = "([^"]+)"$',
                            Path(cli.__file__).read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(literal)
        self.assertEqual(literal.group(1), values.VERSION)
        self.assertEqual(cli.VERSION, values.VERSION)
        self.assertIn(cli.VERSION, values.READABLE_STATE_VERSIONS)

    def test_lower_modules_have_no_upward_imports(self):
        for module in (values, state_module):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                       for alias in node.names}
            imports.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
            self.assertNotIn("rds_cli", imports)
            self.assertNotIn("rds_quick", imports)
            if module is values:
                self.assertEqual(imports, {"json", "rds_core"})

    def test_isolated_lower_module_imports(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "import rds_state, rds_reference_values; "
                "assert 'rds_cli' not in sys.modules; "
                "assert 'rds_campaign' not in sys.modules; "
                "assert 'rds_workspace' not in sys.modules")
        run = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(SCRIPTS)],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_decorators_are_preserved(self):
        for name in ("read_state", "invariants", "event", "artifact"):
            self.assertIsInstance(inspect.getattr_static(state_module.RDSState, name), staticmethod)
        for name in ("snapshot", "transaction", "_transaction"):
            self.assertTrue(hasattr(getattr(state_module.RDSState, name), "__wrapped__"))

    def test_cli_and_lower_monkeypatch_seams(self):
        with patch.object(cli, "canonical", return_value="frozen") as encoder:
            self.assertEqual(cli.digest({}), hashlib.sha256(b"frozen").hexdigest())
            self.assertEqual(cli.digest(b"raw"), hashlib.sha256(b"raw").hexdigest())
            encoder.assert_called_once_with({})
        with patch.object(values, "canonical", return_value="leaf"):
            self.assertEqual(values.digest({}), hashlib.sha256(b"leaf").hexdigest())
        self.assertIs(cli.time, state_module.time)
        self.assertIs(cli.sqlite3, state_module.sqlite3)

    def test_engine_binds_each_new_file_without_optional_files(self):
        original = Path.read_bytes
        before = cli.engine_id()
        for name in ("rds_reference_values.py", "rds_state.py"):
            seen = []

            def changed(path):
                seen.append(path.name)
                raw = original(path)
                return raw + b"\n# identity fixture\n" if path.name == name else raw

            with self.subTest(name=name), patch.object(Path, "read_bytes", changed):
                self.assertNotEqual(cli.engine_id(), before)
                self.assertIn(name, seen)


class ReferenceValueTests(unittest.TestCase):
    def test_json_and_raw_bytes_digest(self):
        value = {"b": [True, None], "a": "text"}
        self.assertEqual(values.canonical(value), '{"a":"text","b":[true,null]}')
        for function in (cli.digest, values.digest):
            self.assertEqual(function(value=value),
                             hashlib.sha256(values.canonical(value).encode("utf-8")).hexdigest())
            self.assertEqual(function(value=b"\x00\xff"), hashlib.sha256(b"\x00\xff").hexdigest())
            with self.assertRaises(TypeError):
                function(bytearray(b"raw"))
            with self.assertRaises(ValueError):
                function({"x": float("nan")})

    def test_json_errors_and_nested_duplicates(self):
        for raw, message in (("{\"x\":1,\"x\":2}", "Duplicate JSON key: x"),
                             ('{"outer":{"x":1,"x":2}}', "Duplicate JSON key: x"),
                             ("NaN", "Non-finite JSON value: NaN"),
                             ("Infinity", "Non-finite JSON value: Infinity"),
                             ("-Infinity", "Non-finite JSON value: -Infinity")):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, re.escape(message)):
                values.strict_json(raw)
        with self.assertRaises(json.JSONDecodeError):
            values.strict_json("{")
        recursion = RecursionError("fixture")
        with patch.object(values.json, "loads", side_effect=recursion):
            with self.assertRaisesRegex(ValueError, "JSON nesting exceeds the parser limit") as caught:
                values.strict_json("[]")
            self.assertIs(caught.exception.__cause__, recursion)
        self.assertEqual(values.strict_json(b'{"x":1}'), {"x": 1})

    def test_require_preserves_message_and_truthiness(self):
        values.require([1], "unused")
        with self.assertRaisesRegex(ValueError, "sentinel"):
            values.require([], "sentinel")


class ReferenceStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rds-reference-services-")
        self.addCleanup(temporary.cleanup)
        self.state = state_module.RDSState(temporary.name)

    def seed(self, version=values.VERSION):
        self.state.directory.mkdir()
        body = {"version": version, "contract": {"claim": "fixture"},
                "contract_sha256": values.digest({"claim": "fixture"}),
                "hypotheses": {"H1": {}}, "plans": {},
                "budget": {"limits": {"runs": 3, "runtime_ms": 100},
                           "spent": {"runs": 0, "runtime_ms": 0},
                           "reserved": {"runs": 0, "runtime_ms": 0}}}
        with closing(sqlite3.connect(self.state.db_path)) as db, db:
            db.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("INSERT INTO state VALUES (1,?)", (values.canonical(body),))
        return body

    def test_all_readable_versions_snapshot_without_rewriting(self):
        body = self.seed()
        for version in sorted(values.READABLE_STATE_VERSIONS):
            body["version"] = version
            with closing(sqlite3.connect(self.state.db_path)) as db, db:
                db.execute("UPDATE state SET body=? WHERE id=1", (values.canonical(body),))
            before = self.state.db_path.read_bytes()
            with self.state.snapshot() as (db, snapshot):
                self.assertEqual(snapshot["version"], version)
                self.assertEqual(snapshot["active_branch"], "main")
                self.assertEqual(snapshot["branches"]["main"]["hypotheses"], ["H1"])
                self.assertEqual(snapshot["baseline_cache"], {})
                self.assertEqual(db.execute("SELECT body FROM state").fetchone()[0], values.canonical(body))
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("UPDATE state SET body='{}'")
            self.assertEqual(self.state.db_path.read_bytes(), before)
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                db.execute("SELECT body FROM state")

    def test_unknown_version_and_integrity_rejections_do_not_rewrite(self):
        body = self.seed("unknown-version")
        for message in ("Incompatible state version", "Contract integrity failure"):
            before = self.state.db_path.read_bytes()
            with self.assertRaisesRegex(ValueError, message), self.state.snapshot():
                pass
            self.assertEqual(self.state.db_path.read_bytes(), before)
            body.update(version=values.VERSION, contract_sha256="0" * 64)
            with closing(sqlite3.connect(self.state.db_path)) as db, db:
                db.execute("UPDATE state SET body=?", (values.canonical(body),))

    def test_public_connect_write_and_creation_guards(self):
        for options, message in (({}, "transaction"), ({"create": True}, "transaction"),
                                 ({"create": True, "readonly": True}, "cannot create state"),
                                 ({"readonly": True}, "not initialized")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, message):
                self.state.connect(**options)
            self.assertFalse(self.state.directory.exists())

    def test_campaign_and_workspace_guards_precede_creation(self):
        for target in ("rds_campaign.enforce", "rds_workspace.check_reference_admission"):
            with self.subTest(target=target), patch(target, side_effect=ValueError("guard sentinel")) as guard:
                with self.assertRaisesRegex(ValueError, "guard sentinel"), self.state.transaction(create=True):
                    pass
                guard.assert_called_once()
                self.assertFalse(self.state.directory.exists())

    def test_workspace_recheck_rolls_back_transaction(self):
        original = self.seed()
        with patch("rds_workspace.check_reference_admission",
                   side_effect=[None, ValueError("commit guard sentinel")]) as guard:
            with self.assertRaisesRegex(ValueError, "commit guard sentinel"):
                with self.state.transaction() as (db, snapshot):
                    snapshot["budget"]["spent"]["runs"] = 1
            self.assertEqual(guard.call_count, 2)
        with self.state.snapshot() as (reader, snapshot):
            self.assertEqual(snapshot["budget"], original["budget"])
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            db.execute("SELECT body FROM state")

    def test_transaction_rollback_and_invariants(self):
        original = self.seed()
        for fail_inside in (True, False):
            with self.subTest(fail_inside=fail_inside), self.assertRaises(ValueError):
                with self.state.transaction() as (_, snapshot):
                    snapshot["budget"]["reserved"]["runs"] = 1
                    if fail_inside:
                        raise ValueError("body sentinel")
            with self.state.snapshot() as (_, snapshot):
                self.assertEqual(snapshot["budget"], original["budget"])

    def test_native_schema_append_only_and_artifact_identity(self):
        with self.state.transaction(create=True) as (db, _):
            with patch.object(cli.time, "time_ns", return_value=123):
                self.assertEqual(self.state.event(db, "fixture"), 1)
            self.assertEqual(values.strict_json(db.execute("SELECT body FROM events").fetchone()[0]),
                             {"kind": "fixture", "time_ns": 123})
            raw = b"\x00\xff"
            sha = self.state.artifact(db, raw)
            self.assertEqual(self.state.artifact(db, raw), sha)
            self.assertEqual(db.execute("SELECT sha,raw FROM artifacts").fetchall(), [(values.digest(raw), raw)])
            db.execute("INSERT INTO receipts VALUES ('run', 'sha', '{}')")
            for table in ("events", "artifacts", "receipts"):
                for statement in (f"DELETE FROM {table}", f"UPDATE {table} SET body='{{}}'"
                                  if table != "artifacts" else "UPDATE artifacts SET raw=X'00'"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                        db.execute(statement)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(db.isolation_level, None)

    def test_legacy_json_creation_guard(self):
        self.state.directory.mkdir()
        legacy = self.state.directory / "contract.json"
        legacy.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Legacy v5 JSON state found"), self.state.transaction(create=True):
            pass
        self.assertEqual(legacy.read_text(encoding="utf-8"), "{}")
        self.assertFalse(self.state.db_path.exists())


if __name__ == "__main__":
    unittest.main()
