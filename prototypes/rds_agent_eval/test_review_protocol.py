"""Prospective protocol binding checks; fixtures do not judge agent answers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from prototypes.rds_agent_eval.review_protocol import freeze_protocol, CRITERIA


class ReviewProtocolTests(unittest.TestCase):
    def setUp(self):
        self.raw = Path(__file__).with_name("semantic_rubric.json").read_bytes()
        self.spec = json.loads(self.raw)
        self.old = ["introduction_pathways_non-native_plants-metadata0-q0"]
        self.new = "requirements_engineering_for_ML_enabled_systems-metadata0-q0"

    def freeze(self, spec=None, **kwargs):
        raw = self.raw if spec is None else json.dumps(spec).encode()
        args = {"expected_sha256": hashlib.sha256(raw).hexdigest(), "task_id": self.new,
                "used_task_ids": self.old}
        return freeze_protocol(raw, **{**args, **kwargs})

    def test_new_task_freezes_complete_criteria_without_inventing_reviewer_or_truth(self):
        result = self.freeze()
        self.assertEqual(set(result["protocol"]["criteria"]), CRITERIA)
        self.assertEqual(result["raw_rubric_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(result["protocol"], self.spec)
        self.assertEqual(result["protocol"]["reviewer"]["identity"], "UNKNOWN")
        self.assertEqual(result["status"], "FROZEN_PROTOCOL_ONLY")
        self.assertEqual(result["study_readiness"], "UNKNOWN")
        self.assertEqual(result["scientific_support"], "UNKNOWN")
        self.assertEqual(result["assessment_effect"], "NONE")

    def test_wrong_raw_pin_missing_evidence_or_criterion_reject(self):
        with self.assertRaises(ValueError):
            self.freeze(expected_sha256="0" * 64)
        for changed in ("criteria", "required_evidence"):
            with self.subTest(changed=changed):
                spec = deepcopy(self.spec)
                if changed == "criteria":
                    spec[changed].pop("uncertainty_consistency")
                else:
                    spec[changed].remove("failed_unknown_attempts_and_costs")
                with self.assertRaises(ValueError):
                    self.freeze(spec)

    def test_unknown_implementation_pin_and_undeclared_identity_reject(self):
        for name in ("reviewer", "verifier"):
            with self.subTest(name=name):
                spec = deepcopy(self.spec)
                spec[name]["source_sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    self.freeze(spec)
                spec[name] = {"identity": "declared-fixture", "source_sha256": None}
                with self.assertRaises(ValueError):
                    self.freeze(spec)
        declared = deepcopy(self.spec)
        declared["reviewer"] = {"identity": "fixture", "source_sha256": "1" * 64}
        result = self.freeze(declared)
        self.assertEqual(result["identity_assurance"], "DECLARATION_ONLY")
        self.assertEqual(result["independence"], "UNKNOWN")

    def test_used_task_duplicate_inventory_empty_rule_and_execution_field_reject(self):
        for change in ({"task_id": self.old[0]}, {"used_task_ids": self.old * 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.freeze(**change)
        spec = deepcopy(self.spec)
        spec["criteria"]["claim_scope"]["pass_rule"] = ""
        with self.assertRaises(ValueError):
            self.freeze(spec)
        spec = {**self.spec, "execute": "invented approval"}
        with self.assertRaises(ValueError):
            self.freeze(spec)
