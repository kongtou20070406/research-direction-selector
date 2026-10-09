"""Domain-neutral scheduling and exact subgoal reuse for trusted proof plans."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_lean_verify as lean
import rds_verify as engine


def plan(steps):
    return {"version": 1, "artifacts": [], "steps": steps}


class ProofPlanOptimizerTests(unittest.TestCase):
    def test_registered_costs_schedule_exact_discriminators_before_native_goals(self):
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
        self.assertEqual(report["optimization"]["estimate_semantics"],
                         "registered adapter operation proxies; not elapsed-time or memory predictions")

    def test_failed_exact_discriminator_stops_before_native_kernel_work(self):
        declaration = plan([
            {"name": "expensive", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                           "left": "1", "right": "1"}},
            {"name": "reject_candidate", "required_assurance": "CERTIFICATE_CHECKED",
             "statement": {"schema": 1, "kind": "affine_fixed_point",
                           "model": {"matrix": [["0"]], "bias": ["1"]},
                           "point": ["0"]}},
        ])
        with mock.patch.object(lean, "verify", wraps=lean.verify) as generate:
            answer = engine.execute_proof_plan(declaration)
        self.assertEqual(answer["status"], "UNKNOWN")
        self.assertEqual(generate.call_count, 0)

    def test_equivalent_rational_spellings_share_a_native_subgoal(self):
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
        over_budget = engine.optimize_proof_plan(declaration, max_work_units=0)
        self.assertEqual(over_budget["status"], "UNKNOWN")
        self.assertEqual(over_budget["optimization"]["budget"],
                         {"max_work_units": 0, "estimated_work_units": 1})

    def test_shared_goal_reuses_evidence_across_dependency_paths(self):
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

    def test_dependencies_override_cost_order_and_invalid_dags_are_unknown(self):
        native = {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                  "left": "3", "right": "3"}
        exact = {"schema": 1, "kind": "affine_fixed_point",
                 "model": {"matrix": [["0"]], "bias": ["0"]}, "point": ["0"]}
        dependent = plan([
            {"name": "exact_after_native", "required_assurance": "CERTIFICATE_CHECKED",
             "depends_on": ["native_prerequisite"], "statement": exact},
            {"name": "native_prerequisite", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": native},
        ])
        report = engine.optimize_proof_plan(dependent)
        self.assertEqual([item["name"] for item in report["optimization"]["schedule"]],
                         ["native_prerequisite", "exact_after_native"])
        cycle = plan([
            {"name": "a", "required_assurance": "CERTIFICATE_CHECKED",
             "depends_on": ["b"], "statement": exact},
            {"name": "b", "required_assurance": "CERTIFICATE_CHECKED",
             "depends_on": ["a"], "statement": exact},
        ])
        self.assertEqual(engine.optimize_proof_plan(cycle)["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
