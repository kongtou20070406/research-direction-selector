"""Exact candidate-to-Lean proof-chain regressions for affine fixed points."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_affine_toolchain as affine
import rds_lean_verify as lean
import rds_verify as engine
from rds_proof_cache import ProofCache


def native_lean_available():
    try:
        lean._executable()
        return True
    except (lean.NoNativeLean, ValueError, OSError):
        return False


def example():
    return json.loads((ROOT / "examples/formal/affine_fixed_point_synthesis.json").read_text(encoding="utf-8"))


class AffineProofChainTests(unittest.TestCase):
    def test_inconsistent_system_remains_unknown_without_a_refutation_claim(self):
        spec = {"schema": 1, "kind": affine.KIND,
                "model": {"matrix": [["1"]], "bias": ["1"]}}
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "UNKNOWN")
        self.assertIn("nonexistence is not certified", answer["reason"])
        self.assertNotIn("certificate", answer)

    def test_missing_or_mismatched_inputs_are_inconclusive(self):
        for spec in ({"schema": 1, "kind": affine.KIND, "model": {"matrix": [["1"]]}},
                     {"schema": 1, "kind": affine.KIND,
                      "model": {"matrix": [["1", "0"]], "bias": ["0"]}}):
            with self.subTest(spec=spec):
                self.assertEqual(engine.verify(spec)["status"], "UNKNOWN")
        dimension = affine.MAX_DIMENSION + 1
        oversized = {"schema": 1, "kind": affine.KIND,
                     "model": {"matrix": [["0"] * dimension for _ in range(dimension)],
                               "bias": ["0"] * dimension}}
        self.assertEqual(engine.verify(oversized)["status"], "UNKNOWN")
        scalar = {"matrix": [["0"]], "bias": ["0"]}
        too_many = {"schema": 1, "kind": affine.KIND, "composition": [scalar] * 9}
        self.assertEqual(engine.verify(too_many)["status"], "UNKNOWN")

    def test_proof_plan_rejects_incompatible_or_unbound_artifacts(self):
        matrix, bias = affine._read_spec(example())
        plan = affine._proof_plan(matrix, bias, affine._solve(matrix, bias))
        wrong_type = copy.deepcopy(plan)
        wrong_type["artifacts"][0]["type"] = "untyped_json"
        self.assertEqual(engine.execute_proof_plan(wrong_type)["status"], "UNKNOWN")
        bad_index = copy.deepcopy(plan)
        bad_index["steps"][1]["statement"]["relations"][0]["left"]["index"] = 99
        self.assertEqual(engine.execute_proof_plan(bad_index)["status"], "UNKNOWN")
        bad_candidate = affine._proof_plan(matrix, bias, [0, 0])
        self.assertEqual(engine.execute_proof_plan(bad_candidate)["status"], "UNKNOWN")

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_candidate_flows_into_every_generated_coordinate_goal(self):
        spec = example()
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "PASS", answer)
        self.assertEqual(answer["assurance"], affine.ASSURANCE)
        self.assertEqual(answer["candidate"], ["1/5", "2/5"])
        self.assertEqual(answer["artifacts"]["fixed_point"], {
            "type": "exact_rational_vector", "dimension": 2, "value": ["1/5", "2/5"]})
        plan = answer["certificate"]["proof"]["certificate"]["plan"]
        self.assertEqual([item["type"] for item in plan["artifacts"]],
                         ["exact_rational_affine_model", "exact_rational_vector"])
        self.assertEqual(plan["artifacts"][1]["value"], ["1/5", "2/5"])
        subgoals = plan["steps"]
        self.assertEqual([item["name"] for item in subgoals],
                         ["fixed_point_exact", "coordinate_obligations"])
        self.assertEqual(subgoals[0]["rule"], "matrix.fixed_point")
        self.assertEqual(subgoals[0]["required_assurance"], "CERTIFICATE_CHECKED")
        self.assertEqual(subgoals[0]["statement"]["model"], plan["artifacts"][0]["value"])
        self.assertEqual(subgoals[0]["statement"]["point"], ["1/5", "2/5"])
        vector_step = subgoals[1]
        self.assertEqual(vector_step["rule"], "lean.rational_relation")
        self.assertEqual(vector_step["statement"]["kind"], "lean_vector_obligation")
        self.assertEqual(len(vector_step["statement"]["relations"]), 2)
        for coordinate, relation in enumerate(vector_step["statement"]["relations"]):
            self.assertEqual(relation["relation"], "eq")
            self.assertEqual(relation["left"], answer["candidate"][coordinate])
            self.assertEqual(relation["right"], relation["left"])
        self.assertEqual(vector_step["certificate"]["assurance"], "LEAN_KERNEL_CHECKED")
        with mock.patch.object(affine, "_solve", side_effect=AssertionError("replay must not search")):
            self.assertTrue(engine.check_certificate(spec, answer["certificate"]))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_changed_context_candidate_and_child_proofs_are_rejected(self):
        spec = example()
        certificate = engine.verify(spec)["certificate"]
        changed = copy.deepcopy(spec)
        changed["model"]["bias"][0] = "1"
        self.assertFalse(engine.check_certificate(changed, certificate))
        mutations = [
            lambda c: c["proof"]["certificate"].update(candidate=["0", "0"]),
            lambda c: c["proof"]["certificate"]["plan"]["artifacts"][1].update(value=["0", "0"]),
            lambda c: c["proof"]["certificate"]["plan"]["steps"][1].update(rule="matrix.fixed_point"),
            lambda c: c["proof"]["certificate"]["plan"]["steps"][1]["certificate"].update(axioms=["sorryAx"]),
            lambda c: c["proof"]["certificate"]["plan"]["steps"].pop(),
        ]
        for mutate in mutations:
            forged = copy.deepcopy(certificate)
            mutate(forged)
            with self.subTest(mutation=mutate):
                self.assertFalse(engine.check_certificate(spec, forged))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_reuses_the_same_adapter_for_a_second_problem(self):
        first_spec = example()
        first = engine.verify(first_spec)
        second_spec = json.loads((ROOT / "examples/formal/affine_fixed_point_synthesis_scalar.json")
                                 .read_text(encoding="utf-8"))
        second = engine.verify(second_spec)
        self.assertEqual(first["status"], "PASS", first)
        self.assertEqual(second["status"], "PASS", second)
        self.assertEqual(len(first["certificate"]["proof"]["certificate"]["plan"]["steps"]), 2)
        self.assertEqual(len(second["certificate"]["proof"]["certificate"]["plan"]["steps"]), 2)
        self.assertEqual(second["candidate"], ["2/3"])
        self.assertFalse(engine.check_certificate(second_spec, first["certificate"]))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_ordered_affine_transformations_are_composed_and_replayed(self):
        spec = json.loads((ROOT / "examples/formal/affine_composed_fixed_point.json")
                          .read_text(encoding="utf-8"))
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "PASS", answer)
        self.assertEqual(answer["candidate"], ["1", "1"])
        plan = answer["certificate"]["proof"]["certificate"]["plan"]
        self.assertEqual([step["name"] for step in plan["steps"]],
                         ["compose_affine_chain", "fixed_point_exact", "coordinate_obligations"])
        self.assertEqual(plan["steps"][0]["rule"], affine.COMPOSITION_RULE)
        self.assertEqual(plan["steps"][1]["rule"], "matrix.fixed_point")
        self.assertEqual(plan["steps"][2]["rule"], "lean.rational_relation")
        self.assertTrue(engine.check_certificate(spec, answer["certificate"]))
        forged = copy.deepcopy(answer["certificate"])
        forged["proof"]["certificate"]["plan"]["steps"][0]["certificate"]["composed_model"]["bias"][0] = "99"
        self.assertFalse(engine.check_certificate(spec, forged))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_repeated_plan_subgoals_reuse_one_checked_certificate(self):
        obligation = {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                      "left": "7/9", "right": "7/9"}
        plan = {"version": 1, "artifacts": [], "steps": [
            {"name": "first", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": copy.deepcopy(obligation)},
            {"name": "second", "required_assurance": "LEAN_KERNEL_CHECKED",
             "statement": copy.deepcopy(obligation)},
        ]}
        with mock.patch.object(lean, "verify", wraps=lean.verify) as generate:
            answer = engine.execute_proof_plan(plan)
        self.assertEqual(answer["status"], "PASS", answer)
        self.assertEqual(generate.call_count, 1)
        steps = answer["certificate"]["steps"]
        self.assertIn("certificate", steps[0])
        self.assertEqual(steps[1]["reuses"], "first")
        self.assertTrue(engine.replay_proof_plan(plan, answer["certificate"]))
        forged = copy.deepcopy(answer["certificate"])
        forged["steps"][1]["reuses"] = "missing"
        self.assertFalse(engine.replay_proof_plan(plan, forged))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_dense_affine_chains_scale_through_dimension_32(self):
        measurements = []
        for dimension in (2, 8, 16, 32):
            first_scale = "1/" + str(4 * dimension)
            second_scale = "1/" + str(8 * dimension)
            first_bias = [str(affine.Fraction(index + 1, 5 * (dimension + 1)))
                          for index in range(dimension)]
            second_bias = [str(affine.Fraction(dimension - index, 7 * (dimension + 1)))
                           for index in range(dimension)]
            spec = {"schema": 1, "kind": affine.KIND, "composition": [
                {"matrix": [[first_scale] * dimension for _ in range(dimension)],
                 "bias": first_bias},
                {"matrix": [[second_scale] * dimension for _ in range(dimension)],
                 "bias": second_bias},
            ]}
            tracemalloc.start()
            started = time.perf_counter()
            answer = engine.verify(spec)
            elapsed = time.perf_counter() - started
            _current, peak_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(answer["status"], "PASS", answer)
            self.assertEqual(answer["certificate"]["proof"]["certificate"]["dimension"], dimension)
            candidate = [affine.Fraction(value) for value in answer["candidate"]]

            # Independent closed-form check for A1=J/(4n), A2=J/(8n).
            sum_first = sum((affine.Fraction(value) for value in first_bias), affine.Fraction(0))
            sum_second = sum((affine.Fraction(value) for value in second_bias), affine.Fraction(0))
            coordinate_sum = affine.Fraction(32, 31) * (
                sum_second + affine.Fraction(1, 8) * sum_first)
            expected = [affine.Fraction(second_bias[i]) +
                        affine.Fraction(1, 8 * dimension) * sum_first +
                        affine.Fraction(1, 32 * dimension) * coordinate_sum
                        for i in range(dimension)]
            self.assertEqual(candidate, expected)
            cert_bytes = len(engine.canonical(answer["certificate"]).encode("utf-8"))
            self.assertLess(peak_bytes, 64 * 1024 * 1024)
            self.assertLess(cert_bytes, engine.MAX_CERTIFICATE_BYTES)
            self.assertLess(elapsed, 60.0)
            measurements.append((dimension, elapsed, peak_bytes, cert_bytes))
        for dimension, elapsed, peak_bytes, cert_bytes in measurements:
            print("AFFINE_SCALE dimension={} wall_seconds={:.3f} python_peak_bytes={} certificate_bytes={} lean_memory_cap_mib=512".format(
                dimension, elapsed, peak_bytes, cert_bytes))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_named_typed_result_is_reused_by_multiple_module_nodes(self):
        spec = {"schema": 1, "kind": "theorem_module", "definitions": {"claim": example()},
                "theorems": [
                    {"name": "fixed", "statement": {"$ref": "claim"},
                     "by": {"rule": affine.RULE}},
                    {"name": "use_a", "statement": {"kind": "all", "of": ["fixed"]},
                     "by": {"rule": "logic.and_intro", "premises": ["fixed"]}},
                    {"name": "use_b", "statement": {"kind": "all", "of": ["fixed"]},
                     "by": {"rule": "logic.and_intro", "premises": ["fixed"]}},
                ]}
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "PASS", answer)
        self.assertEqual(answer["theorems"]["fixed"]["assurance"], affine.ASSURANCE)
        self.assertEqual(answer["theorems"]["fixed"]["artifacts"]["fixed_point"]["value"], ["1/5", "2/5"])
        self.assertEqual(answer["theorems"]["use_a"]["status"], "PASS")
        self.assertEqual(answer["theorems"]["use_b"]["status"], "PASS")

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_cache_replays_hits_and_new_context_gets_a_new_entry(self):
        spec = example()
        with tempfile.TemporaryDirectory(prefix="rds-affine-chain-") as folder:
            cache = ProofCache(Path(folder) / "proofs.sqlite3")
            first = cache.verify(spec)
            self.assertTrue(first["cache"]["stored"])
            hit = cache.verify(spec)
            self.assertTrue(hit["cache"]["hit"])
            changed = copy.deepcopy(spec)
            changed["model"]["bias"][0] = "1/10"
            fresh = cache.verify(changed)
            self.assertEqual(fresh["status"], "PASS", fresh)
            self.assertFalse(fresh["cache"]["hit"])


if __name__ == "__main__":
    unittest.main()
