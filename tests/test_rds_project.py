import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rds_project import ProjectStore, canonical, digest, file_sha


SCRIPT = '''import json, pathlib, sys, time
mode, output = sys.argv[1:]
print("real project stdout", flush=True)
print("real project stderr", file=sys.stderr, flush=True)
if mode == "timeout": time.sleep(3)
if mode == "nonzero": sys.exit(7)
if mode.startswith("mutate-"):
    pathlib.Path(mode[7:]+".json").write_text('{"changed":true}')
if mode == "directory":
    pathlib.Path(output).mkdir()
elif mode != "missing":
    rows = json.loads(pathlib.Path("data.json").read_text())
    pathlib.Path(output).write_text(json.dumps({"mean":sum(rows)/len(rows),"n":len(rows)}))
'''


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rds project ")
        self.root = Path(self.tmp.name)
        files = {"code": ("code.py", SCRIPT), "config": ("config.json", "{}"),
                 "data": ("data.json", "[1,2,3]"), "evaluator": ("evaluator.json", "{}")}
        for name, content in files.values():
            (self.root / name).write_text(content, encoding="utf-8")
        protocol = {"code_sha256": file_sha(self.root / "code.py"), "config_sha256": file_sha(self.root / "config.json"),
                    "data_sha256": file_sha(self.root / "data.json"), "data_split": "development-only",
                    "init": "none", "seed": 0, "checkpoint": "none", "schedule": "one calculation",
                    "sample_work": {"rows": 3}, "numeric_protocol": "Python float"}
        (self.root / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        files["protocol"] = ("protocol.json", "")
        self.contract = {"schema": 1, "bindings": [{"role": role, "path": name, "sha256": file_sha(self.root / name)}
                                                  for role, (name, _) in files.items()],
                         "allowed_commands": [[sys.executable, "-B", "code.py", mode, f"outputs/{rid}.json"]
                                              for rid in ("r1", "r2", "r3")
                                              for mode in ("ok", "missing", "directory", "nonzero", "timeout", "mutate-evaluator", "mutate-protocol")],
                         "output_roots": ["outputs"], "budget": {"wall_seconds": 20, "cpu_seconds": 10, "gpu_seconds": 0}}
        self.store = ProjectStore(self.root)
        self.store.initialize(self.contract)

    def tearDown(self):
        self.tmp.cleanup()

    def spec(self, rid="r1", mode="ok", timeout=2):
        return {"schema": 1, "id": rid, "arm": "control", "control_id": None,
                "protocol": {"path": "protocol.json", "sha256": file_sha(self.root / "protocol.json")},
                "argv": [sys.executable, "-B", "code.py", mode, f"outputs/{rid}.json"],
                "outpaths": [f"outputs/{rid}.json"], "resource_estimates": {"wall_seconds": timeout + 0.2, "cpu_seconds": 1, "gpu_seconds": 0},
                "timeout_seconds": timeout}

    def run_spec(self, spec):
        self.store.register(spec)
        return self.store.execute(spec["id"])

    def new_store(self, budget=None, commands=(), policy=None):
        root = self.root / "other-project"
        root.mkdir()
        for binding in self.contract["bindings"]:
            shutil.copyfile(self.root / binding["path"], root / binding["path"])
        contract = json.loads(canonical(self.contract))
        if budget is not None:
            contract["budget"] = budget
        contract["allowed_commands"].extend(commands)
        if policy is not None:
            contract['execution_policy'] = policy
        store = ProjectStore(root)
        store.initialize(contract)
        return store

    def test_execution_policy_is_opt_in_and_frozen(self):
        policy = {'schema': 1, 'max_attempts': 1}
        store = self.new_store(policy=policy)
        contract = store.snapshot()['contract']
        for changed in ({**policy, 'max_attempts': 2}, None):
            update = json.loads(canonical(contract))
            if changed is None:
                update.pop('execution_policy')
            else:
                update['execution_policy'] = changed
            with self.assertRaisesRegex(ValueError, 'frozen'):
                store.initialize(update)
        for invalid in ({'schema': 1, 'max_attempts': 0}, {'schema': 1, 'max_attempts': True},
                        {'schema': 1, 'max_attempts': 33}, {'schema': 1, 'max_attempts': 1, 'override': True}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'Execution policy'):
                self.store.initialize({**self.contract, 'execution_policy': invalid})

    def test_policy_success_reuse_checks_receipt_outputs_without_another_reservation(self):
        store = self.new_store(policy={'schema': 1, 'max_attempts': 2})
        store.register(self.spec())
        receipt = store.execute('r1')
        before = store.snapshot()['budget']
        observed = store.execute('r1')
        self.assertEqual(observed['sha256'], receipt['sha256'])
        self.assertFalse(observed['execution_started'])
        self.assertEqual(store.snapshot()['budget'], before)
        with self.assertRaisesRegex(ValueError, 'observe or recover'):
            store.register(self.spec('r2'))
        self.assertEqual(len(store.snapshot()['runs']), 1)
        (store.root / 'outputs/r1.json').unlink()
        with self.assertRaisesRegex(ValueError, 'output unavailable'):
            store.execute('r1')
        self.assertEqual(store.snapshot()['budget'], before)

    def test_policy_failure_attempt_cap_ignores_names_destinations_and_timeout(self):
        store = self.new_store(policy={'schema': 1, 'max_attempts': 2})
        for rid in ('r1', 'r2'):
            store.register(self.spec(rid, 'nonzero'))
            self.assertEqual(store.execute(rid)['run_status'], 'FAILED')
        before = store.snapshot()['budget']
        with self.assertRaisesRegex(ValueError, 'max_attempts'):
            store.register(self.spec('r3', 'nonzero', timeout=0.5))
        self.assertEqual(store.snapshot()['budget'], before)
        self.assertGreater(before['wall_seconds']['spent_measured'], 0)
        self.assertEqual(len(store.snapshot()['receipts']), 2)

    def test_policy_observation_requires_owned_completion_not_a_self_signed_receipt(self):
        store = self.new_store(policy={'schema': 1, 'max_attempts': 1})
        store.register(self.spec())
        original = store.execute('r1')
        before = store.snapshot()['budget']
        forged = {**original, 'assessment': {'task_gain': 'PASS', 'mechanism': 'PASS'}}
        forged['sha256'] = digest({key: value for key, value in forged.items() if key != 'sha256'})
        with store._db() as db:
            # Simulate a damaged/copied local ledger beyond its append-only SQL guards.
            db.execute('DROP TRIGGER receipts_no_update')
            db.execute('UPDATE receipts SET body=?,sha256=? WHERE run_id=?', (canonical(forged), forged['sha256'], 'r1'))
        with self.assertRaisesRegex(ValueError, 'owned completion'):
            store.execute('r1')
        with store._db() as db:
            db.execute('DROP TRIGGER receipts_no_delete')
            db.execute('DELETE FROM receipts WHERE run_id=?', ('r1',))
        with self.assertRaisesRegex(ValueError, 'no owned receipt'):
            store.execute('r1')
        self.assertEqual(store.snapshot()['budget'], before)

    def test_policy_atomic_same_route_registration_and_live_observation(self):
        store = self.new_store(policy={'schema': 1, 'max_attempts': 2})
        def reserve(rid):
            try:
                return store.register(self.spec(rid))['id']
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            admitted = list(pool.map(reserve, ('r1', 'r2')))
        self.assertEqual(len([rid for rid in admitted if rid is not None]), 1)
        state = store.snapshot()
        self.assertEqual(len(state['runs']), 1)
        self.assertEqual(state['budget']['wall_seconds']['reserved'], 2.2)
        rid = next(rid for rid in admitted if rid is not None)
        with store._db() as db:
            run = store._run(db, rid)
            run.update(status='RUNNING', attempt_id='owned-attempt')
            store._save(db, run)
        observed = store.execute(rid)
        self.assertEqual(observed['status'], 'RUNNING')
        self.assertFalse(observed['execution_started'])
        self.assertFalse((store.root / f'outputs/{rid}.json').exists())

    def test_registration_consumes_observed_executor_binding_before_reserving(self):
        executor = self.root / Path(sys.executable).name
        shutil.copy2(sys.executable, executor)
        spec = self.spec()
        spec['argv'][0] = str(executor)
        store = self.new_store(policy={'schema': 1, 'max_attempts': 1}, commands=[spec['argv']])
        expected = file_sha(executor)
        before = store.snapshot()['budget']
        with executor.open('ab') as handle:
            handle.write(b'changed-after-parent-observation')
        with self.assertRaisesRegex(ValueError, 'changed before registration'):
            store.register(spec, executor_sha256=expected)
        self.assertEqual(store.snapshot()['budget'], before)
        self.assertFalse(store.snapshot()['runs'])
        shutil.copyfile(sys.executable, executor)
        registered = store.register(spec, executor_sha256=expected)
        self.assertEqual(registered['executor_sha256'], expected)

    def test_policy_route_normalizes_bound_file_aliases_to_content_and_role(self):
        from rds_project import execution_route
        alias = self.root / 'same-code.py'
        alias.write_bytes((self.root / 'code.py').read_bytes())
        bindings = [{'path': 'code.py', 'role': 'code', 'sha256': file_sha(alias)},
                    {'path': 'same-code.py', 'role': 'code', 'sha256': file_sha(alias)}]
        one = execution_route([sys.executable, '-B', 'code.py', 'outputs/one'], bindings, ['outputs/one'], self.root)
        two = execution_route([sys.executable, '-B', 'same-code.py', 'outputs/two'], bindings, ['outputs/two'], self.root)
        self.assertEqual(one, two)
        if os.name == 'nt':
            self.assertEqual(one, execution_route([sys.executable, '-B', 'CODE.PY', 'OUTPUTS/ONE'],
                                                  bindings, ['outputs/one'], self.root))
        changed = [{**bindings[0], 'sha256': 'a' * 64}]
        self.assertNotEqual(one, execution_route([sys.executable, '-B', 'code.py', 'outputs/one'], changed, ['outputs/one'], self.root))

    def reserve_overrun_pair(self):
        store = self.new_store({"wall_seconds": 0.002, "cpu_seconds": 2, "gpu_seconds": 0})
        for rid in ("r1", "r2"):
            spec = self.spec(rid, "timeout", 0.001)
            spec["resource_estimates"]["wall_seconds"] = 0.001
            store.register(spec)
        return store

    def test_real_success_receipt_and_distinct_costs(self):
        receipt = self.run_spec(self.spec())
        self.assertEqual(receipt["run_status"], "SUCCEEDED")
        self.assertEqual(receipt["process_status"], "COMPLETED")
        self.assertEqual(json.loads((self.root / "outputs/r1.json").read_text()), {"mean": 2.0, "n": 3})
        self.assertEqual(receipt["assessment"], {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN"})
        self.assertGreater(receipt["resources"]["wall_seconds"]["measured"], 0)
        self.assertIsNone(receipt["resources"]["cpu_seconds"]["measured"])
        self.assertEqual(receipt["resources"]["cpu_seconds"]["charged_estimate"], 1)
        self.assertEqual(receipt["sha256"], digest({k: v for k, v in receipt.items() if k != "sha256"}))
        for artifact in receipt["artifacts"]:
            self.assertEqual(file_sha(self.root / artifact["path"]), artifact["sha256"])
        snap = self.store.snapshot()
        self.assertEqual(snap["runs"][0]["status"], "COMPLETED")
        self.assertEqual(snap["runs"][0]["run_status"], "SUCCEEDED")
        self.assertEqual(snap["budget"]["cpu_seconds"]["charged_estimate"], 1)
        self.assertEqual(snap["budget"]["wall_seconds"]["reserved"], 0)
        self.assertEqual(len(snap["exposures"]), 1)

    def test_exit_zero_missing_artifact_is_failure(self):
        receipt = self.run_spec(self.spec(mode="missing"))
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assertTrue(any("Missing output" in err for err in receipt["errors"]))

    def test_exit_zero_directory_artifact_is_a_file_type_failure(self):
        receipt = self.run_spec(self.spec(mode="directory"))
        self.assertTrue((self.root / "outputs/r1.json").is_dir())
        self.assertEqual((receipt["run_status"], receipt["exit_code"]), ("FAILED", 0))
        self.assertEqual(receipt["assessment"], {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN"})
        self.assertFalse(any(artifact["kind"] == "project_output" for artifact in receipt["artifacts"]))
        error = "Output is a directory; expected a file: outputs/r1.json"
        self.assertEqual(receipt["errors"], [error])
        from rds_quick import brief
        summary = brief(self.root, self.store.snapshot(), "test")
        self.assertEqual(summary["latest_receipt"]["errors"], [error])
        self.assertEqual(summary["latest_receipt"]["error_count"], 1)

    def test_protocol_and_evaluator_drift_are_failures(self):
        for mode in ("mutate-evaluator", "mutate-protocol"):
            with self.subTest(mode=mode):
                saved = (self.root / (mode[7:] + ".json")).read_bytes()
                rid = "r1" if mode == "mutate-evaluator" else "r2"
                receipt = self.run_spec(self.spec(rid, mode))
                self.assertEqual(receipt["exit_code"], 0)
                self.assertEqual(receipt["run_status"], "FAILED")
                self.assertNotEqual(receipt["bindings_before"], receipt["bindings_after"])
                (self.root / (mode[7:] + ".json")).write_bytes(saved)

    def test_binding_drift_before_start_does_not_launch(self):
        self.store.register(self.spec())
        (self.root / "config.json").write_text('{"changed":true}')
        receipt = self.store.execute("r1")
        self.assertFalse(receipt["process_started"])
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assertFalse((self.root / "outputs/r1.json").exists())
        self.assertEqual(self.store.snapshot()["budget"]["cpu_seconds"]["charged_estimate"], 1)
        self.assertIsNone(receipt["resources"]["cpu_seconds"]["measured"])

    def test_nonzero_and_timeout_keep_costs(self):
        failed = self.run_spec(self.spec("r1", "nonzero"))
        timed = self.run_spec(self.spec("r2", "timeout", 0.15))
        self.assertEqual(failed["exit_code"], 7)
        self.assertEqual(timed["run_status"], "FAILED")
        self.assertTrue(timed["timeout"])
        self.assertGreater(failed["resources"]["wall_seconds"]["measured"], 0)
        self.assertGreater(timed["resources"]["wall_seconds"]["measured"], 0)
        self.assertEqual(self.store.snapshot()["budget"]["cpu_seconds"]["charged_estimate"], 2)

    def test_append_only_receipts_and_exposures(self):
        self.run_spec(self.spec())
        with self.store._db() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE receipts SET body='{}'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM exposures")

    def test_duplicate_run_pending_and_terminal_never_restart(self):
        self.store.register(self.spec())
        with self.assertRaises(ValueError):
            self.store.register(self.spec())
        pending = self.store.recover("r1")
        self.assertEqual(pending["status"], "RESERVED")
        original = self.store.execute("r1")
        with self.assertRaises(ValueError):
            self.store.execute("r1")
        self.assertEqual(self.store.recover("r1")["sha256"], original["sha256"])
        with self.assertRaises(ValueError):
            self.store.recover("missing")

    def test_interrupted_recovery_preserves_unknown_cost_and_no_rerun(self):
        self.store.register(self.spec())
        with self.store._db() as db:
            run = self.store._run(db, "r1")
            run.update(status="RUNNING", attempt_id="lost-attempt", worker_pid=None, pid=None,
                       started_at=time.time() - 2, observed_wall_seconds=0.3)
            self.store._save(db, run)
        receipt = self.store.recover("r1")
        self.assertEqual(receipt["run_status"], "INTERRUPTED")
        self.assertIsNone(receipt["resources"]["wall_seconds"]["measured"])
        self.assertEqual(receipt["resources"]["wall_seconds"]["charged_estimate"], 2.2)
        self.assertEqual(receipt["resources"]["wall_seconds"]["observed_lower_bound"], 0.3)
        with self.assertRaises(ValueError):
            self.store.execute("r1")
        self.assertFalse((self.root / "outputs/r1.json").exists())

    def test_recover_does_not_stop_live_or_foreign_process(self):
        self.store.register(self.spec())
        with self.store._db() as db:
            run = self.store._run(db, "r1")
            run.update(status="RUNNING", attempt_id="live", worker_pid=os.getpid())
            self.store._save(db, run)
        self.assertEqual(self.store.recover("r1")["status"], "RUNNING")
        self.assertEqual(self.store.snapshot()["budget"]["cpu_seconds"]["reserved"], 1)

    def test_recover_during_foreground_startup_preserves_reservation(self):
        from startup_recovery_fixture import run_fixture
        result = run_fixture(self.root, self.spec())
        self.assertFalse(result["watchdog_expired"], result)
        self.assertEqual(result["returncode"], 0, result)

    def test_startup_recovery_waits_for_real_sqlite_settlement(self):
        from startup_recovery_fixture import run_fixture
        result = run_fixture(self.root, self.spec(), scenario="sqlite_wait")
        self.assertFalse(result["watchdog_expired"], result)
        self.assertEqual(result["returncode"], 0, result)
        self.assertGreaterEqual(result["report"]["writer_lock_seconds"], 5.2)
        self.assertEqual(result["report"]["snapshot"]["receipts"][0]["run_status"], "SUCCEEDED")

    def test_startup_fixture_does_not_hide_nonzero_child_failure(self):
        from startup_recovery_fixture import run_fixture
        result = run_fixture(self.root, self.spec(mode="nonzero"))
        self.assertFalse(result["watchdog_expired"], result)
        self.assertEqual(result["returncode"], 1, result)
        self.assertEqual(result["report"]["exception"]["type"], "AssertionError")
        receipt = result["report"]["snapshot"]["receipts"][0]
        self.assertEqual((receipt["run_status"], receipt["exit_code"]), ("FAILED", 7))
        self._assert_single_failed_startup(receipt)

    def test_startup_fixture_preserves_actual_runtime_timeout_failure(self):
        from startup_recovery_fixture import run_fixture
        result = run_fixture(self.root, self.spec(mode="timeout", timeout=0.15))
        self.assertFalse(result["watchdog_expired"], result)
        self.assertEqual(result["returncode"], 1, result)
        receipt = result["report"]["snapshot"]["receipts"][0]
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assertTrue(receipt["timeout"])
        self._assert_single_failed_startup(receipt)

    def _assert_single_failed_startup(self, receipt):
        snapshot = self.store.snapshot()
        self.assertEqual((len(snapshot["runs"]), len(snapshot["receipts"]), len(snapshot["exposures"])), (1, 1, 1))
        self.assertEqual(snapshot["runs"][0]["attempt_id"], receipt["attempt_id"])
        self.assertEqual(snapshot["receipts"][0]["sha256"], receipt["sha256"])
        self.assertEqual(snapshot["budget"]["cpu_seconds"]["charged_estimate"], 1)
        self.assertEqual(snapshot["budget"]["cpu_seconds"]["reserved"], 0)
        self.assertTrue(receipt["process_started"])
        self.assertEqual(self.store.recover("r1")["sha256"], receipt["sha256"])
        with self.assertRaises(ValueError):
            self.store.execute("r1")
        self.assertEqual(self.store.snapshot(), snapshot)

    def test_startup_fixture_watchdog_fails_and_stops_its_controller(self):
        from startup_recovery_fixture import run_fixture
        result = run_fixture(self.root, self.spec(), scenario="stalled_claim", watchdog=0.2)
        self.assertTrue(result["watchdog_expired"], result)
        self.assertNotEqual(result["returncode"], 0)
        self.assertEqual(result["report"]["phase"], "stalled_claim")
        self.assertIn("Thread", result["stderr"])
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["runs"][0]["status"], "RESERVED")
        self.assertIsNone(snapshot["runs"][0]["pid"])
        self.assertFalse(snapshot["receipts"])
        self.assertEqual(snapshot["budget"]["cpu_seconds"]["reserved"], 1)
        self.assertEqual(snapshot["budget"]["cpu_seconds"]["charged_estimate"], 0)
        from rds_project import _alive
        self.assertEqual(snapshot["runs"][0]["worker_pid"], result["report"]["controller_pid"])
        self.assertFalse(_alive(snapshot["runs"][0]["worker_pid"]))
        recovered = self.store.recover("r1")
        self.assertEqual(recovered["run_status"], "INTERRUPTED")
        self.assertEqual(recovered["attempt_id"], snapshot["runs"][0]["attempt_id"])
        self.assertIsNone(recovered["exit_code"])
        self.assertIsNone(recovered["resources"]["cpu_seconds"]["measured"])
        self.assertEqual(recovered["assessment"], {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN"})
        after = self.store.snapshot()
        self.assertEqual((len(after["runs"]), len(after["receipts"])), (1, 1))
        self.assertEqual(after["budget"]["cpu_seconds"]["reserved"], 0)
        self.assertEqual(after["budget"]["cpu_seconds"]["charged_estimate"], 1)
        self.assertEqual(self.store.recover("r1")["sha256"], recovered["sha256"])
        with self.assertRaises(ValueError):
            self.store.execute("r1")
        self.assertEqual(self.store.snapshot(), after)

    def _assert_foreground_startup_recovery(self, spec, record):
        self.store.register(spec)
        claimed, resume = threading.Event(), threading.Event()
        execute_claim = self.store._execute_claim

        def pause_claim(run_id, attempt_id):
            claimed.set()
            # The isolated controller's parent watchdog bounds this rendezvous.
            resume.wait()
            return execute_claim(run_id, attempt_id)

        with patch.object(self.store, "_execute_claim", side_effect=pause_claim), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            execution = pool.submit(self.store.execute, "r1")
            # A pre-claim worker exception must wake the gate and retain its
            # original traceback instead of waiting for the parent watchdog.
            execution.add_done_callback(lambda future: claimed.set())
            try:
                record("waiting_for_claim")
                claimed.wait()
                if execution.done():
                    execution.result()
                record("claimed")
                recovered = self.store.recover("r1")
                self.assertEqual(recovered.get("status"), "RESERVED")
                self.assertEqual(recovered["worker_pid"], os.getpid())
                attempt_id = recovered["attempt_id"]
                snapshot = self.store.snapshot()
                self.assertFalse(snapshot["receipts"])
                self.assertEqual(snapshot["budget"]["cpu_seconds"]["reserved"], 1)
                self.assertEqual(snapshot["budget"]["cpu_seconds"]["charged_estimate"], 0)
                with self.assertRaises(ValueError):
                    self.store.execute("r1")
                record("recovered_without_duplicate")
            finally:
                try:
                    record("waiting_for_receipt")
                finally:
                    resume.set()
            # Five seconds was a fixture limit, shorter than the existing ten-
            # second SQLite wait, not a bound on the frozen child runtime.
            receipt = execution.result()
        record("settled")
        self.assertEqual(receipt["run_status"], "SUCCEEDED")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["attempt_id"], attempt_id)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["budget"]["cpu_seconds"]["charged_estimate"], 1)
        self.assertEqual(snapshot["budget"]["cpu_seconds"]["reserved"], 0)
        self.assertEqual(len(snapshot["runs"]), 1)
        self.assertEqual(len(snapshot["receipts"]), 1)
        self.assertEqual(len(snapshot["exposures"]), 1)
        self.assertEqual(snapshot["receipts"][0]["sha256"], receipt["sha256"])
        self.assertTrue(receipt["process_started"])
        self.assertIsNotNone(receipt["pid"])
        output = next(a for a in receipt["artifacts"] if a["kind"] == "project_output")
        self.assertEqual(file_sha(self.root / output["path"]), output["sha256"])
        self.assertEqual(self.store.recover("r1")["sha256"], receipt["sha256"])
        with self.assertRaises(ValueError):
            self.store.execute("r1")
        self.assertEqual(self.store.snapshot(), snapshot)

    def test_stale_recovery_cannot_settle_a_newly_claimed_worker(self):
        # An existing reservation may predate foreground controller identity.
        self.store.register(self.spec())
        with self.store._db() as db:
            run = self.store._run(db, "r1")
            run["attempt_id"] = "pending-attempt"
            self.store._save(db, run)
        recovering, claimed = threading.Event(), threading.Event()
        resume_recovery, resume_execution = threading.Event(), threading.Event()
        bindings = self.store._bindings

        def pause_bindings(contract):
            if threading.current_thread().name.startswith("recover"):
                recovering.set()
                self.assertTrue(resume_recovery.wait(5))
            else:
                claimed.set()
                self.assertTrue(resume_execution.wait(5))
            return bindings(contract)

        with patch.object(self.store, "_bindings", side_effect=pause_bindings), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="recover") as recovery_pool, \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as execution_pool:
            recovery = recovery_pool.submit(self.store.recover, "r1")
            try:
                self.assertTrue(recovering.wait(5))
                execution = execution_pool.submit(self.store._execute_claim, "r1", "pending-attempt")
                self.assertTrue(claimed.wait(5))
                resume_recovery.set()
                state = recovery.result(timeout=5)
                self.assertEqual(state.get("status"), "RUNNING")
                self.assertEqual(state["worker_pid"], os.getpid())
                self.assertFalse(self.store.snapshot()["receipts"])
                self.assertEqual(self.store.snapshot()["budget"]["cpu_seconds"]["reserved"], 1)
            finally:
                resume_recovery.set()
                resume_execution.set()
            receipt = execution.result(timeout=5)
        self.assertEqual(receipt["run_status"], "SUCCEEDED")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(len(self.store.snapshot()["exposures"]), 1)
        self.assertEqual(self.store.snapshot()["budget"]["cpu_seconds"]["charged_estimate"], 1)

    def test_stale_recovery_returns_normal_receipt_without_double_settlement(self):
        self.store.register(self.spec())
        with self.store._db() as db:
            run = self.store._run(db, "r1")
            run["attempt_id"] = "pending-attempt"
            self.store._save(db, run)
        recovering, resume = threading.Event(), threading.Event()
        bindings = self.store._bindings

        def pause_bindings(contract):
            if threading.current_thread().name.startswith("recover"):
                recovering.set()
                self.assertTrue(resume.wait(5))
            return bindings(contract)

        with patch.object(self.store, "_bindings", side_effect=pause_bindings), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="recover") as pool:
            recovery = pool.submit(self.store.recover, "r1")
            try:
                self.assertTrue(recovering.wait(5))
                receipt = self.store._execute_claim("r1", "pending-attempt")
                budget = self.store.snapshot()["budget"]
            finally:
                resume.set()
            self.assertEqual(recovery.result(timeout=5), receipt)
        snapshot = self.store.snapshot()
        self.assertEqual(receipt["run_status"], "SUCCEEDED")
        self.assertEqual(snapshot["budget"], budget)
        self.assertEqual(len(snapshot["receipts"]), 1)
        self.assertEqual(len(snapshot["exposures"]), 1)
        with self.assertRaises(ValueError):
            self.store.execute("r1")

    def test_atomic_budget_vector_and_dimension_limits(self):
        self.contract["budget"]["wall_seconds"] = 3
        # A frozen contract cannot be reset to a larger or smaller budget.
        with self.assertRaises(ValueError):
            self.store.initialize(self.contract)
        with self.store._db() as db:
            db.execute("UPDATE budget SET cap=3 WHERE resource='wall_seconds'")
        def reserve(rid):
            try:
                self.store.register(self.spec(rid))
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ["r1", "r2"]))
        self.assertEqual(sum(results), 1)
        self.assertAlmostEqual(self.store.snapshot()["budget"]["wall_seconds"]["reserved"], 2.2)
        other = self.spec("r3")
        other["resource_estimates"]["gpu_seconds"] = 0.01
        with self.assertRaises(ValueError):
            self.store.register(other)

    def test_start_uses_existing_reservation_without_double_counting(self):
        store = self.new_store({"wall_seconds": 4.4, "cpu_seconds": 2, "gpu_seconds": 0})
        store.register(self.spec("r1"))
        store.register(self.spec("r2"))
        self.assertAlmostEqual(store.snapshot()["budget"]["wall_seconds"]["remaining"], 0)

        def settle_synthetic_accounting(run_id, attempt_id):
            # This tests reservation arithmetic, not subprocess performance.
            # Successful real execution can settle above its estimate after
            # preflight/launch/cleanup, which must block the second reservation.
            # Keep admission and settlement real, but control this worker's
            # finite cost; no subprocess is launched or claimed by this fixture.
            with store._db(True) as db:
                run = store._run(db, run_id)
                self.assertEqual(run["attempt_id"], attempt_id)
                self.assertEqual(run["status"], "RESERVED")
                contract = store._contract(db)
            before, errors = store._bindings(contract)
            self.assertEqual(errors, [])
            return store._finish(run_id, attempt_id, "COMPLETED", 0, 2.2, False, [], before)

        with patch.object(store, "_execute_claim", side_effect=settle_synthetic_accounting) as worker:
            first = store.execute("r1")
            self.assertEqual(first["run_status"], "SUCCEEDED")
            self.assertFalse(first["process_started"])
            midway = store.snapshot()["budget"]["wall_seconds"]
            self.assertAlmostEqual(midway["spent_measured"], 2.2)
            self.assertAlmostEqual(midway["reserved"], 2.2)
            self.assertAlmostEqual(midway["charged_estimate"], 0)
            self.assertAlmostEqual(midway["remaining"], 0)
            second = store.execute("r2")
            self.assertEqual(second["run_status"], "SUCCEEDED")
            self.assertFalse(second["process_started"])
            self.assertNotEqual(first["attempt_id"], second["attempt_id"])
            self.assertEqual(worker.call_count, 2)
        final = store.snapshot()["budget"]["wall_seconds"]
        self.assertAlmostEqual(final["spent_measured"], 4.4)
        self.assertAlmostEqual(final["reserved"], 0)
        self.assertAlmostEqual(final["charged_estimate"], 0)
        self.assertAlmostEqual(final["remaining"], 0)

    def test_real_overrun_blocks_reserved_dispatch_and_preserves_charges(self):
        store = self.reserve_overrun_pair()
        receipt = store.execute("r1")
        self.assertTrue(receipt["process_started"])
        self.assertTrue(receipt["timeout"])
        self.assertGreater(receipt["resources"]["wall_seconds"]["measured"], 0.002)
        before = store.snapshot()
        self.assertLess(before["budget"]["wall_seconds"]["remaining"], 0)
        for background in ([False, True] if os.name == "nt" else [False]):
            with self.subTest(background=background), patch.object(store, "_schedule") as schedule:
                with self.assertRaisesRegex(ValueError, "Insufficient wall_seconds budget before start"):
                    store.execute("r2", background=background)
                schedule.assert_not_called()
        after = store.snapshot()
        self.assertEqual(before["budget"], after["budget"])
        self.assertEqual(before["receipts"], after["receipts"])
        pending = next(run for run in after["runs"] if run["id"] == "r2")
        self.assertEqual(pending["status"], "RESERVED")
        self.assertIsNone(pending["attempt_id"])
        self.assertFalse((store.root / "outputs/r2.json").exists())

    def test_queued_worker_rechecks_budget_after_another_run_overruns(self):
        store = self.reserve_overrun_pair()
        with store._db() as db:
            run = store._run(db, "r2")
            run["attempt_id"] = "queued-attempt"
            run["scheduler"] = {"task_id": "RDS-Project-queued", "status": "REGISTERED"}
            store._save(db, run)
        store.execute("r1")
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, "Insufficient wall_seconds budget before start"):
            store._execute_claim("r2", "queued-attempt")
        self.assertEqual(before, store.snapshot())
        self.assertEqual(store.recover("r2")["status"], "RESERVED")
        with self.assertRaises(ValueError):
            store.execute("r2")

    def test_overrun_during_preflight_blocks_popen_and_retains_failure_cost(self):
        store = self.reserve_overrun_pair()
        preflight, resume = threading.Event(), threading.Event()
        bindings = store._bindings

        def pause_bindings(contract):
            if threading.current_thread().name.startswith("pending") and not preflight.is_set():
                preflight.set()
                self.assertTrue(resume.wait(5))
            return bindings(contract)

        with patch.object(store, "_bindings", side_effect=pause_bindings), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pending") as pool:
            execution = pool.submit(store.execute, "r2")
            try:
                self.assertTrue(preflight.wait(5))
                first = store.execute("r1")
                self.assertTrue(first["process_started"])
                self.assertLess(store.snapshot()["budget"]["wall_seconds"]["remaining"], 0)
            finally:
                resume.set()
            receipt = execution.result(timeout=5)
        self.assertFalse(receipt["process_started"])
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assertTrue(any("Insufficient wall_seconds budget before start" in error for error in receipt["errors"]))
        self.assertFalse((store.root / "outputs/r2.json").exists())
        budget = store.snapshot()["budget"]
        self.assertEqual(budget["cpu_seconds"]["charged_estimate"], 2)
        self.assertEqual(budget["cpu_seconds"]["reserved"], 0)
        with self.assertRaises(ValueError):
            store.execute("r2")

    def test_legacy_relative_output_claim_is_same_as_absolute_claim(self):
        self.store.register(self.spec())
        with self.store._db() as db:
            db.execute("UPDATE output_claims SET path='outputs/./r1.json' WHERE run_id='r1'")
        self.assertEqual(self.store._output_key("outputs/./r1.json"),
                         self.store._output_key(self.root / "outputs/r1.json"))
        self.assertEqual(self.store.execute("r1")["run_status"], "SUCCEEDED")

    @unittest.skipUnless(os.name == "nt", "Windows physical output aliases")
    def test_windows_output_alias_cannot_claim_legacy_absolute_path(self):
        argv = [sys.executable, "-B", "code.py", "ok", "outputs/R1.json"]
        store = self.new_store(commands=[argv])
        store.register(self.spec("r1"))
        with store._db() as db:
            db.execute("UPDATE output_claims SET path=? WHERE run_id='r1'",
                       (str(store.root / "outputs/r1.json"),))
        spec = self.spec("r2")
        spec.update(argv=argv, outpaths=["outputs/./R1.json"])
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, "Output is already claimed"):
            store.register(spec)
        self.assertEqual(before, store.snapshot())
        self.assertEqual(store.execute("r1")["run_status"], "SUCCEEDED")
        self.assertTrue((store.root / "outputs/R1.json").samefile(store.root / "outputs/r1.json"))

    @unittest.skipUnless(os.name == "nt", "Existing Windows ledger aliases")
    def test_legacy_conflicting_windows_claims_block_controller_and_worker(self):
        argv = [sys.executable, "-B", "code.py", "ok", "outputs/R1.json"]
        store = self.new_store(commands=[argv])
        store.register(self.spec("r1"))
        store.register(self.spec("r2"))
        # Reproduce the two raw, case-distinct keys admitted by the old runner.
        with store._db() as db:
            run = store._run(db, "r2")
            run["manifest"].update(argv=argv, outpaths=["outputs/R1.json"])
            run["manifest_sha256"] = digest(run["manifest"])
            store._save(db, run)
            db.execute("UPDATE output_claims SET path=? WHERE run_id='r1'",
                       (str(store.root / "outputs/r1.json"),))
            db.execute("UPDATE output_claims SET path=? WHERE run_id='r2'",
                       (str(store.root / "outputs/R1.json"),))
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, "not exclusively claimed"):
            store.execute("r1")
        with patch.object(store, "_schedule") as schedule:
            with self.assertRaisesRegex(ValueError, "not exclusively claimed"):
                store.execute("r2", background=True)
            schedule.assert_not_called()
        self.assertEqual(before, store.snapshot())
        with store._db() as db:
            run = store._run(db, "r2")
            run["attempt_id"] = "legacy-queued"
            run["scheduler"] = {"task_id": "RDS-Project-legacy", "status": "REGISTERED"}
            store._save(db, run)
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, "not exclusively claimed"):
            store._execute_claim("r2", "legacy-queued")
        self.assertEqual(before, store.snapshot())

    @unittest.skipUnless(os.name == "nt", "Windows strips output suffix dots/spaces")
    def test_windows_suffix_aliases_cannot_get_two_success_receipts(self):
        aliases = ["outputs/r1.json.", "outputs/r1.json ", "outputs/r1.json. ",
                   "outputs./r1.json", "outputs /r1.json", "outputs. /r1.json"]
        commands = [[sys.executable, "-B", "code.py", "ok", path] for path in aliases]
        store = self.new_store(commands=commands)
        store.register(self.spec("r1"))
        before = store.snapshot()
        for path, argv in zip(aliases, commands):
            with self.subTest(path=path):
                spec = self.spec("r2")
                spec.update(argv=argv, outpaths=[path])
                with self.assertRaisesRegex(ValueError, "Windows output path components"):
                    store.register(spec)
                self.assertEqual(before, store.snapshot())
        receipt = store.execute("r1")
        self.assertEqual(receipt["run_status"], "SUCCEEDED")
        for path in aliases[:4]:
            self.assertTrue((store.root / path).samefile(store.root / "outputs/r1.json"))
        self.assertEqual(len(store.snapshot()["runs"]), 1)
        self.assertEqual(len(store.snapshot()["exposures"]), 1)

    @unittest.skipUnless(os.name == "nt", "Legacy Win32 output suffix aliases")
    def test_windows_legacy_suffix_key_normalizes_before_file_creation(self):
        self.store.register(self.spec("r1"))
        legacy = "outputs././r1.json. "
        self.assertFalse((self.root / "outputs/r1.json").exists())
        self.assertEqual(self.store._output_key(legacy), self.store._output_key("outputs/r1.json"))
        self.assertEqual(self.store._output_key(self.root / legacy), self.store._output_key("outputs/r1.json"))
        with self.store._db() as db:
            db.execute("UPDATE output_claims SET path=? WHERE run_id='r1'", (legacy,))
        self.assertEqual(self.store.execute("r1")["run_status"], "SUCCEEDED")
        self.assertTrue((self.root / legacy).samefile(self.root / "outputs/r1.json"))

    @unittest.skipUnless(os.name == "nt", "Legacy Win32 suffix ownership conflict")
    def test_windows_legacy_suffix_owners_block_controller_scheduler_and_worker(self):
        self.store.register(self.spec("r1"))
        self.store.register(self.spec("r2"))
        with self.store._db() as db:
            db.execute("UPDATE output_claims SET path=? WHERE run_id='r2'", (str(self.root / "outputs. /r1.json. "),))
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "not exclusively claimed"):
            self.store.execute("r1")
        with patch.object(self.store, "_schedule") as schedule:
            with self.assertRaisesRegex(ValueError, "not exclusively claimed"):
                self.store.execute("r1", background=True)
            schedule.assert_not_called()
        self.assertEqual(before, self.store.snapshot())
        with self.store._db() as db:
            run = self.store._run(db, "r1")
            run.update(attempt_id="legacy-queued", scheduler={"task_id": "RDS-Project-legacy", "status": "REGISTERED"})
            self.store._save(db, run)
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "not exclusively claimed"):
            self.store._execute_claim("r1", "legacy-queued")
        self.assertEqual(before, self.store.snapshot())
        self.assertFalse((self.root / "outputs/r1.json").exists())

    @unittest.skipUnless(os.name == "nt", "Old Windows suffix manifests")
    def test_windows_legacy_suffix_manifest_cannot_dispatch(self):
        self.store.register(self.spec("r1"))
        with self.store._db() as db:
            run = self.store._run(db, "r1")
            run["manifest"]["outpaths"] = ["outputs/r1.json. "]
            run["manifest_sha256"] = digest(run["manifest"])
            self.store._save(db, run)
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "Windows output path components"):
            self.store.execute("r1")
        self.assertEqual(before, self.store.snapshot())

    @unittest.skipUnless(os.name != "nt", "POSIX suffix names are distinct files")
    def test_posix_suffix_outputs_remain_independent(self):
        aliases = ["outputs/r1.json.", "outputs/r1.json "]
        commands = [[sys.executable, "-B", "code.py", "ok", path] for path in aliases]
        store = self.new_store(commands=commands)
        store.register(self.spec("r1"))
        for rid, path, argv in zip(("r2", "r3"), aliases, commands):
            spec = self.spec(rid)
            spec.update(argv=argv, outpaths=[path])
            store.register(spec)
        for rid in ("r1", "r2", "r3"):
            self.assertEqual(store.execute(rid)["run_status"], "SUCCEEDED")
        for path in aliases:
            self.assertFalse((store.root / path).samefile(store.root / "outputs/r1.json"))

    @unittest.skipUnless(os.name != "nt", "Native case-distinct output paths")
    def test_case_distinct_outputs_remain_independent_on_posix(self):
        argv = [sys.executable, "-B", "code.py", "ok", "outputs/R1.json"]
        store = self.new_store(commands=[argv])
        store.register(self.spec("r1"))
        spec = self.spec("r2")
        spec.update(argv=argv, outpaths=["outputs/R1.json"])
        store.register(spec)
        self.assertEqual(store.execute("r1")["run_status"], "SUCCEEDED")
        self.assertEqual(store.execute("r2")["run_status"], "SUCCEEDED")
        self.assertFalse((store.root / "outputs/R1.json").samefile(store.root / "outputs/r1.json"))

    def test_paths_commands_and_handwritten_verification_rejected(self):
        for edit in ({"outpaths": ["../escape.json"]}, {"outpaths": [str(self.root.parent / "escape.json")]},
                     {"argv": [sys.executable, "-c", "print('unauthorized')"]}, {"verified": True},
                     {"timeout_seconds": float("nan")}, {"timeout_seconds": True}, {"schema": True}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.store.register({**self.spec(), **edit})
        with self.assertRaises(ValueError):
            ProjectStore._command(["cmd.exe" if os.name == "nt" else "/bin/sh", "-c", "echo test"])

    def test_protocol_identity_must_match_real_bindings(self):
        protocol = json.loads((self.root / "protocol.json").read_text())
        protocol["data_sha256"] = "0" * 64
        (self.root / "protocol.json").write_text(json.dumps(protocol))
        # Even updating its declared hash cannot make conflicting data identities valid.
        for b in self.contract["bindings"]:
            if b["role"] == "protocol":
                b["sha256"] = file_sha(self.root / "protocol.json")
        with self.store._db() as db:
            # New test store so no frozen contract is modified.
            pass
        with tempfile.TemporaryDirectory(prefix="rds identity ") as other:
            import shutil
            for path in self.root.iterdir():
                if path.is_file():
                    shutil.copyfile(path, Path(other) / path.name)
            other_store = ProjectStore(other)
            # The protocol freezes with the contract, so the conflict is refused before anything is frozen.
            with self.assertRaisesRegex(ValueError, "data_sha256 must be"):
                other_store.initialize(self.contract)
            self.assertFalse((Path(other) / ".rds" / "project.sqlite3").exists())

    def test_existing_output_cannot_supply_stale_success(self):
        (self.root / "outputs").mkdir()
        (self.root / "outputs/r1.json").write_text('{}')
        with self.assertRaises(ValueError):
            self.store.register(self.spec())

    def test_readonly_snapshot_preserves_budget_and_receipts(self):
        self.store.register(self.spec())
        first = self.store.snapshot()
        self.assertEqual(first, self.store.snapshot())
        with self.store._db(True) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("UPDATE budget SET cap=1000")

    def test_snapshot_hashes_only_on_explicit_binding_check(self):
        with patch.object(self.store, "_bindings", wraps=self.store._bindings) as check:
            self.store.snapshot()
            check.assert_not_called()
            (self.root / "config.json").write_text('{"changed":true}')
            checked = self.store.snapshot(check_bindings=True)
            self.assertTrue(checked["binding_check"]["errors"])
            self.assertEqual(check.call_count, 1)

    def test_duplicate_file_roles_hash_once_per_boundary(self):
        contract = {**self.contract, "bindings": [*self.contract["bindings"],
                    {"path": "code.py", "role": "evaluator", "sha256": file_sha(self.root / "code.py")} ]}
        with patch("rds_project.file_sha", wraps=file_sha) as hashed:
            files, errors = self.store._bindings(contract)
        self.assertFalse(errors)
        self.assertEqual(hashed.call_count, 5)
        self.assertEqual(len(files), 6)

    @unittest.skipUnless(os.name == "nt", "Windows scheduler dispatch guard")
    def test_scheduler_failure_is_recorded_and_never_restarted(self):
        self.store.register(self.spec())
        with patch.object(self.store, "_schedule", side_effect=OSError("recorded denial")):
            receipt = self.store.execute("r1", background=True)
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assertEqual(receipt["scheduler"]["task_id"].split("-")[:2], ["RDS", "Project"])
        self.assertEqual(receipt["scheduler"]["status"], "FAILED")
        self.assertEqual(self.store.snapshot()["runs"][0]["scheduler"], receipt["scheduler"])
        with self.assertRaises(ValueError):
            self.store.execute("r1", background=True)

    @unittest.skipUnless(os.name == "nt", "Windows scheduler dispatch race")
    def test_dispatch_error_cannot_close_an_already_claimed_worker(self):
        self.store.register(self.spec())
        def partial_dispatch(run):
            with self.store._db() as db:
                current = self.store._run(db, run["id"])
                current.update(status="RUNNING", worker_pid=os.getpid())
                current["scheduler"]["status"] = "RUNNING"
                self.store._save(db, current)
            raise OSError("Controller failed after worker claimed attempt")
        with patch.object(self.store, "_schedule", side_effect=partial_dispatch):
            state = self.store.execute("r1", background=True)
        self.assertEqual(state["status"], "RUNNING")
        self.assertEqual(state["scheduler"]["status"], "RUNNING")
        self.assertFalse(self.store.snapshot()["receipts"])
        self.assertEqual(self.store.snapshot()["budget"]["cpu_seconds"]["reserved"], 1)
        with self.assertRaises(ValueError):
            self.store.execute("r1", background=True)

    def test_scheduled_worker_completion_records_terminal_lifecycle(self):
        # Start the same worker path without requiring an OS scheduler in CI.
        for rid, mode, terminal in (("r1", "ok", "COMPLETED"), ("r2", "nonzero", "FAILED")):
            with self.subTest(mode=mode):
                self.store.register(self.spec(rid, mode))
                task_id = "RDS-Project-" + rid
                with self.store._db() as db:
                    run = self.store._run(db, rid)
                    run["attempt_id"] = rid
                    run["scheduler"] = {"task_id": task_id, "status": "REGISTERED"}
                    self.store._save(db, run)
                receipt = self.store._execute_claim(rid, rid)
                scheduler = {"task_id": task_id, "status": terminal}
                self.assertEqual(receipt["scheduler"], scheduler)
                live = next(r for r in self.store.snapshot()["runs"] if r["id"] == rid)
                self.assertEqual(live["status"], terminal)
                self.assertEqual(live["scheduler"], scheduler)
                self.assertEqual(receipt["sha256"], digest({k:v for k,v in receipt.items() if k!="sha256"}))

    def test_nonwindows_background_is_explicitly_unsupported(self):
        self.store.register(self.spec())
        with patch("rds_project.os.name", "posix"):
            with self.assertRaises(NotImplementedError):
                self.store.execute("r1", background=True)
        self.assertIsNone(self.store.snapshot()["runs"][0]["attempt_id"])


HANG_SCRIPT = '''import sys, time
print("started", flush=True)
time.sleep(30)
'''


class StopPolicyAndMaintenanceTests(unittest.TestCase):
    """Stop policy and maintenance allowance; partial output and honest costs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rds stop ")
        self.root = Path(self.tmp.name)
        files = {"code": ("code.py", HANG_SCRIPT), "config": ("config.json", "{}"),
                 "data": ("data.json", "[1]"), "evaluator": ("evaluator.json", "{}")}
        for name, content in files.values():
            (self.root / name).write_text(content, encoding="utf-8")
        protocol = {"code_sha256": file_sha(self.root / "code.py"), "config_sha256": file_sha(self.root / "config.json"),
                    "data_sha256": file_sha(self.root / "data.json"), "data_split": "development-only",
                    "init": "none", "seed": 0, "checkpoint": "none", "schedule": "one calculation",
                    "sample_work": {"rows": 1}, "numeric_protocol": "Python float"}
        (self.root / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        files["protocol"] = ("protocol.json", "")
        self.contract = {"schema": 1, "bindings": [{"role": role, "path": name, "sha256": file_sha(self.root / name)}
                                                  for role, (name, _) in files.items()],
                         "allowed_commands": [[sys.executable, "-B", "code.py"] for _ in ("r1", "r2", "r3")],
                         "output_roots": ["outputs"], "budget": {"wall_seconds": 20, "cpu_seconds": 10, "gpu_seconds": 0}}
        self.store = ProjectStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def contract_with(self, stop_policy=None, maintenance_allowance=None):
        contract = json.loads(canonical(self.contract))
        if stop_policy is not None:
            contract["stop_policy"] = stop_policy
        if maintenance_allowance is not None:
            contract['maintenance_allowance'] = {**maintenance_allowance}
            if set(maintenance_allowance) == {'schema', 'wall_seconds', 'max_uses'}:
                self.bind_maintenance(contract)
        self.store.initialize(contract)
        return self.store

    def bind_maintenance(self, contract, *, supported=False, unrelated=False, stale=False, truncated=False):
        from rds_math import bind_objective
        goal = {'schema': 'rds-objective-v1', 'question_id': 'maintenance-goal', 'goal_revision': '1',
                'scope': {'task': 'synthetic-fixture'}, 'statement': 'Complete the original task',
                'domain': 'synthetic software inputs', 'quantifier_order': ['all fixture inputs'], 'assumptions': [],
                'evidence_standard': 'retained evidence', 'completion_standard': 'the original obligation is met'}
        record = bind_objective(self.root, canonical(goal).encode('utf-8'))
        graph = {'schema': 1, 'nodes': [
            {'id': 'config_health', 'status': 'SUPPORTED' if supported else 'UNKNOWN', 'source': 'synthetic blocker'},
            {'id': 'side_task', 'status': 'UNKNOWN', 'source': 'unrelated maintenance'},
            {'id': 'completion_standard', 'status': 'UNKNOWN', 'source': 'original goal'}],
            'hyperedges': [{'id': 'repair_closes', 'premises': ['side_task' if unrelated else 'config_health'],
                           'conclusion': 'completion_standard', 'status': 'SUPPORTED', 'source': 'declared implication'}],
            'goals': ['completion_standard']}
        if truncated:
            graph['limits'] = {'max_blocker_sets': 1}
            graph['hyperedges'].append({'id': 'alternate', 'premises': ['side_task'],
                                       'conclusion': 'completion_standard', 'status': 'SUPPORTED', 'source': 'alternative'})
        context = {'objective_binding': {'question_id': goal['question_id'], 'goal_revision': 'stale' if stale else '1',
                                         'sha256': record['asset']['sha256']}, 'scope': goal['scope'], 'dependency_map': graph}
        declaration = self.spec(timeout=0.4, maintenance=True)['maintenance']
        contract['allowed_commands'].append([sys.executable, '-B', 'code.py', 'ordinary'])
        context['action'] = {'argv': [sys.executable, '-B', 'code.py'], 'target': 'config_health',
                             'goal_contribution': declaration['goal_contribution']}
        path = self.root / 'maintenance-context.json'
        path.write_text(canonical(context), encoding='utf-8')
        ref = {'path': path.name, 'sha256': file_sha(path)}
        contract['bindings'].append({'role': 'config', **ref})
        contract['objective_sha256'] = record['asset']['sha256']
        contract['maintenance_allowance']['context'] = ref
        protocol = json.loads((self.root / 'protocol.json').read_text(encoding='utf-8'))
        protocol['config_sha256'] = ProjectStore._role_sha(contract, 'config')
        (self.root / 'protocol.json').write_text(canonical(protocol), encoding='utf-8')
        next(b for b in contract['bindings'] if b['role'] == 'protocol')['sha256'] = file_sha(self.root / 'protocol.json')

    def spec(self, rid="r1", timeout=15, maintenance=False):
        spec = {"schema": 1, "id": rid, "arm": "tool", "control_id": None,
                "protocol": {"path": "protocol.json", "sha256": file_sha(self.root / "protocol.json")},
                "argv": [sys.executable, "-B", "code.py"],
                "outpaths": [], "resource_estimates": {"wall_seconds": timeout + 0.2, "cpu_seconds": 1, "gpu_seconds": 0},
                "timeout_seconds": timeout}
        if maintenance:
            spec["maintenance"] = {"reason": "MAINTENANCE", "blocker": "node:config_health",
                                   "affected_obligation": "config_health",
                                   "repair": "check config reader", "acceptance": "code.py exits 0 with retained evidence",
                                   'goal_contribution': {'target': 'completion_standard',
                                                         'path': ['config_health', 'completion_standard'],
                                                         'source': 'synthetic fixture obligation'}}
        return spec

    def run_spec(self, spec):
        self.store.register(spec)
        return self.store.execute(spec["id"])

    def test_malformed_stop_policy_and_allowance_rejected_at_initialize(self):
        cases = [
            {"schema": 1, "wall_seconds": 10},
            {"schema": 1, "wall_seconds": 10, "progress": {}, "extra": 1},
            {"schema": "1", "wall_seconds": 10, "progress": {"window_seconds": 1, "min_bytes": 0}},
            {"schema": 1, "wall_seconds": 0, "progress": {"window_seconds": 1, "min_bytes": 0}},
            {"schema": 1, "wall_seconds": -5, "progress": {"window_seconds": 1, "min_bytes": 0}},
            {"schema": 1, "wall_seconds": 10, "progress": {"window_seconds": 1, "min_bytes": -1}},
            {"schema": 1, "wall_seconds": 10, "progress": {"window_seconds": 1, "min_bytes": 1.5}},
        ]
        for policy in cases:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    self.contract_with(stop_policy=policy)
        for allowance in ({"schema": 1, "wall_seconds": 5}, {"schema": 1, "max_uses": 2},
                          {"schema": 1, "wall_seconds": 5, "max_uses": 0}, {"schema": 1, "wall_seconds": 5, "max_uses": 65},
                          {"schema": 1, "wall_seconds": -1, "max_uses": 2}, {"schema": 1, "wall_seconds": 5, "max_uses": True},
                          {"schema": 1, "wall_seconds": 5, "max_uses": 2.5}):
            with self.subTest(allowance=allowance):
                with self.assertRaises(ValueError):
                    self.contract_with(maintenance_allowance=allowance)

    def test_campaign_deadline_stops_hang_and_preserves_partial_stdout(self):
        # The deadline starts at reservation; allow Windows process startup and scheduling
        # before asserting that deadline cleanup preserves the child's flushed output.
        # CI runners can need seconds to launch python.exe under load, so the campaign
        # deadline must outlast interpreter startup: otherwise the child is killed before
        # its first flushed line and stdout.bin is legitimately empty. Measure a real bare
        # spawn now and scale it: the deadline is fixture data, not a weakened assertion;
        # the hang still cannot reach its own 15s timeout, and every CAMPAIGN_DEADLINE/
        # size/cost assertion below is unchanged.
        spawn_probe = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], capture_output=True)
        startup_headroom = round(max(5.0, 40.0 * (time.perf_counter() - spawn_probe)), 3)
        store = self.contract_with(stop_policy={"schema": 1, "wall_seconds": startup_headroom,
                                                "progress": {"window_seconds": 3600, "min_bytes": 0}})
        receipt = self.run_spec(self.spec(timeout=15))
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assertEqual(receipt["stop_reason"], "CAMPAIGN_DEADLINE")
        self.assertFalse(receipt["timeout"])
        self.assertTrue(any("Stop policy: CAMPAIGN_DEADLINE" in error for error in receipt["errors"]))
        stdout = next(artifact for artifact in receipt["artifacts"] if artifact["kind"] == "stdout.bin")
        self.assertGreater(stdout["size"], 0)
        self.assertLess(receipt["resources"]["wall_seconds"]["measured"], 15)
        self.assertEqual(store.snapshot()["budget"]["cpu_seconds"]["charged_estimate"], 1)
        with self.assertRaises(ValueError):
            self.store.execute("r1")

    def test_progress_no_growth_stops_hang_after_window(self):
        store = self.contract_with(stop_policy={"schema": 1, "wall_seconds": 30,
                                                "progress": {"window_seconds": 0.2, "min_bytes": 10}})
        started = time.monotonic()
        receipt = self.run_spec(self.spec(timeout=15))
        evidence = self.progress_evidence(receipt)
        self.assertEqual(receipt["run_status"], "FAILED", evidence)
        self.assertEqual(receipt.get("stop_reason"), "PROGRESS_NO_GROWTH", evidence)
        self.assertGreater(time.monotonic() - started, 0.2)
        self.assertLess(receipt["resources"]["wall_seconds"]["measured"], 15)
        self.assertTrue(any("Stop policy: PROGRESS_NO_GROWTH" in error for error in receipt["errors"]))

    def bind_progress_code(self, source):
        """Keep the original code/protocol identities valid in synthetic fixtures."""
        (self.root / "code.py").write_text(source, encoding="utf-8")
        protocol = json.loads((self.root / "protocol.json").read_text(encoding="utf-8"))
        protocol["code_sha256"] = file_sha(self.root / "code.py")
        (self.root / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        for binding in self.contract["bindings"]:
            if binding["role"] == "code":
                binding["sha256"] = file_sha(self.root / "code.py")
            if binding["role"] == "protocol":
                binding["sha256"] = file_sha(self.root / "protocol.json")

    def progress_evidence(self, receipt):
        streams = {kind: (self.root / ".rds/project-artifacts/r1" / (kind + ".bin")).read_bytes().decode(
                            'utf-8', errors='backslashreplace')[:2048]
                   for kind in ('stdout', 'stderr')
                   if (self.root / ".rds/project-artifacts/r1" / (kind + ".bin")).is_file()}
        return canonical({'receipt': receipt, 'streams': streams, 'snapshot': self.store.snapshot()})

    def test_progress_window_with_growth_completes_normally(self):
        # The requirement is observed byte growth, not a sub-0.2s startup/scheduling SLA.
        # Use a real child and real monotonic clock, but synchronize each sampled write.
        # No stop predicate, threshold, output observation or success result is mocked.
        self.bind_progress_code('from pathlib import Path\nimport time\nfor i in range(6):\n'
                                '    while not Path(f"permit-{i}").exists(): time.sleep(0.002)\n'
                                '    print("x" * 64, flush=True)\n'
                                '    Path(f"ack-{i}").touch()\n')
        (self.root / 'permit-0').touch()
        store = self.contract_with(stop_policy={"schema": 1, "wall_seconds": 30,
                                                "progress": {"window_seconds": 0.2, "min_bytes": 10}})
        real_popen = subprocess.Popen
        child, steps = [], []

        def wait_for_ack(index):
            deadline = time.monotonic() + 4
            while not (self.root / f'ack-{index}').is_file():
                self.assertIsNone(child[0].poll(), 'Synthetic progress child exited before its write')
                self.assertLess(time.monotonic(), deadline, 'Synthetic progress child did not acknowledge its write')
                time.sleep(0.002)

        def spawn(*args, **kwargs):
            # The private diagnostic reproduced premature no-growth with slow launch.
            # Ensure the success fixture supplies first evidence before its first sample.
            time.sleep(0.3)
            process = real_popen(*args, **kwargs)
            child.append(process)
            wait_for_ack(0)
            return process

        def advance_stream(interval):
            time.sleep(interval)  # real polling intervals; the run spans a full window
            index = len(steps) + 1
            if index < 6:
                (self.root / f'permit-{index}').touch()
                wait_for_ack(index)
                steps.append(index)
            else:
                child[0].wait(timeout=4)

        clock = SimpleNamespace(monotonic=time.monotonic, time=time.time, sleep=advance_stream)
        try:
            with patch('rds_project.subprocess.Popen', side_effect=spawn), patch('rds_project.time', clock):
                receipt = self.run_spec(self.spec(timeout=5))
        finally:
            for process in child:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=4)
        evidence = self.progress_evidence(receipt)
        self.assertEqual(receipt["run_status"], "SUCCEEDED", evidence)
        self.assertNotIn("stop_reason", receipt, evidence)
        self.assertEqual(steps, [1, 2, 3, 4, 5], evidence)
        output = (self.root / '.rds/project-artifacts/r1/stdout.bin').read_bytes().splitlines()
        self.assertEqual(output, [b'x' * 64] * 6, evidence)
        self.assertGreaterEqual(receipt['resources']['wall_seconds']['measured'], 0.2, evidence)
        self.assertEqual(store.recover('r1')['sha256'], receipt['sha256'])
        self.assertEqual(store.snapshot()['budget']['cpu_seconds']['charged_estimate'], 1)

    def test_progress_delayed_first_output_is_no_growth_not_a_guaranteed_success(self):
        # This is the falsifier for the old timing assumption: eventually printing
        # does not satisfy a frozen 0.2s no-growth policy during a silent interval.
        self.bind_progress_code('import time\ntime.sleep(30)\nprint("late", flush=True)\n')
        store = self.contract_with(stop_policy={"schema": 1, "wall_seconds": 30,
                                                "progress": {"window_seconds": 0.2, "min_bytes": 10}})
        receipt = self.run_spec(self.spec(timeout=15))
        evidence = self.progress_evidence(receipt)
        self.assertEqual(receipt['run_status'], 'FAILED', evidence)
        self.assertEqual(receipt.get('stop_reason'), 'PROGRESS_NO_GROWTH', evidence)
        self.assertFalse(receipt['timeout'], evidence)
        self.assertEqual((self.root / '.rds/project-artifacts/r1/stdout.bin').read_bytes(), b'', evidence)
        self.assertEqual(store.snapshot()['budget']['cpu_seconds']['charged_estimate'], 1, evidence)
        self.assertGreater(receipt['resources']['wall_seconds']['measured'], 0, evidence)
        self.assertEqual(store.recover('r1')['sha256'], receipt['sha256'], evidence)

    def test_progress_stalled_after_observed_growth_retains_output_and_charge(self):
        self.bind_progress_code('from pathlib import Path\nimport time\n'
                                'print("x" * 64, flush=True)\nPath("ack-0").touch()\ntime.sleep(30)\n')
        store = self.contract_with(stop_policy={"schema": 1, "wall_seconds": 30,
                                                "progress": {"window_seconds": 0.2, "min_bytes": 10}})
        real_popen = subprocess.Popen
        child = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            child.append(process)
            deadline = time.monotonic() + 4
            while not (self.root / 'ack-0').is_file():
                self.assertIsNone(process.poll(), 'Synthetic stalled child exited before its write')
                self.assertLess(time.monotonic(), deadline, 'Synthetic stalled child did not acknowledge its write')
                time.sleep(0.002)
            return process

        try:
            with patch('rds_project.subprocess.Popen', side_effect=spawn):
                receipt = self.run_spec(self.spec(timeout=15))
        finally:
            for process in child:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=4)
        evidence = self.progress_evidence(receipt)
        self.assertEqual(receipt['run_status'], 'FAILED', evidence)
        self.assertEqual(receipt.get('stop_reason'), 'PROGRESS_NO_GROWTH', evidence)
        self.assertFalse(receipt['timeout'], evidence)
        self.assertEqual((self.root / '.rds/project-artifacts/r1/stdout.bin').read_bytes().splitlines(),
                         [b'x' * 64], evidence)
        self.assertEqual(store.snapshot()['budget']['cpu_seconds']['charged_estimate'], 1, evidence)
        self.assertEqual(store.snapshot()['budget']['cpu_seconds']['reserved'], 0, evidence)
        self.assertEqual(store.recover('r1')['sha256'], receipt['sha256'], evidence)

    def test_no_stop_policy_leaves_execution_unchanged(self):
        # Hang still runs to its own manifest timeout; regression guard.
        self.contract_with()
        receipt = self.run_spec(self.spec(timeout=0.25))
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assertTrue(receipt["timeout"])
        self.assertNotIn("stop_reason", receipt)

    def test_maintenance_run_requires_allowance_and_complete_declaration(self):
        self.contract_with()
        with self.assertRaisesRegex(ValueError, "maintenance_allowance"):
            self.store.register(self.spec(maintenance=True))
        # A run without the frozen allowance cannot even carry the field.
        with self.assertRaisesRegex(ValueError, "maintenance_allowance"):
            self.store.register(self.spec("r5", maintenance=True))
        for field, value, message in [('goal_contribution', None, 'must be an object'),
                                      ('reason', 'EXPERIMENT', 'reason must be MAINTENANCE'),
                                      ('acceptance', '  ', 'nonempty strings')]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                bad = self.spec(maintenance=True)
                bad['maintenance'][field] = value
                self.store.register(bad)
        with self.assertRaisesRegex(ValueError, 'maintenance must declare'):
            bad = self.spec(maintenance=True)
            del bad['maintenance']['acceptance']
            self.store.register(bad)

    def test_maintenance_allowance_admits_declared_runs_and_is_exhausted(self):
        store = self.contract_with(maintenance_allowance={"schema": 1, "wall_seconds": 16, "max_uses": 2})
        first = self.run_spec(self.spec("r1", timeout=0.25, maintenance=True))
        self.assertEqual(first["run_status"], "FAILED")
        self.assertEqual(first["timeout"], True)
        self.assertEqual(first["maintenance"], True)
        self.assertEqual(first["assessment"], {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN", "purpose": "MAINTENANCE"})
        self.run_spec(self.spec("r2", timeout=0.25, maintenance=True))
        events = store.snapshot()
        self.assertEqual(len([run for run in events["runs"]]), 2)
        with self.assertRaisesRegex(ValueError, "Maintenance allowance is exhausted"):
            self.store.register(self.spec("r3", maintenance=True))
        # Scientific runs are unaffected by the exhausted maintenance allowance.
        scientific = self.spec("r4", timeout=0.25)
        scientific['argv'].append('ordinary')
        scientific["resource_estimates"]["wall_seconds"] = 0.45
        scientific["outpaths"] = []
        store.register(scientific)
        self.assertEqual(store.execute("r4")["run_status"], "FAILED")

    def test_maintenance_wall_estimate_is_capped_by_allowance(self):
        self.contract_with(maintenance_allowance={"schema": 1, "wall_seconds": 1, "max_uses": 1})
        with self.assertRaisesRegex(ValueError, "exceeds the frozen maintenance_allowance"):
            self.store.register(self.spec(timeout=5, maintenance=True))
        self.assertEqual(self.store.snapshot()["runs"], [])
        self.assertEqual(self.store.snapshot()["budget"]["cpu_seconds"]["reserved"], 0)


    def test_maintenance_total_cap_preserves_failed_cost_and_rejects_rename(self):
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 1, 'max_uses': 4})
        before = store.snapshot()
        bad = self.spec(timeout=0.4, maintenance=True)
        bad['maintenance']['blocker'] = 'node:invented'
        with self.assertRaisesRegex(ValueError, 'current ready'):
            store.register(bad)
        self.assertEqual(store.snapshot(), before)
        self.run_spec(self.spec(timeout=0.4, maintenance=True))
        retained = store.snapshot()
        self.assertEqual(store.recover('r1')['assessment']['task_gain'], 'UNKNOWN')
        with self.assertRaisesRegex(ValueError, 'wall_seconds total'):
            ProjectStore(self.root).register(self.spec('renamed', timeout=0.4, maintenance=True))
        self.assertEqual(store.snapshot(), retained)

    def test_zero_maintenance_cap_disables_admission(self):
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 0, 'max_uses': 4})
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, 'wall_seconds total'):
            store.register(self.spec(timeout=0.4, maintenance=True))
        self.assertEqual(store.snapshot(), before)

    def test_repair_command_cannot_omit_maintenance_or_substitute_another_command(self):
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 5, 'max_uses': 4})
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, 'requires a maintenance declaration'):
            store.register(self.spec(timeout=0.4))
        bad = self.spec(timeout=0.4, maintenance=True)
        bad['argv'].append('ordinary')
        with self.assertRaisesRegex(ValueError, 'bound repair action'):
            store.register(bad)
        self.assertEqual(store.snapshot(), before)

    def test_side_task_and_mismatched_action_are_refused_atomically(self):
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 5, 'max_uses': 4})
        before = store.snapshot()
        for field, value in [('affected_obligation', 'side_task'),
                             ('goal_contribution', {'target': 'side_task', 'path': ['config_health', 'side_task'], 'source': 'x'}),
                             ('goal_contribution', {'target': 'completion_standard', 'path': ['side_task', 'completion_standard'], 'source': 'x'}),
                             ('goal_contribution', {'target': 'completion_standard', 'path': ['config_health', 'invented', 'completion_standard'], 'source': 'x'})]:
            with self.subTest(field=field, value=value):
                spec = self.spec(timeout=0.4, maintenance=True)
                spec['maintenance'][field] = value
                with self.assertRaisesRegex(ValueError, 'original-goal'):
                    store.register(spec)
                self.assertEqual(store.snapshot(), before)

    def test_completed_and_unrelated_graph_obligations_are_refused(self):
        for option in ('supported', 'unrelated', 'truncated'):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as folder:
                for name in ('code.py', 'config.json', 'data.json', 'evaluator.json', 'protocol.json'):
                    shutil.copyfile(self.root / name, Path(folder) / name)
                original_root = self.root
                self.root = Path(folder)
                try:
                    contract = json.loads(canonical(self.contract))
                    contract['maintenance_allowance'] = {'schema': 1, 'wall_seconds': 5, 'max_uses': 4}
                    self.bind_maintenance(contract, **{option: True})
                    store = ProjectStore(self.root)
                    store.initialize(contract)
                    before = store.snapshot()
                    with self.assertRaises(ValueError):
                        store.register(self.spec(timeout=0.4, maintenance=True))
                    self.assertEqual(store.snapshot(), before)
                finally:
                    self.root = original_root

    def test_stale_objective_context_is_refused_before_contract_init(self):
        contract = json.loads(canonical(self.contract))
        contract['maintenance_allowance'] = {'schema': 1, 'wall_seconds': 5, 'max_uses': 4}
        self.bind_maintenance(contract, stale=True)
        with self.assertRaisesRegex(ValueError, 'frozen objective'):
            self.store.initialize(contract)
        with self.store._db(True) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='contract'").fetchone())

    def test_changed_context_refused_before_launch_and_keeps_reservation(self):
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 5, 'max_uses': 4})
        store.register(self.spec(timeout=0.4, maintenance=True))
        (self.root / 'maintenance-context.json').write_text('{}', encoding='utf-8')
        before = store.snapshot()
        with patch('rds_project.subprocess.Popen') as launch, self.assertRaisesRegex(ValueError, 'binding changed'):
            ProjectStore(self.root).execute('r1')
        launch.assert_not_called()
        self.assertEqual(store.snapshot(), before)

    def test_deadline_survives_rename_reopen_and_recovery(self):
        store = self.contract_with(stop_policy={'schema': 1, 'wall_seconds': 20,
                                                'progress': {'window_seconds': 10, 'min_bytes': 0}})
        with patch('rds_project.time.time', return_value=1000):
            store.register(self.spec(timeout=0.4))
            store.register(self.spec('queued', timeout=0.4))
        before = store.snapshot()
        with patch('rds_project.time.time', return_value=1021), patch('rds_project.subprocess.Popen') as launch:
            self.assertEqual(store.recover('r1')['status'], 'RESERVED')
            with self.assertRaisesRegex(ValueError, 'CAMPAIGN_DEADLINE'):
                ProjectStore(self.root).register(self.spec('renamed', timeout=0.4))
            with self.assertRaisesRegex(ValueError, 'CAMPAIGN_DEADLINE'):
                ProjectStore(self.root).execute('queued')
        launch.assert_not_called()
        self.assertEqual(store.snapshot(), before)

    def test_queued_worker_records_prestart_deadline_and_terminal_receipt(self):
        store = self.contract_with(stop_policy={'schema': 1, 'wall_seconds': 20,
                                                'progress': {'window_seconds': 10, 'min_bytes': 0}})
        with patch('rds_project.time.time', return_value=1000):
            store.register(self.spec(timeout=0.4))
        with store._db() as db:
            run = store._run(db, 'r1')
            run['attempt_id'] = 'queued-attempt'
            store._save(db, run)
        with patch('rds_project.time.time', return_value=1021), patch('rds_project.subprocess.Popen') as launch:
            receipt = store._execute_claim('r1', 'queued-attempt')
        launch.assert_not_called()
        self.assertEqual(receipt['stop_reason'], 'CAMPAIGN_DEADLINE')
        self.assertEqual(receipt['run_status'], 'FAILED')
        self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(store.recover('r1'), receipt)

    def test_concurrent_maintenance_admission_obeys_total_cap(self):
        from concurrent.futures import ThreadPoolExecutor
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 1, 'max_uses': 4})
        def reserve(rid):
            try:
                ProjectStore(self.root).register(self.spec(rid, timeout=0.4, maintenance=True))
                return 'RESERVED'
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ('r1', 'r2')))
        self.assertEqual(results.count('RESERVED'), 1, results)
        self.assertEqual(len(store.snapshot()['runs']), 1)
        self.assertAlmostEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0.6)

    def test_late_maintenance_overrun_blocks_reserved_start_and_new_admission(self):
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 1.2, 'max_uses': 4})
        store.register(self.spec('r1', timeout=0.4, maintenance=True))
        store.register(self.spec('r2', timeout=0.4, maintenance=True))
        with store._db() as db:
            run = store._run(db, 'r1')
            run['attempt_id'] = 'overrun'
            store._save(db, run)
        store._finish('r1', 'overrun', 'FAILED', 1, 0.8, True, ['retained synthetic overrun'])
        before = store.snapshot()
        with patch('rds_project.subprocess.Popen') as launch, self.assertRaisesRegex(ValueError, 'exhausted before start'):
            store.execute('r2')
        launch.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'wall_seconds total'):
            store.register(self.spec('r3', timeout=0.1, maintenance=True))
        self.assertEqual(store.snapshot(), before)

    def test_quick_and_theory_routes_cannot_discard_configured_policy(self):
        store = self.contract_with(stop_policy={'schema': 1, 'wall_seconds': 20,
                                                'progress': {'window_seconds': 10, 'min_bytes': 0}})
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, 'cannot bypass'):
            with store.theory_allowance(self.spec(timeout=0.4), {}, {'wall_seconds': 1, 'cpu_seconds': 1, 'gpu_seconds': 0}):
                self.fail('Policy bypassed')
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts/rds_cli.py'),
                                '--root', str(self.root), 'exec', '--name', 'bypass', '--timeout', '0.4', '--', 'code.py'],
                                capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('cannot bypass', result.stderr)
        self.assertEqual(store.snapshot(), before)
        self.assertFalse((self.root / '.rds' / 'jobs').exists())

    def test_actual_cli_maintenance_refusal_changes_no_state(self):
        store = self.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 1, 'max_uses': 4})
        before = store.snapshot()
        bad = self.spec(timeout=0.4, maintenance=True)
        bad['maintenance']['blocker'] = 'node:nonexistent'
        path = self.root / 'bad-manifest.json'
        path.write_text(canonical(bad), encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts/rds_cli.py'),
                                 '--root', str(self.root), 'project', 'create', '--manifest', str(path)],
                                capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('[RDS-REJECT]', result.stderr)
        self.assertIn('current ready', result.stderr)
        self.assertEqual(store.snapshot(), before)

    def test_execution_policy_observes_valid_maintenance_receipt_after_deadline(self):
        (self.root / 'code.py').write_text('print("synthetic repair check", flush=True)\n', encoding='utf-8')
        protocol = json.loads((self.root / 'protocol.json').read_text(encoding='utf-8'))
        protocol['code_sha256'] = file_sha(self.root / 'code.py')
        (self.root / 'protocol.json').write_text(canonical(protocol), encoding='utf-8')
        for binding in self.contract['bindings']:
            if binding['role'] in {'code', 'protocol'}:
                binding['sha256'] = file_sha(self.root / binding['path'])
        self.contract['execution_policy'] = {'schema': 1, 'max_attempts': 2}
        store = self.contract_with(stop_policy={'schema': 1, 'wall_seconds': 30,
                                               'progress': {'window_seconds': 10, 'min_bytes': 0}},
                                   maintenance_allowance={'schema': 1, 'wall_seconds': 5, 'max_uses': 4})
        receipt = self.run_spec(self.spec(timeout=2, maintenance=True))
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        before = store.snapshot()
        with patch('rds_project.time.time', return_value=time.time() + 31), patch('rds_project.subprocess.Popen') as launch:
            observed = ProjectStore(self.root).execute('r1')
        launch.assert_not_called()
        self.assertFalse(observed['execution_started'])
        self.assertEqual(observed['sha256'], receipt['sha256'])
        self.assertEqual(observed['assessment'], {'task_gain': 'UNKNOWN', 'mechanism': 'UNKNOWN', 'purpose': 'MAINTENANCE'})
        self.assertEqual(store.snapshot(), before)


class ProtocolIdentityMessageTests(unittest.TestCase):
    """#154: a protocol/binding conflict names the role, the expected value and the rule, while it can still be fixed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rds protocol identity ")
        self.env = {**os.environ, "RDS_USAGE_DB": str(Path(self.tmp.name) / "usage.db")}
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "project-runner"))
        from prepare import prepare
        self.prepare = prepare

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, root, *args):
        return subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "scripts" / "rds_cli.py"),
                               "--root", str(root), *args], capture_output=True, text=True, env=self.env, timeout=60)

    def project(self, name, extra_code=False):
        root = Path(self.prepare(Path(self.tmp.name) / name)["root"])
        if extra_code:  # A second source file, as in most real projects.
            (root / "helper.py").write_text("HELPER = 1\n", encoding="utf-8")
            contract = json.loads((root / "contract.json").read_text(encoding="utf-8"))
            contract["bindings"].append({"role": "code", "path": "helper.py", "sha256": file_sha(root / "helper.py")})
            (root / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        return root

    def edit_protocol(self, root, **fields):
        """Rewrite protocol.json and every reference to its SHA256, as an agent repairing the project would."""
        protocol = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
        protocol.update(fields)
        (root / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        sha = file_sha(root / "protocol.json")
        for name, refs in (("contract.json", lambda c: [b for b in c["bindings"] if b["role"] == "protocol"]),
                           ("control.json", lambda m: [m["protocol"]])):
            body = json.loads((root / name).read_text(encoding="utf-8"))
            for ref in refs(body):
                ref["sha256"] = sha
            (root / name).write_text(json.dumps(body), encoding="utf-8")

    def init(self, root):
        return self.cli(root, "project", "init", "--contract", str(root / "contract.json"))

    def test_multi_file_conflict_is_refused_before_the_contract_freezes_and_is_fixable_in_place(self):
        root = self.project("multi", extra_code=True)
        rejected = self.init(root)
        self.assertNotEqual(rejected.returncode, 0)
        expected = digest(sorted([{"path": "experiment.py", "sha256": file_sha(root / "experiment.py")},
                                  {"path": "helper.py", "sha256": file_sha(root / "helper.py")}], key=lambda b: b["path"]))
        message = rejected.stdout + rejected.stderr
        self.assertIn("Protocol identity conflicts with bindings: code_sha256 must be " + expected, message)
        self.assertIn("canonical digest of the 2 'code' bindings", message)
        self.assertIn("in protocol.json; the protocol is frozen with the contract", message)
        self.assertFalse((root / ".rds" / "project.sqlite3").exists())
        # Following the message in the same root initializes it and registers the run; no new root is needed.
        self.edit_protocol(root, code_sha256=expected)
        self.assertEqual(self.init(root).returncode, 0)
        registered = self.cli(root, "project", "create", "--manifest", str(root / "control.json"))
        self.assertEqual(registered.returncode, 0, registered.stdout + registered.stderr)
        self.assertEqual([run["id"] for run in ProjectStore(root).snapshot()["runs"]], ["control"])

    def test_single_binding_conflict_names_the_file_hash(self):
        root = self.project("single")
        self.edit_protocol(root, config_sha256="0" * 64)
        rejected = self.init(root)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("config_sha256 must be " + file_sha(root / "config.json")
                      + " (the SHA256 of the one 'config' binding)", rejected.stdout + rejected.stderr)
        self.assertFalse((root / ".rds" / "project.sqlite3").exists())

    def test_protocol_files_that_cannot_register_are_rejected_before_the_contract_freezes(self):
        """#159: a JSON protocol missing identity fields would freeze a contract that can never register a run."""
        # Prose protocols stay initable: registration rejects them only when a run names one.
        prose = self.project("prose")
        (prose / "protocol.md").write_text("# Protocol\nPrimary metric: mse.\n", encoding="utf-8")
        contract = json.loads((prose / "contract.json").read_text(encoding="utf-8"))
        contract["bindings"].append({"role": "protocol", "path": "protocol.md", "sha256": file_sha(prose / "protocol.md")})
        (prose / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        self.assertEqual(self.init(prose).returncode, 0)
        # A JSON protocol missing identity fields is rejected at init and fixable in the same root.
        root = self.project("partial")
        (root / "partial.json").write_text(json.dumps({"seed": 3}), encoding="utf-8")
        contract = json.loads((root / "contract.json").read_text(encoding="utf-8"))
        contract["bindings"].append({"role": "protocol", "path": "partial.json", "sha256": file_sha(root / "partial.json")})
        (root / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        rejected = self.init(root)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("Protocol identity fields required: code_sha256, config_sha256, data_sha256, data_split, init, "
                      "checkpoint, schedule, sample_work, numeric_protocol; exec can complete operational identity "
                      "fields in partial.json; the protocol is frozen with the contract", rejected.stdout + rejected.stderr)
        self.assertFalse((root / ".rds" / "project.sqlite3").exists())
        # Corrected in place: the unusable protocol binding is removed and the contract initializes.
        (root / "partial.json").unlink()
        contract["bindings"] = [b for b in contract["bindings"] if b["path"] != "partial.json"]
        (root / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        self.assertEqual(self.init(root).returncode, 0)

if __name__ == "__main__":
    unittest.main()
