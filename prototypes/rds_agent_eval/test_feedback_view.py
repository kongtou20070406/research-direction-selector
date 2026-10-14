"""Focused real native fixtures; no paid requests or production wiring."""
import json
from unittest.mock import patch
import unittest

from prototypes.rds_agent_eval import test_study_bridge as fixtures, feedback_view as adapter
from rds_project import file_sha
from rds_steering import plan


class FeedbackViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StudyBridgeTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store

    def prepare(self, fail=False):
        if fail:
            self.fixture.recipe["routes"][0]["run"]["argv"][-2] = "invalid-method"
        self.fixture.initialize()
        result = self.fixture.fixture.call("project", "advance", ok=not fail)
        if fail:
            self.assertEqual(result.returncode, 1)
        self.fixture.fixture.call("project", "next")
        receipt = self.store.snapshot()["receipts"][0]
        self.outputs = [{"run_id": receipt["run_id"], "receipt_sha256": receipt["sha256"],
                         "path": "outputs/baseline.json"}]

    def read(self, outputs=None):
        return adapter.read_feedback(self.store,
            expected_snapshot_sha256=plan(self.store, dialogue=True)["snapshot_sha256"],
            outputs=self.outputs if outputs is None else outputs)

    def test_success_keeps_full_originals_and_current_native_selection_without_writes(self):
        self.prepare()
        before = file_sha(self.store.path)
        with patch.object(self.store, "execute", side_effect=AssertionError("Dispatched")), \
             patch("rds_owned_advisor.review", side_effect=AssertionError("Collected")):
            result = self.read()
        compact = result["model_feedback"]
        raw = result["original_observations"]["observations"][0]
        entry = compact["observations"][0]
        self.assertEqual(entry["resources"], raw["receipt"]["resources"])
        self.assertEqual(len(entry["artifacts"]), len(raw["artifacts"]))
        output = next(a for a in raw["artifacts"] if a["kind"] == "project_output")
        self.assertEqual(entry["measurement"]["value"], json.loads(output["bytes"]))
        self.assertEqual(entry["measurement"]["artifact"]["sha256"], file_sha(self.store.root / output["path"]))
        self.assertEqual(compact["advisor"]["dialogue"]["selected_run"], "repair")
        self.assertEqual(compact["advisor"]["selected_manifest"], result["original_advisor"]["native_report"]["selected_manifest"])
        self.assertEqual(compact["scientific_support"], "UNKNOWN")
        self.assertEqual(file_sha(self.store.path), before)
        self.assertNotIn("native_report", compact["advisor"])

    def test_failed_original_and_unknown_charged_costs_remain_in_denominator(self):
        self.prepare(fail=True)
        before = file_sha(self.store.path)
        result = self.read()
        entry = result["model_feedback"]["observations"][0]
        original = result["original_observations"]["observations"][0]
        self.assertEqual(entry["run_status"], "FAILED")
        self.assertEqual(entry["errors"], original["receipt"]["errors"])
        self.assertTrue(entry["errors"])
        self.assertEqual(entry["resources"], original["receipt"]["resources"])
        self.assertTrue(entry["resources"]["cpu_seconds"]["unknown"])
        self.assertIsNone(entry["measurement"])
        self.assertTrue(next(a for a in original["artifacts"] if a["kind"] == "stderr.bin")["bytes"])
        self.assertEqual(file_sha(self.store.path), before)

    def test_wrong_receipt_cross_route_output_and_tampered_bytes_reject(self):
        self.prepare()
        for change in ({"receipt_sha256": "0" * 64}, {"path": "outputs/repair.json"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.read([{**self.outputs[0], **change}])
        (self.store.root / "outputs/baseline.json").write_bytes(b"tampered")
        before = file_sha(self.store.path)
        with self.assertRaises(ValueError):
            self.read()
        self.assertEqual(file_sha(self.store.path), before)

    def test_stale_and_changed_advice_preserve_unknown_or_reject_mixed_view(self):
        self.prepare()
        config = self.store.root / "config.json"
        original = config.read_bytes()
        config.write_bytes(b'{"changed":true}')
        stale = self.read()["model_feedback"]["advisor"]
        self.assertEqual(stale["dialogue"]["status"], "STALE_OR_UNAVAILABLE")
        self.assertIsNone(stale["dialogue"]["selected_run"])
        self.assertIsNone(stale["selected_manifest"])
        config.write_bytes(original)
        read_originals = adapter.read_observations
        def changed(*args):
            result = read_originals(*args)
            config.write_bytes(b'{"changed-during-projection":true}')
            return result
        before = file_sha(self.store.path)
        with patch.object(adapter, "read_observations", side_effect=changed), self.assertRaises(ValueError):
            self.read()
        self.assertEqual(file_sha(self.store.path), before)
