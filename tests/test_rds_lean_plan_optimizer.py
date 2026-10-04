"""Native-kernel integration tests for the reusable proof-plan optimizer.

CI runs this file in the dedicated Lean job via the ``test_rds_lean*.py`` pattern.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_lean_verify as lean
import rds_verify as engine


def native_lean_available():
    try:
        lean._executable()
        return True
    except (lean.NoNativeLean, ValueError, OSError):
        return False


def plan(steps):
    return {"version": 1, "artifacts": [], "steps": steps}


@unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof execution")
class ProofPlanOptimizerNativeTests(unittest.TestCase):
    def test_cost_ordered_plan_generates_and_replays_checked_leaf(self):
        native = {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                  "left": "2", "right": "2"}
        exact = {"schema": 1, "kind": "affine_fixed_point",
                 "model": {"matrix": [["0"]], "bias": ["0"]}, "point": ["0"]}
        declaration = plan([
            {"name": "native_first", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": native},
            {"name": "exact_gate", "required_assurance": "CERTIFICATE_CHECKED",
             "statement": exact},
        ])
        report = engine.optimize_proof_plan(declaration)
        self.assertEqual(report["status"], "READY", report)
        self.assertEqual([item["name"] for item in report["optimization"]["schedule"]],
                         ["exact_gate", "native_first"])
        with mock.patch.object(lean, "verify", wraps=lean.verify) as generate:
            answer = engine.execute_proof_plan(declaration)
        self.assertEqual(answer["status"], "PASS", answer)
        self.assertEqual(answer["certificate"]["version"], engine.PLAN_CERTIFICATE_VERSION)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual([item["name"] for item in answer["certificate"]["steps"]],
                         ["exact_gate", "native_first"])
        self.assertTrue(engine.replay_proof_plan(declaration, answer["certificate"]))

    def test_normalized_duplicate_proof_uses_one_kernel_leaf_and_budget_is_pre_run(self):
        declaration = plan([
            {"name": "first", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                           "left": "1/2", "right": "2/4"}},
            {"name": "equivalent", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                           "left": "2/4", "right": "1/2"}},
        ])
        report = engine.optimize_proof_plan(declaration)
        self.assertEqual(report["optimization"]["reused_steps"], 1)
        self.assertEqual(report["optimization"]["estimated_work_units"], 1)
        self.assertEqual(report["optimization"]["unoptimized_work_units"], 2)
        with mock.patch.object(lean, "verify", wraps=lean.verify) as generate:
            over_budget = engine.execute_proof_plan(declaration, max_work_units=0)
            self.assertEqual(over_budget["status"], "UNKNOWN")
            self.assertEqual(generate.call_count, 0)
            answer = engine.execute_proof_plan(declaration, max_work_units=1)
        self.assertEqual(answer["status"], "PASS", answer)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(answer["certificate"]["steps"][1]["reuses"], "first")
        self.assertTrue(engine.replay_proof_plan(declaration, answer["certificate"]))

    def test_shared_goal_reuses_proof_across_dependency_paths(self):
        shared = {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                  "left": "5/7", "right": "10/14"}
        gate = {"schema": 1, "kind": "affine_fixed_point",
                "model": {"matrix": [["0"]], "bias": ["0"]}, "point": ["0"]}
        declaration = plan([
            {"name": "dependent_copy", "required_assurance": "LEAN_KERNEL_CHECKED",
             "depends_on": ["gate"], "statement": shared},
            {"name": "independent_copy", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": {**shared, "left": "10/14", "right": "5/7"}},
            {"name": "gate", "required_assurance": "CERTIFICATE_CHECKED",
             "statement": gate},
        ])
        report = engine.optimize_proof_plan(declaration)
        schedule = report["optimization"]["schedule"]
        self.assertEqual([item["name"] for item in schedule],
                         ["gate", "dependent_copy", "independent_copy"])
        self.assertEqual(schedule[2]["reuses"], "dependent_copy")
        with mock.patch.object(lean, "verify", wraps=lean.verify) as generate:
            answer = engine.execute_proof_plan(declaration)
        self.assertEqual(answer["status"], "PASS", answer)
        self.assertEqual(generate.call_count, 1)
        self.assertTrue(engine.replay_proof_plan(declaration, answer["certificate"]))


if __name__ == "__main__":
    unittest.main()
