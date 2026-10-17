"""Focused coverage for the native checkpoint decision kernel boundary."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rds_advisor
import rds_checkpoint_decision as native
from rds_checkpoints import read_checkpoint, save_checkpoint


class CheckpointDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".rds" / "cas").mkdir(parents=True)
        (self.root / ".rds" / "project.sqlite3").touch()

    @staticmethod
    def decision(**changes):
        value = {
            "question_id": "q-route",
            "goal_revision": "goal-1",
            "scope": {"dataset": "dev"},
            "candidate": {"action": {"description": "fit model", "target": "model",
                                       "parameters": {"rate": 2}}},
            "outcome": "rejected",
            "evidence": {"metric": {"value": 0.2}},
        }
        value.update(changes)
        return value

    def test_advisor_reexports_identical_original_api_and_constants(self):
        for name in ("_checkpoint_route", "validate_checkpoint_decision", "_loop_route",
                     "_loop_family", "_loop_context", "_json", "_scope", "_text", "_require",
                     "MAX_LOOP_BYTES", "CHECKPOINT_ROUTE_FIELDS"):
            with self.subTest(name=name):
                self.assertIs(getattr(rds_advisor, name), getattr(native, name))

    def test_valid_route_and_bounded_invalid_fields(self):
        valid = self.decision(outcome="accepted")
        native.validate_checkpoint_decision(valid, "cp-valid", self.root / ".rds")
        self.assertIsNotNone(native._loop_route(valid["candidate"]))
        self.assertIsNotNone(native._loop_family(valid["candidate"]))
        self.assertEqual(native._loop_context(valid["candidate"], valid["evidence"]),
                         native._json({"facts": {}, "conditions": [], "observables": [],
                                       "outcomes": [], "stop": []}))

        invalids = (
            self.decision(candidate={"action": {"parameters": {"rate": 2}}}),
            self.decision(scope={f"k{i}": i for i in range(17)}),
            self.decision(evidence={"large": "x" * (native.MAX_LOOP_BYTES + 1)}),
        )
        for index, invalid in enumerate(invalids):
            with self.subTest(index=index), self.assertRaises(ValueError):
                native.validate_checkpoint_decision(invalid, f"cp-invalid-{index}", self.root / ".rds")

    def test_rejected_domain_accepts_intact_cas_witness(self):
        witness = self.root / ".rds" / "cas" / "witness.json"
        raw = b'{"metric":0.1}\n'
        witness.write_bytes(raw)
        value = self.decision(rejected_domain={"parameters": {"rate": {"min": 1, "max": 3}},
                                               "justification": "bounded tested range"},
                              falsification={"path": str(witness),
                                             "sha256": hashlib.sha256(raw).hexdigest()})
        native.validate_checkpoint_decision(value, "cp-cas", self.root / ".rds")

    def test_rejected_domain_refuses_tampered_and_missing_cas_witness(self):
        witness = self.root / ".rds" / "cas" / "witness.json"
        raw = b'{"metric":0.1}\n'
        witness.write_bytes(raw)
        base = self.decision(rejected_domain={"parameters": {"rate": {"eq": 2}},
                                              "justification": "single point"},
                             falsification={"path": str(witness),
                                            "sha256": hashlib.sha256(raw).hexdigest()})
        witness.write_bytes(b'{"metric":0.9}\n')
        with self.assertRaisesRegex(ValueError, "integrity"):
            native.validate_checkpoint_decision(base, "cp-tampered", self.root / ".rds")
        witness.unlink()
        with self.assertRaises((ValueError, OSError)):
            native.validate_checkpoint_decision(base, "cp-missing", self.root / ".rds")

    def test_real_checkpoint_save_and_read_use_kernel_and_preserve_budget(self):
        snapshot = {"contract": {"project_id": "study"},
                    "budget": {"wall_seconds": {"cap": 30, "reserved": 8,
                                                   "spent_measured": 7, "remaining": 15}},
                    "exposures": [{"split": "dev"}], "runs": {}}
        decision = self.decision(outcome="accepted")
        with patch.object(rds_advisor, "validate_checkpoint_decision",
                          side_effect=AssertionError("Advisor callback invoked")):
            saved = save_checkpoint(self.root, "cp-real", snapshot, kind="project", decision=decision)
            import sqlite3
            db = sqlite3.connect(self.root / ".rds" / "project.sqlite3")
            try:
                retained = read_checkpoint(db, "cp-real", root=self.root)
            finally:
                db.close()
        self.assertEqual(saved["status"], "SAVED")
        self.assertEqual(retained["record"]["snapshot"]["budget"], snapshot["budget"])
        self.assertEqual(retained["record"]["decision"], decision)

    def test_cold_native_import_does_not_import_advisor(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SCRIPTS)
        code = ("import sys; import rds_checkpoint_decision; "
                "assert 'rds_advisor' not in sys.modules, sorted(sys.modules)")
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code],
                                cwd=self.root, env=env, capture_output=True, text=True,
                                encoding="utf-8", timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
