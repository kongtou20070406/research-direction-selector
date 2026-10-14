"""Actual native FULL fixtures and synthetic original terminal replies; no paid calls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from prototypes.rds_agent_eval import test_review_inputs as fixtures, test_survey_domain as domain_fixture
from prototypes.rds_agent_eval import domain_bridge as bridge
from rds_project import file_sha
from rds_steering import plan


class DomainBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReviewInputTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.study = self.fixture.fixture.fixture
        self.store = self.fixture.store
        self.native = self.study.fixture
        sample = domain_fixture.SurveyDomainTests("runTest")
        sample.setUp()
        self.native.write("code.py", Path(__file__).with_name("survey_methods.py").read_bytes())
        self.native.write("evaluator.json", Path(__file__).with_name("survey_verifier.py").read_bytes())
        self.native.write("data.csv", sample.source)
        for route, method in zip(self.study.recipe["routes"], ("role_inventory", "quoted_fractions")):
            route["run"]["argv"][-2] = method
        self.study.recipe["routes"][1]["preconditions"][0]["value"] = "role_inventory"
        self.study.recipe["context"]["decision"]["goal_conditions"][0]["value"] = "quoted_fractions"
        self.study.declaration["model"] = self.study.authorization["model"] = "requested"
        self.study.declaration["response_model"] = self.study.authorization["response_model"] = "resolved"
        self.fixture.final = json.dumps({"answer": "Scoped coded fractions, no bootstrap replication.",
            "top_roles": ["Project_Lead", "Data_Scientist"],
            "proportion_basis": "QUOTED_OVER_QUOTED_PLUS_NOT_QUOTED", "bootstrap_ci": None,
            "limitations": ["Unknown source filtering/bootstrap"], "extra": {"retain": True}})

    def prepare(self, fail=False):
        if fail:
            self.study.recipe["routes"][0]["run"]["argv"][-2] = "invalid-method"
        self.fixture.write_sources(status="UNKNOWN_NO_RETRY" if fail else "AGENT_FINISHED")
        self.study.initialize()
        result = self.native.call("project", "advance", ok=not fail)
        if fail:
            self.assertEqual(result.returncode, 1)
        else:
            self.native.call("project", "advance")
        self.native.call("project", "next")
        self.outputs = [{"run_id": r["run_id"], "receipt_sha256": r["sha256"],
                         "path": "outputs/" + r["run_id"] + ".json"} for r in self.store.snapshot()["receipts"]]

    def inspect(self, **changes):
        args = {"expected_snapshot_sha256": plan(self.store, dialogue=True)["snapshot_sha256"],
                "outputs": self.outputs, "source_path": "data.csv", "answer_pins": self.fixture.pins}
        return bridge.inspect(self.store, self.study.prepared, **{**args, **changes})

    def test_two_native_original_receipts_full_reply_and_separate_statuses_without_writes(self):
        self.prepare()
        before = file_sha(self.store.path)
        with patch.object(self.store, "execute", side_effect=AssertionError("Dispatched")):
            result = self.inspect()
        self.assertEqual(len(result["domain_outputs"]), 2)
        self.assertTrue(all(r["measurement_correctness"] == r["structured_claim_correctness"] == "PASS" for r in result["domain_outputs"]))
        self.assertTrue(all(r["task_correctness"] == "UNKNOWN" for r in result["domain_outputs"]))
        self.assertEqual(result["final"]["full_final"], json.loads(self.fixture.final))
        self.assertEqual(result["scientific_support"], "UNKNOWN")
        self.assertEqual(file_sha(self.store.path), before)

    def test_failed_native_and_unknown_final_keep_original_costs_without_verification(self):
        self.prepare(fail=True)
        before = file_sha(self.store.path)
        with patch.object(bridge.survey_verifier, "verify", side_effect=AssertionError("Negative verified")):
            result = self.inspect()
        row = result["domain_outputs"][0]
        self.assertEqual(row["run_status"], "FAILED")
        self.assertIsNone(row["verification"])
        original = result["original_review_inputs"]["native_evidence"]["original_observations"]["observations"][0]
        self.assertTrue(original["receipt"]["errors"])
        self.assertTrue(original["receipt"]["resources"]["cpu_seconds"]["unknown"])
        self.assertEqual(file_sha(self.store.path), before)

    def test_contract_implementation_output_and_final_fields_fail_closed(self):
        self.prepare()
        prepared = deepcopy(self.study.prepared)
        self.study.prepared["contract"]["schema"] = "different-preflight"
        with self.assertRaises(ValueError):
            self.inspect()
        self.study.prepared = prepared
        with self.assertRaises(ValueError):
            self.inspect(outputs=[{**self.outputs[0], "path": "outputs/wrong.json"}])
        with self.assertRaises(ValueError):
            bridge.parse_final('{"answer":"missing declared fields"}')
        self.native.write("evaluator.json", b"different verifier")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_native_change_after_independent_verification_rejects_mixed_projection(self):
        self.prepare()
        original = bridge.survey_verifier.verify
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.native.write("outputs/baseline.json", b"changed after reading")
            return result
        before = file_sha(self.store.path)
        with patch.object(bridge.survey_verifier, "verify", side_effect=changed), self.assertRaises(ValueError):
            self.inspect()
        self.assertEqual(file_sha(self.store.path), before)
