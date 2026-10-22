"""Production attempt/outbox transactions on finite, synthetic local commands."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_outbox import Outbox
from rds_project import ProjectStore, canonical, file_sha


SCRIPT = '''import pathlib, sys, time
mode, output = sys.argv[1:]
with pathlib.Path('launches.txt').open('a') as stream:
    stream.write(mode + '\\n')
if mode == 'timeout': time.sleep(3)
if mode == 'nonzero': sys.exit(7)
if mode != 'missing': pathlib.Path(output).write_text('{}')
'''


class ProjectEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rds-project-events-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        files = {"code": ("code.py", SCRIPT), "config": ("config.json", "{}"),
                 "data": ("data.json", "[]"), "evaluator": ("evaluator.json", "{}")}
        for name, body in files.values():
            (self.root / name).write_text(body, encoding="utf-8")
        protocol = {r + "_sha256": file_sha(self.root / files[r][0])
                    for r in ("code", "config", "data")}
        protocol.update(data_split="synthetic", init="none", seed=0, checkpoint="none",
                        schedule="one command", sample_work={"rows": 0}, numeric_protocol="Python")
        (self.root / "protocol.json").write_text(canonical(protocol), encoding="utf-8")
        files["protocol"] = ("protocol.json", "")
        self.contract = {"schema": 1,
            "bindings": [{"role": r, "path": n, "sha256": file_sha(self.root / n)}
                         for r, (n, _) in files.items()],
            "allowed_commands": [[sys.executable, "-X", "utf8", "-B", "code.py", mode, "outputs/r1.json"]
                                 for mode in ("ok", "missing", "nonzero", "timeout")],
            "output_roots": ["outputs"],
            "budget": {"wall_seconds": 20, "cpu_seconds": 10, "gpu_seconds": 0}}
        self.store = ProjectStore(self.root)

    def prepare(self, mode="ok", timeout=2, **contract_fields):
        self.contract.update(contract_fields)
        self.store.initialize(self.contract)
        self.spec = {"schema": 1, "id": "r1", "arm": "control", "control_id": None,
            "protocol": {"path": "protocol.json", "sha256": file_sha(self.root / "protocol.json")},
            "argv": [sys.executable, "-X", "utf8", "-B", "code.py", mode, "outputs/r1.json"],
            "outpaths": ["outputs/r1.json"],
            "resource_estimates": {"wall_seconds": timeout + .2, "cpu_seconds": 1, "gpu_seconds": 0},
            "timeout_seconds": timeout}
        return self.store.register(self.spec)

    def events(self):
        with self.store._db(True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='outbox_events'").fetchone():
                return []
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM outbox_events ORDER BY event_id")]

    def schema(self):
        with self.store._db(True) as db:
            return (db.execute("PRAGMA schema_version").fetchone()[0],
                    list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name")))

    def assert_terminal(self, receipt, kinds):
        events = self.events()
        self.assertEqual([e["kind"] for e in events], kinds)
        self.assertEqual([e["sequence"] for e in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(e["job_id"] == "r1" and e["attempt_id"] == receipt["attempt_id"] for e in events))
        payload = events[-1]["payload"]
        self.assertEqual(payload["receipt_sha256"], receipt["sha256"])
        self.assertEqual(payload["exit_code"], receipt["exit_code"])
        self.assertEqual(payload["assessment"], {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN"})
        return events

    def test_success_execute_reopen_recover_and_policy_observation_do_not_repeat(self):
        self.prepare(execution_policy={"schema": 1, "max_attempts": 1})
        receipt = self.store.execute("r1")
        events = self.assert_terminal(receipt, ["JOB_STARTED", "RESULT_READY"])
        self.assertEqual(events[0]["payload"]["phase"], "ATTEMPT_RUNNING")
        self.assertEqual(events[0]["payload"]["run_status"], "RUNNING")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertTrue(receipt["resources"]["cpu_seconds"]["unknown"])
        before, schema = self.store.snapshot(), self.schema()
        self.store = ProjectStore(self.root)
        with patch("rds_project._WindowsPopen", side_effect=AssertionError("duplicate launch")):
            for _ in range(2):
                self.assertEqual(self.store.recover("r1")["sha256"], receipt["sha256"])
                self.assertEqual(self.store.execute("r1")["sha256"], receipt["sha256"])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.events(), events)
        self.assertEqual(self.schema(), schema)
        self.assertEqual((self.root / "launches.txt").read_text(), "ok\n")

    def test_nonzero_keeps_rc_and_is_job_failed(self):
        self.prepare("nonzero")
        receipt = self.store.execute("r1")
        self.assertEqual(receipt["exit_code"], 7)
        events = self.assert_terminal(receipt, ["JOB_STARTED", "JOB_FAILED"])
        self.assertNotIn("limit", events[-1]["payload"])

    def test_missing_output_keeps_zero_rc_but_is_job_failed(self):
        self.prepare("missing")
        receipt = self.store.execute("r1")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["run_status"], "FAILED")
        self.assert_terminal(receipt, ["JOB_STARTED", "JOB_FAILED"])

    def test_observed_timeout_is_resource_limit(self):
        self.prepare("timeout", timeout=.35)
        receipt = self.store.execute("r1")
        self.assertTrue(receipt["timeout"])
        events = self.assert_terminal(receipt, ["JOB_STARTED", "RESOURCE_LIMIT"])
        self.assertEqual(events[-1]["payload"]["limit"],
                         {"cause": "PROCESS_TIMEOUT", "resource": "wall_seconds", "seconds": .35})

    def test_campaign_deadline_is_observed_resource_limit(self):
        self.prepare("timeout", stop_policy={"schema": 1, "wall_seconds": .45,
                     "progress": {"window_seconds": 10, "min_bytes": 1}})
        receipt = self.store.execute("r1")
        self.assertEqual(receipt["stop_reason"], "CAMPAIGN_DEADLINE")
        events = self.assert_terminal(receipt, ["JOB_STARTED", "RESOURCE_LIMIT"])
        self.assertEqual(events[-1]["payload"]["limit"]["cause"], "CAMPAIGN_DEADLINE")

    def test_progress_stall_is_failure_without_inventing_resource_limit(self):
        self.prepare("timeout", stop_policy={"schema": 1, "wall_seconds": 10,
                     "progress": {"window_seconds": .2, "min_bytes": 1}})
        receipt = self.store.execute("r1")
        self.assertEqual(receipt["stop_reason"], "PROGRESS_NO_GROWTH")
        events = self.assert_terminal(receipt, ["JOB_STARTED", "JOB_FAILED"])
        self.assertNotIn("limit", events[-1]["payload"])

    def test_observed_deadline_between_admission_and_start_has_no_started_event(self):
        self.prepare(stop_policy={"schema": 1, "wall_seconds": 10,
                     "progress": {"window_seconds": 10, "min_bytes": 1}})
        original, calls = self.store._check_start, []
        def check(db, run):
            calls.append(run["attempt_id"])
            if len(calls) == 2:
                raise ValueError("Stop policy: CAMPAIGN_DEADLINE")
            return original(db, run)
        with patch.object(self.store, "_check_start", new=check):
            receipt = self.store.execute("r1")
        self.assertIsNone(calls[0])
        self.assertEqual(calls[1], receipt["attempt_id"])
        self.assertFalse(receipt["process_started"])
        events = self.assert_terminal(receipt, ["RESOURCE_LIMIT"])
        self.assertEqual(events[0]["payload"]["limit"]["cause"], "CAMPAIGN_DEADLINE")
        self.assertFalse((self.root / "launches.txt").exists())

    def test_live_dispatched_attempt_recovery_does_not_emit_or_settle(self):
        self.prepare()
        with patch.object(self.store, "_execute_claim", return_value=None):
            self.store.execute("r1")
        before, schema = self.store.snapshot(), self.schema()
        observed = self.store.recover("r1")
        self.assertEqual(observed["status"], "RESERVED")
        self.assertIn("Process may still be active", observed["recovery"])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.schema(), schema)
        self.assertEqual(self.events(), [])

    def test_scheduler_failure_before_running_has_only_terminal_event(self):
        if os.name != "nt":
            self.skipTest("production background admission is Windows-only")
        self.prepare()
        with patch.object(self.store, "_schedule", side_effect=OSError("synthetic dispatch failure")):
            receipt = self.store.execute("r1", background=True)
        events = self.assert_terminal(receipt, ["JOB_FAILED"])
        self.assertIsNone(receipt["process_started"])
        self.assertIsNone(events[0]["payload"]["exit_code"])
        self.assertNotIn("limit", events[0]["payload"])

    def test_start_event_and_schema_roll_back_with_running_transition(self):
        self.prepare()
        publish = Outbox.publish
        def fail(outbox, event):
            publish(outbox, event)
            raise RuntimeError("synthetic start transaction interruption")
        with patch.object(Outbox, "publish", new=fail):
            with self.assertRaisesRegex(RuntimeError, "start transaction interruption"):
                self.store.execute("r1")
        state = self.store.snapshot()
        run = state["runs"][0]
        self.assertEqual(run["status"], "RESERVED")
        self.assertIsNone(run["started_at"])
        self.assertIsNotNone(run["attempt_id"])
        self.assertEqual(self.events(), [])
        self.assertEqual(state["receipts"], [])
        self.assertFalse((self.root / "launches.txt").exists())
        self.assertFalse(any(name.startswith("outbox_") for name, _ in self.schema()[1]))
        with patch("rds_project._alive", return_value=False):
            receipt = ProjectStore(self.root).recover("r1")
        self.assertEqual(receipt["attempt_id"], run["attempt_id"])
        self.assert_terminal(receipt, ["JOB_FAILED"])

    def test_terminal_emit_failure_rolls_back_receipt_budget_and_event_then_recovers_once(self):
        self.prepare()
        publish, retained = Outbox.publish, []
        def fail(outbox, event):
            if event["kind"] != "JOB_STARTED":
                retained.append(self.store.snapshot())
                publish(outbox, event)
                raise RuntimeError("synthetic settlement interruption")
            return publish(outbox, event)
        with patch.object(Outbox, "publish", new=fail):
            with self.assertRaisesRegex(RuntimeError, "settlement interruption"):
                self.store.execute("r1")
        state = self.store.snapshot()
        self.assertEqual(state, retained[0])
        self.assertEqual(state["runs"][0]["status"], "RUNNING")
        self.assertEqual(state["receipts"], [])
        self.assertEqual([e["kind"] for e in self.events()], ["JOB_STARTED"])
        with patch("rds_project._alive", return_value=False):
            receipt = ProjectStore(self.root).recover("r1")
        self.assertEqual(receipt["run_status"], "INTERRUPTED")
        self.assertIsNone(receipt["exit_code"])
        self.assertTrue(receipt["resources"]["wall_seconds"]["unknown"])
        events = self.assert_terminal(receipt, ["JOB_STARTED", "JOB_FAILED"])
        settled = self.store.snapshot()
        self.assertEqual(settled["budget"]["wall_seconds"]["reserved"], 0)
        for _ in range(2):
            self.assertEqual(self.store.recover("r1")["sha256"], receipt["sha256"])
        self.assertEqual(self.store.snapshot(), settled)
        self.assertEqual(self.events(), events)
        self.assertEqual((self.root / "launches.txt").read_text(), "ok\n")

    def test_legacy_read_paths_do_not_initialize_outbox(self):
        self.prepare()
        before = self.schema()
        for _ in range(2):
            self.store.snapshot()
            self.store.next_move()
            self.store.recover("r1")
        self.assertEqual(self.schema(), before)
        self.assertEqual(self.events(), [])

    def test_existing_receipt_recovery_without_outbox_does_not_backfill(self):
        self.prepare()
        with patch("rds_project.project_events.publish_started"), \
             patch("rds_project.project_events.publish_finished"):
            receipt = self.store.execute("r1")
        before, schema = self.store.snapshot(), self.schema()
        self.assertEqual(self.events(), [])
        for _ in range(2):
            self.assertEqual(ProjectStore(self.root).recover("r1")["sha256"], receipt["sha256"])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.schema(), schema)

    def test_legacy_non_uuid_attempt_settles_without_rekey_or_schema_migration(self):
        self.prepare()
        with self.store._db() as db:
            run = self.store._run(db, "r1")
            run.update(status="RUNNING", attempt_id="lost-attempt", worker_pid=None,
                       pid=None, started_at=time.time() - 2, observed_wall_seconds=.3)
            self.store._save(db, run)
        schema = self.schema()
        receipt = self.store.recover("r1")
        self.assertEqual(receipt["attempt_id"], "lost-attempt")
        self.assertEqual(receipt["run_status"], "INTERRUPTED")
        self.assertIsNone(receipt["exit_code"])
        self.assertEqual(receipt["resources"]["wall_seconds"]["observed_lower_bound"], .3)
        self.assertTrue(receipt["resources"]["wall_seconds"]["unknown"])
        self.assertEqual(self.schema(), schema)
        self.assertEqual(self.events(), [])
        before = self.store.snapshot()
        self.assertEqual(self.store.recover("r1")["sha256"], receipt["sha256"])
        self.assertEqual(self.store.snapshot(), before)

    def test_admission_rejection_does_not_emit_or_create_attempt(self):
        self.prepare()
        before, schema = self.store.snapshot(), self.schema()
        def reject(db, run):
            raise ValueError("synthetic admission denied")
        with self.assertRaisesRegex(ValueError, "admission denied"):
            self.store.execute("r1", admission_guard=reject)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.schema(), schema)

    def test_terminal_events_borrow_original_settlement_connection(self):
        self.prepare()
        original, seen = self.store._open_db, []
        @contextmanager
        def track(*args, **kwargs):
            with original(*args, **kwargs) as db:
                if kwargs.get("settlement") is not None:
                    seen.append(db)
                yield db
        publish = Outbox.publish
        def check(outbox, event):
            self.assertTrue(outbox.db.in_transaction)
            self.assertEqual(Path(outbox.db.execute("PRAGMA database_list").fetchone()[2]), self.store.path)
            if event["kind"] != "JOB_STARTED":
                self.assertIs(outbox.db, seen[-1])
                row = outbox.db.execute("SELECT sha256 FROM receipts WHERE run_id='r1'").fetchone()
                self.assertEqual(row[0], event["payload"]["receipt_sha256"])
                self.assertEqual(outbox.db.execute("SELECT reserved FROM budget WHERE resource='wall_seconds'").fetchone()[0], 0)
            return publish(outbox, event)
        with patch.object(self.store, "_open_db", new=track), patch.object(Outbox, "publish", new=check):
            receipt = self.store.execute("r1")
        self.assert_terminal(receipt, ["JOB_STARTED", "RESULT_READY"])


class ProjectInitRegressionTests(unittest.TestCase):
    """Check initialization independently of the unassigned query extraction.

    Only the post-commit snapshot return is replaced; validation, initialization
    and freezing use the real production writer and the original ledger.
    """
    def setUp(self):
        self.fixture = ProjectEventTests("test_legacy_read_paths_do_not_initialize_outbox")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.contract = self.fixture.store, self.fixture.contract

    def test_initialize_validated_budget_caps_on_original_ledger(self):
        marker = object()
        with patch.object(self.store, "snapshot", return_value=marker):
            self.assertIs(self.store.initialize(self.contract), marker)
        with self.store._db(True) as db:
            self.assertEqual(json.loads(db.execute("SELECT body FROM contract WHERE id=1").fetchone()[0]),
                             self.contract)
            rows = db.execute("SELECT resource,cap,spent,charged,reserved FROM budget ORDER BY resource").fetchall()
            self.assertEqual([tuple(row) for row in rows],
                             [("cpu_seconds", 10.0, 0.0, 0.0, 0.0),
                              ("gpu_seconds", 0.0, 0.0, 0.0, 0.0),
                              ("wall_seconds", 20.0, 0.0, 0.0, 0.0)])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='outbox_events'").fetchone())

    def test_invalid_caps_are_rejected_before_initialization(self):
        for value in (True, -1, float("inf"), "20"):
            with self.subTest(value=value):
                contract = {**self.contract, "budget": {"wall_seconds": value}}
                with self.assertRaisesRegex(ValueError, "Invalid budget.wall_seconds"):
                    self.store.initialize(contract)
                self.assertFalse(self.store.path.exists())

    def test_reinitialize_same_contract_preserves_accounting_and_rejects_new_cap(self):
        with patch.object(self.store, "snapshot", return_value={}):
            self.store.initialize(self.contract)
            with self.store._db() as db:
                db.execute("UPDATE budget SET spent=2 WHERE resource='wall_seconds'")
            self.store.initialize(self.contract)
            changed = {**self.contract, "budget": {**self.contract["budget"], "wall_seconds": 21}}
            with self.assertRaisesRegex(ValueError, "Contract is frozen"):
                self.store.initialize(changed)
        with self.store._db(True) as db:
            self.assertEqual(tuple(db.execute("SELECT cap,spent FROM budget WHERE resource='wall_seconds'").fetchone()),
                             (20.0, 2.0))


if __name__ == "__main__":
    unittest.main()
