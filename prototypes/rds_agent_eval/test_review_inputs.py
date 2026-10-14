"""Pinned synthetic terminal replies plus actual native receipts; no paid calls."""
import hashlib
import json
import unittest
from unittest.mock import patch

from prototypes.rds_agent_eval import test_feedback_view as fixtures, review_inputs as adapter
from rds_project import file_sha
from rds_steering import plan


class ReviewInputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FeedbackViewTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.final = json.dumps({"answer": "bin-level claim", "evidence": {"n": 4},
                                 "limitations": ["uneven bins"], "extra": {"retain": True}})

    def write_sources(self, mode="FUNCTION", status="AGENT_FINISHED"):
        choice = {"finish_reason": "stop", "message": {"role": "assistant", "content": self.final}}
        if mode == "FUNCTION":
            choice = {"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [
                {"id": "final", "function": {"name": "finish", "arguments": json.dumps({"answer": self.final})}}]}}
        values = {"request": {"model": "requested", "max_tokens": 3000},
                  "response": {"model": "resolved", "choices": [choice], "usage": {"total_tokens": 31}},
                  "summary": {"calls": 1, "status": status, "final_answer": self.final,
                              "completion_mode": mode, "paid_cost": "UNKNOWN"},
                  "task": {"query": "Synthetic fixture only"}}
        self.pins = {"turn": 0, "requested_model": "requested", "resolved_model": "resolved",
                     "completion_protocol": "finish-function-or-stop-text-v2"}
        for name, value in values.items():
            path = "task.json" if name == "task" else "audit/summary.json" if name == "summary" else f"audit/{name}-0.json"
            file = self.store.root / path
            file.parent.mkdir(exist_ok=True)
            file.write_text(json.dumps(value), encoding="utf-8")
            self.pins[name] = {"path": path, "sha256": file_sha(file)}

    def assemble(self):
        return adapter.prepare_review(self.store,
            expected_snapshot_sha256=plan(self.store, dialogue=True)["snapshot_sha256"],
            outputs=self.fixture.outputs, answer_pins=self.pins)

    def test_native_complete_function_and_common_text_preserve_entire_answer_and_originals(self):
        self.fixture.prepare()
        for mode in ("FUNCTION", "TEXT"):
            with self.subTest(mode=mode):
                self.write_sources(mode)
                before = file_sha(self.store.path)
                with patch.object(self.store, "execute", side_effect=AssertionError("Dispatched")):
                    result = self.assemble()
                answer = result["answer"]
                self.assertEqual(answer["final_text"], self.final)
                self.assertEqual(answer["final_json"], json.loads(self.final))
                self.assertEqual(answer["completion_mode"], mode)
                self.assertEqual(len(answer["original_files"]), 4)
                self.assertEqual(result["free_text_review"], "UNKNOWN")
                self.assertEqual(result["semantic_rubric"], "NOT_FROZEN")
                self.assertEqual(file_sha(self.store.path), before)

    def test_tampered_pin_cross_attempt_and_mismatched_summary_reject_without_repair(self):
        self.fixture.prepare()
        self.write_sources()
        for name in ("request", "response", "summary", "task"):
            with self.subTest(name=name):
                changed = {**self.pins, name: {**self.pins[name], "sha256": "0" * 64}}
                with self.assertRaises(ValueError):
                    adapter.read_answer(self.store.root, pins=changed)
        with self.assertRaises(ValueError):
            adapter.read_answer(self.store.root, pins={**self.pins, "turn": 1})
        path = self.store.root / "audit/summary.json"
        value = json.loads(path.read_bytes())
        value["final_answer"] = "different retained answer"
        path.write_text(json.dumps(value), encoding="utf-8")
        self.pins["summary"]["sha256"] = file_sha(path)
        with self.assertRaises(ValueError) as mismatch:
            self.assemble()
        self.assertIn("original files", mismatch.exception.args[0])
        self.assertIn("summary", mismatch.exception.args[1])

    def test_failed_native_and_unknown_summary_keep_costs_and_no_verified_final(self):
        self.fixture.prepare(fail=True)
        self.write_sources(status="UNKNOWN_NO_RETRY")
        result = self.assemble()
        self.assertEqual(result["answer"]["final_source_status"], "NO_VERIFIED_FINAL")
        self.assertIsNone(result["answer"]["final_text"])
        self.assertEqual(result["answer"]["original_files"]["summary"]["value"]["paid_cost"], "UNKNOWN")
        entry = result["native_evidence"]["model_feedback"]["observations"][0]
        self.assertEqual(entry["run_status"], "FAILED")
        self.assertTrue(entry["errors"])
        self.assertTrue(entry["resources"]["cpu_seconds"]["unknown"])

    def test_truncated_final_and_native_change_reject(self):
        self.fixture.prepare()
        self.write_sources()
        path = self.store.root / "audit/response-0.json"
        original = path.read_bytes()
        value = json.loads(original)
        value["choices"][0]["finish_reason"] = "length"
        path.write_text(json.dumps(value), encoding="utf-8")
        self.pins["response"]["sha256"] = file_sha(path)
        with self.assertRaises(ValueError):
            self.assemble()
        path.write_bytes(original)
        self.pins["response"]["sha256"] = hashlib.sha256(original).hexdigest()
        reader = adapter.read_answer
        def changed(*args, **kwargs):
            result = reader(*args, **kwargs)
            (self.store.root / "config.json").write_bytes(b'{"changed":true}')
            return result
        before = file_sha(self.store.path)
        with patch.object(adapter, "read_answer", side_effect=changed), self.assertRaises(ValueError):
            self.assemble()
        self.assertEqual(file_sha(self.store.path), before)
