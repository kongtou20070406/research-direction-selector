"""Real native CLI fixture setup; only the new prototype reader is exercised."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests")]
import test_rds_steering as fixtures
from rds_project import ProjectStore
from rds_steering import plan

spec = importlib.util.spec_from_file_location("advisor_view", Path(__file__).with_name("advisor_view.py"))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class AdvisorViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SteeringCLITests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = ProjectStore(self.fixture.root)

    def pin(self):
        return plan(self.store, dialogue=True).get("snapshot_sha256")

    def files(self):
        return {p.relative_to(self.store.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.store.root.rglob("*") if p.is_file()}

    def test_native_report_expansion_is_complete_read_only_and_not_admission(self):
        self.fixture.initialize(mutate_policy=fixtures.SteeringCLITests.independent_routes)
        advice = self.fixture.output("project", "next")
        pin, before = self.pin(), self.files()
        with patch("rds_owned_advisor.review", side_effect=AssertionError("Reader collected new advice")), \
             patch.object(self.store, "execute", side_effect=AssertionError("Reader dispatched")):
            view = adapter.read_view(self.store, expected_snapshot_sha256=pin)
        self.assertEqual(view["status"], "COMPATIBLE_RECORDED_ADVICE")
        self.assertEqual(view["native_report"], advice)
        self.assertEqual(view["native_plan"]["dialogue"]["selected_run"], advice["selected_run"])
        self.assertEqual(view["native_report"]["coverage"], advice["coverage"])
        self.assertFalse(view["execution_authorized"])
        self.assertEqual(view["scientific_support"], "UNKNOWN")
        self.assertEqual(view["admission"], "RECHECK_REQUIRED_BEFORE_DISPATCH")
        self.assertEqual(self.files(), before)
        self.assertEqual(self.fixture.starts(), [])

    def test_new_missing_and_stale_advice_keep_native_unknown_diagnostics(self):
        new = adapter.read_view(self.store, expected_snapshot_sha256=None)
        self.assertEqual(new["status"], "NO_CURRENT_ADVICE")
        self.assertIsNone(new["native_report"])
        self.assertFalse(self.store.path.exists())
        self.fixture.initialize()
        missing = adapter.read_view(self.store, expected_snapshot_sha256=self.pin())
        self.assertEqual(missing["status"], "NO_CURRENT_ADVICE")
        self.fixture.output("project", "next")
        (self.store.root / "config.json").write_text('{"changed":true}', encoding="utf-8")
        before = self.files()
        stale = adapter.read_view(self.store, expected_snapshot_sha256=self.pin())
        self.assertEqual(stale["status"], "STALE_OR_UNAVAILABLE")
        self.assertIsNone(stale["native_report"])
        self.assertIsNone(stale["native_plan"]["dialogue"]["selected_run"])
        self.assertEqual(stale["native_plan"]["dialogue"]["historical_selection"]["selected_run"], "baseline")
        self.assertEqual(self.files(), before)

    def test_tampered_original_measurement_and_cas_do_not_become_current_advice(self):
        self.fixture.initialize()
        self.fixture.create()
        self.fixture.execute()
        self.fixture.output("project", "next")
        pin = self.pin()
        view = adapter.read_view(self.store, expected_snapshot_sha256=pin)
        self.assertTrue(view["native_report"]["coverage"]["receipts"])
        original = view["native_report"]["evidence_files"][0]
        (self.store.root / original["path"]).write_bytes(b"changed original")
        stale = adapter.read_view(self.store, expected_snapshot_sha256=pin)
        self.assertEqual(stale["status"], "STALE_OR_UNAVAILABLE")
        self.assertIsNone(stale["native_report"])
        report = stale["native_plan"]["dialogue"]["source_report"]
        Path(report["path"]).write_text('{"selected_run":"invented"}', encoding="utf-8")
        damaged = adapter.read_view(self.store, expected_snapshot_sha256=pin)
        self.assertEqual(damaged["status"], "STALE_OR_UNAVAILABLE")
        self.assertIn("integrity failure", damaged["native_plan"]["dialogue"]["reason"])

    def test_stale_pin_and_changed_read_reject_with_original_diagnostic(self):
        self.fixture.initialize()
        self.fixture.output("project", "next")
        with self.assertRaises(ValueError) as wrong:
            adapter.read_view(self.store, expected_snapshot_sha256="0" * 64)
        self.assertEqual(wrong.exception.args[1]["mode"], "EXISTING_PROJECT")
        from rds_dialogue import _report
        calls = []
        def changed(store, ref):
            result = _report(store, ref)
            calls.append(ref)
            if len(calls) == 2:
                self.fixture.steer(self.fixture.request("pause", ident="changed-during-expansion"))
            return result
        pin = self.pin()
        with patch("rds_dialogue._report", side_effect=changed), self.assertRaises(ValueError) as raced:
            adapter.read_view(self.store, expected_snapshot_sha256=pin)
        self.assertIn("changed during expansion", str(raced.exception.args[0]))
        self.assertEqual(raced.exception.args[1]["snapshot_sha256"], pin)
