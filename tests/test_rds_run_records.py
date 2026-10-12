"""Frozen original run-row decoder versus the extracted pure codec."""
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parents[1] / "scripts")]
import test_rds_project as base
import rds_project
from rds_project import ProjectStore, RunIntegrityError, TERMINAL, canonical, digest
from rds_run_records import decode_run_row


ORIGINAL_FUNCTION_SHA256 = "b9a4965199bdc20a862bc1173b97ee2433355c99b3e6244e88d3784930455395"
ORIGINAL_SOURCE = r'''    @staticmethod
    def _run_row(row):
        """Validate retained writer values before displaying, dispatching or settling them.

        No schema migration, content repair or new evidence claim. Optional later
        aliases/policy fields are not required, and hot-loop reads remain keyed by id.
        """
        prefix = f"Project run integrity failure: run {row['id']!r} body "
        suffix = "; inspect retained state"

        def reject(reason):
            raise RunIntegrityError(prefix + reason + suffix)

        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    reject("repeats a JSON key")
                result[key] = value
            return result

        try:
            run = json.loads(row["body"], object_pairs_hook=unique)
        except RunIntegrityError:
            raise
        except (ValueError, TypeError, RecursionError):
            reject("is not valid JSON")
        if not isinstance(run, dict):
            reject("is not an object")
        if run.get("id") != row["id"]:
            reject("names a different run")
        if not isinstance(run.get("status"), str) or run["status"] not in {"RESERVED", "RUNNING", *TERMINAL} or run["status"] != row["status"]:
            reject("does not match its recorded status")
        if not isinstance(run.get("manifest"), dict) or run["manifest"].get("id") != row["id"]:
            reject("has no matching manifest")
        try:
            canonical(run).encode("utf-8")
            manifest_sha = digest(run["manifest"])
        except (ValueError, RecursionError):
            reject("has no canonical encoding")
        if manifest_sha != run.get("manifest_sha256"):
            reject("has a Manifest integrity failure")
        required = {"resource_estimates", "protocol", "executor", "executor_sha256", "attempt_id",
                    "worker_pid", "pid", "started_at", "finished_at", "observed_wall_seconds", "scheduler"}
        if not required <= run.keys():
            reject("is missing execution state")
        if (not isinstance(run["resource_estimates"], dict)
                or not isinstance(run["protocol"], dict)
                or not isinstance(run["executor"], str) or not run["executor"]
                or not isinstance(run["executor_sha256"], str)
                or (run["attempt_id"] is not None and not isinstance(run["attempt_id"], str))
                or (run["scheduler"] is not None and not isinstance(run["scheduler"], dict))):
            reject("has invalid execution state")
        for value in [run["observed_wall_seconds"], *run["resource_estimates"].values()]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                reject("has invalid recorded costs")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                reject("has invalid recorded costs")
        for key in ("worker_pid", "pid"):
            if run[key] is not None and (type(run[key]) is not int or run[key] <= 0):
                reject("has an invalid process identity")
        return run
'''


def original_decoder():
    namespace = {"json": json, "math": math, "RunIntegrityError": RunIntegrityError,
                 "canonical": canonical, "digest": digest, "TERMINAL": TERMINAL}
    exec("class Original:\n" + ORIGINAL_SOURCE, namespace)
    return namespace["Original"]._run_row


def outcome(decoder, row):
    try:
        return ("value", decoder(row))
    except Exception as exc:
        context = exc.__context__
        cause = exc.__cause__
        return ("error", type(exc), str(exc).encode("utf-8"),
                type(context) if context is not None else None,
                str(context).encode("utf-8") if context is not None else None,
                type(cause) if cause is not None else None,
                str(cause).encode("utf-8") if cause is not None else None)


