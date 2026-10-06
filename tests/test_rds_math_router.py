"""Exact math routing selects only typed adapters with independently replayable certificates."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_math_router as router


def linear_spec(**updates):
    spec = {
        "schema": router.REQUEST_SCHEMA,
        "kind": "rational_linear_system",
        "domain": "QQ",
        "matrix": [[2, 3], [4, -1], [6, 2]],
        "rhs": [1, 2, 3],
        "claim": "unique_solution",
    }
    spec.update(updates)
    return spec


def roots_spec(**updates):
    spec = {
        "schema": router.REQUEST_SCHEMA,
        "kind": "univariate_qq_polynomial_roots",
        "domain": "QQ",
        # (x - 1)^2 * (x^2 - 2), in ascending coefficient order.
        "coefficients_ascending": [-2, 4, -1, -2, 1],
        "claim": "complete_distinct_real_root_set",
        "max_interval_width": "1/16",
    }
    spec.update(updates)
    return spec


class MathRouterTests(unittest.TestCase):
    def test_registry_reports_only_pinned_registered_adapters_and_is_immutable(self):
        first = router.capabilities()
        first["capabilities"][0]["backend"]["version"] = "forged"
        second = router.capabilities()
        self.assertEqual(second["capabilities"][0]["backend"]["version"], router.SYMPY_VERSION)
        self.assertEqual(second["registered_backends"][0]["required_version"], router.SYMPY_VERSION)
        self.assertEqual({item["id"] for item in second["capabilities"]}, {
            "sympy.qq.linear.unique.v1", "sympy.qq.univariate.distinct-real-roots.v1"})
        self.assertTrue(all(item["performance_claim"] == "NOT_BENCHMARKED"
                            for item in second["capabilities"]))
        self.assertTrue(all(not item["registered"] and not item["runnable"]
                            for item in second["unregistered_tool_candidates"]))
        self.assertFalse(second["policy"]["automatic_install"])

    def test_linear_unique_solution_is_exactly_replayed_without_search_backend(self):
        spec = linear_spec()
        result = router.solve_and_check(spec)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assurance"], "CERTIFICATE_CHECKED")
        self.assertEqual(result["selected_capability"], "sympy.qq.linear.unique.v1")
        self.assertEqual(result["result"]["solution"], ["1/2", "0"])
        with patch.object(router, "_sympy_import", side_effect=AssertionError("search called during replay")):
            self.assertTrue(router.check_certificate(spec, result["certificate"]))
        forged = dict(result["certificate"], solution=["0", "0"])
        self.assertFalse(router.check_certificate(spec, forged))
        wrong_request = linear_spec(rhs=[1, 2, 4])
        self.assertFalse(router.check_certificate(wrong_request, result["certificate"]))

    def test_parametric_and_inconsistent_systems_stay_unknown(self):
        parametric = linear_spec(matrix=[[1, 0], [0, 0]], rhs=[1, 0])
        result = router.route(parametric)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["extension_task"]["capability_gap"], "parametric_linear_solution_family")
        underdetermined = router.route(linear_spec(matrix=[[1, 0]], rhs=[1]))
        self.assertEqual(underdetermined["status"], "UNKNOWN")
        self.assertEqual(underdetermined["extension_task"]["capability_gap"], "underdetermined_system")
        # A unique solution exists here; a contradictory overdetermined row needs an
        # inconsistency certificate, which this adapter does not claim to provide.
        inconsistent = linear_spec(matrix=[[1, 0], [0, 1], [1, 1]], rhs=[1, 2, 4])
        result = router.route(inconsistent)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["extension_task"]["capability_gap"], "inconsistent_system_certificate")

    def test_distinct_real_root_intervals_cover_squarefree_root_set(self):
        spec = roots_spec()
        result = router.solve_and_check(spec)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assurance"], "CERTIFICATE_CHECKED")
        self.assertEqual(result["selected_capability"], "sympy.qq.univariate.distinct-real-roots.v1")
        self.assertEqual(result["result"]["root_count"], 3)
        self.assertEqual(result["result"]["multiplicities"], "NOT_REPORTED")
        self.assertTrue(router.check_certificate(spec, result["certificate"]))
        missing_root = dict(result["certificate"])
        missing_root["intervals"] = missing_root["intervals"][:-1]
        missing_root["distinct_real_root_count"] -= 1
        self.assertFalse(router.check_certificate(spec, missing_root))
        forged = dict(result["certificate"])
        forged["intervals"] = [dict(interval) for interval in forged["intervals"]]
        forged["intervals"][0] = {"lower": "10", "upper": "10"}
        self.assertFalse(router.check_certificate(spec, forged))

    def test_rational_singleton_root_and_constant_polynomial(self):
        spec = roots_spec(coefficients_ascending=[0, -2, 0, 1])  # x(x^2 - 2)
        result = router.solve_and_check(spec)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(any(item["lower"] == item["upper"] == "0"
                            for item in result["certificate"]["intervals"]))
        constant = roots_spec(coefficients_ascending=[5])
        constant_result = router.solve_and_check(constant)
        self.assertEqual(constant_result["status"], "PASS")
        self.assertEqual(constant_result["certificate"]["intervals"], [])

    def test_unregistered_cases_emit_extension_task_and_limits_are_explicit(self):
        unsupported = {"schema": router.REQUEST_SCHEMA, "kind": "multivariate_polynomial",
                       "domain": "QQ", "expression": "x+y"}
        result = router.route(unsupported)
        self.assertEqual(result["status"], "UNKNOWN")
        task = result["extension_task"]
        self.assertEqual(task["schema"], router.EXTENSION_SCHEMA)
        self.assertEqual(task["requested_problem"], unsupported)
        self.assertFalse(task["execution_policy"]["install_authorized"])
        self.assertFalse(task["execution_policy"]["generated_code_execution_authorized"])
        with self.assertRaisesRegex(ValueError, "floats are unsupported"):
            router.route(linear_spec(matrix=[[1.0, 0], [0, 1]], rhs=[1, 2]))
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            router.route({"kind": "other", "payload": "x" * (router.MAX_SPEC_BYTES + 1)})
        budgeted = router.route(roots_spec(max_work_units=1))
        self.assertEqual(budgeted["status"], "UNKNOWN")
        self.assertEqual(budgeted["extension_task"]["capability_gap"], "work_budget_exhausted")
        zero = router.route(roots_spec(coefficients_ascending=[0]))
        self.assertEqual(zero["status"], "UNKNOWN")
        self.assertEqual(zero["extension_task"]["capability_gap"], "zero_polynomial_root_set")

    def test_cli_solve_check_and_capabilities_work_from_another_directory(self):
        command = [sys.executable, "-B", str(ROOT / "scripts/rds_theory_tools.py")]
        temp = ROOT / ".math-router-cli-test"
        temp.mkdir(exist_ok=True)
        request = temp / "request.json"
        certificate = temp / "certificate.json"
        try:
            request.write_text(json.dumps(linear_spec()), encoding="utf-8")
            solved = subprocess.run(command + ["--math-solve", str(request)], cwd=temp,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(solved.returncode, 0, solved.stderr)
            solution = json.loads(solved.stdout)
            self.assertEqual(solution["status"], "PASS")
            certificate.write_text(json.dumps(solution["certificate"]), encoding="utf-8")
            checked = subprocess.run(command + ["--math-check", str(request), "--certificate", str(certificate)],
                                     cwd=temp, capture_output=True, text=True, timeout=20)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)["status"], "PASS")
            listed = subprocess.run(command + ["--math-capabilities"], cwd=temp,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(json.loads(listed.stdout)["schema"], "rds-math-capability-registry-v1")
            certificate.write_text(json.dumps(dict(solution["certificate"], solution=["99", "99"])),
                                   encoding="utf-8")
            forged = subprocess.run(command + ["--math-check", str(request), "--certificate", str(certificate)],
                                    cwd=temp, capture_output=True, text=True, timeout=20)
            self.assertEqual(forged.returncode, 1)
            self.assertEqual(json.loads(forged.stdout)["status"], "INVALID_CERTIFICATE")
        finally:
            request.unlink(missing_ok=True)
            certificate.unlink(missing_ok=True)
            temp.rmdir()


if __name__ == "__main__":
    unittest.main()