class RunRecordCodecTests(unittest.TestCase):
    def setUp(self):
        self.fx = base.ProjectTests("test_execution_policy_is_opt_in_and_frozen")
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)

    def test_frozen_source_and_writer_row_differential(self):
        self.assertEqual(hashlib.sha256(ORIGINAL_SOURCE.encode("utf-8")).hexdigest(),
                         ORIGINAL_FUNCTION_SHA256)
        original_body = ORIGINAL_SOURCE.splitlines(keepends=True)[2:]
        codec_body = inspect.getsource(decode_run_row).splitlines(keepends=True)[1:]
        self.assertEqual(codec_body,
                         [line[4:] if line.strip() else line for line in original_body])
        store = self.fx.store
        store.register(self.fx.spec())
        with store._db(True) as db:
            row = dict(db.execute("SELECT id,status,body FROM runs WHERE id='r1'").fetchone())
        run = json.loads(row["body"])
        old = original_decoder()
        new = lambda value: decode_run_row(value, RunIntegrityError, canonical, digest, TERMINAL)

        def body(**updates):
            return canonical({**run, **updates})

        def missing(key):
            return canonical({name: value for name, value in run.items() if name != key})

        cases = [("writer", row["body"], True),
                 ("malformed", "{", False),
                 ("duplicate root", row["body"][:-1] + ',"id":"r1"}', False),
                 ("duplicate nested", row["body"].replace('"manifest":{',
                    '"manifest":{"id":"r1",', 1), False),
                 ("array", "[]", False), ("null", "null", False),
                 ("number", "1", False), ("boolean", "true", False),
                 ("wrong id", body(id="r2"), False),
                 ("id before status", body(id="r2", status=[]), False),
                 ("wrong status", body(status="RUNNING"), False),
                 ("status type", body(status=[]), False),
                 ("missing manifest", missing("manifest"), False),
                 ("manifest identity", body(manifest={**run["manifest"], "id": "r2"}), False),
                 ("manifest digest", body(manifest={**run["manifest"], "timeout_seconds": 123}), False),
                 ("manifest before cost", body(manifest_sha256="0" * 64,
                    observed_wall_seconds=-1), False),
                 ("digest field", body(manifest_sha256="0" * 64), False),
                 ("nan", row["body"][:-1] + ',"extra":NaN}', False),
                 ("nan cost", json.dumps({**run, "observed_wall_seconds": float("nan")}), False),
                 ("nan estimate", json.dumps({**run, "resource_estimates": {"cpu_seconds": float("nan")}}), False),
                 ("infinity", row["body"][:-1] + ',"extra":Infinity}', False),
                 ("overflow float", row["body"][:-1] + ',"extra":1e400}', False),
                 ("lone surrogate", row["body"][:-1] + ',"extra":"\\ud800"}', False),
                 ("missing execution", missing("pid"), False),
                 ("bad estimates", body(resource_estimates=[]), False),
                 ("bad protocol", body(protocol=[]), False),
                 ("bad executor", body(executor=""), False),
                 ("bad sha", body(executor_sha256=[]), False),
                 ("bad attempt", body(attempt_id=4), False),
                 ("bad scheduler", body(scheduler="queued"), False),
                 ("negative observed", body(observed_wall_seconds=-1), False),
                 ("bool observed", body(observed_wall_seconds=True), False),
                 ("huge observed", body(observed_wall_seconds=10 ** 400), False),
                 ("string observed", body(observed_wall_seconds="1"), False),
                 ("negative estimate", body(resource_estimates={"cpu_seconds": -1}), False),
                 ("bool estimate", body(resource_estimates={"cpu_seconds": True}), False),
                 ("huge estimate", body(resource_estimates={"cpu_seconds": 10 ** 400}), False),
                 ("bool pid", body(pid=True), False),
                 ("float pid", body(pid=1.0), False),
                 ("zero pid", body(pid=0), False),
                 ("negative worker pid", body(worker_pid=-1), False),
                 ("unknown optional", body(extra={"any": "value"}), True),
                 ("historical aliases", canonical({key: value for key, value in run.items()
                      if key not in {"run_id", "run_status"}}), True)]
        for name, raw, valid in cases:
            with self.subTest(name=name):
                candidate = {**row, "body": raw}
                expected = outcome(old, candidate)
                self.assertEqual(expected[0] == "value", valid)
                self.assertEqual(outcome(new, candidate), expected)
                self.assertEqual(outcome(ProjectStore._run_row, candidate), expected)

    def test_store_consumers_and_committed_run(self):
        store = self.fx.store
        store.register(self.fx.spec())
        store.register(self.fx.spec("r2"))
        old = original_decoder()
        with patch.object(rds_project, "decode_run_row", wraps=decode_run_row) as called:
            with store._db(True) as db:
                unfinished = store._run(db, "r1")
                both = store._runs(db)
            self.assertEqual(called.call_count, 3)
        self.assertEqual(unfinished["status"], "RESERVED")
        self.assertEqual([run["id"] for run in both], ["r1", "r2"])
        with self.assertRaisesRegex(ValueError, "Run ID already exists"):
            store.register(self.fx.spec())
        committed = store.execute("r1")
        self.assertEqual(committed["run_status"], "SUCCEEDED")
        with store._db(True) as db:
            row = dict(db.execute("SELECT id,status,body FROM runs WHERE id='r1'").fetchone())
            self.assertEqual(outcome(ProjectStore._run_row, row), outcome(old, row))
            self.assertEqual(store._run(db, "r1")["status"], "COMPLETED")
            self.assertEqual([run["status"] for run in store._runs(db)], ["COMPLETED", "RESERVED"])
        self.assertEqual(store.recover("r1"), committed)

    def test_clean_import_rejects_direct_or_deferred_upper_imports(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        code = r'''
import builtins, hashlib, json, math, sys
sys.path.insert(0, sys.argv[1])
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith("rds_") and name != "rds_run_records":
        raise AssertionError("upper import: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
from rds_run_records import decode_run_row
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
manifest = {"id": "r1"}
run = {"id": "r1", "status": "RESERVED", "manifest": manifest,
       "manifest_sha256": digest(manifest), "resource_estimates": {}, "protocol": {},
       "executor": "worker", "executor_sha256": "", "attempt_id": None,
       "worker_pid": None, "pid": None, "started_at": None, "finished_at": None,
       "observed_wall_seconds": 0, "scheduler": None}
assert decode_run_row({"id":"r1", "status":"RESERVED", "body":canonical(run)},
                      ValueError, canonical, digest, {"COMPLETED", "FAILED", "INTERRUPTED"}) == run
assert all(name == "rds_run_records" for name in sys.modules if name.startswith("rds_"))
print("IMPORT_GUARD_PASS")
'''
        result = subprocess.run([sys.executable, "-B", "-c", code, str(scripts)],
                                capture_output=True, text=True, timeout=15,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORT_GUARD_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
