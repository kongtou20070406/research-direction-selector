import copy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_lean_verify
import rds_theory_progression as progression
from rds_verify_types import digest


SPEC = {
    "schema": 1,
    "claim_id": progression.CLAIM_ID,
    "domain": ["0", "1"],
    "assumptions": [],
    "transport_obligations": list(progression.TRANSPORTS),
    "limits": {"max_domain_size": 4, "max_native_pairs": 16, "egraph_iterations": 8},
}
CONCISE_SPEC = {
    "schema": 2,
    "domain": ["0", "1"],
    "limits": {"max_domain_size": 4, "max_native_pairs": 16, "egraph_iterations": 8},
}


def native_pass(spec):
    return {"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED", "backend": "lean4_closed_rational",
            "certificate": {"spec_sha256": digest(spec), "verdict": "PASS"}}


def installed_native_lean():
    try:
        rds_lean_verify._executable()
        return True
    except (rds_lean_verify.NoNativeLean, ValueError, OSError):
        return False


class TheoryProgressionTests(unittest.TestCase):
    def test_three_existing_checks_bind_the_same_claim_and_all_domain_pairs(self):
        seen = []

        def check(spec):
            seen.append(spec)
            return native_pass(spec)

        result = progression.run_progression(SPEC, native_verify=check)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assurance"], "FINITE_DOMAIN_EXHAUSTIVE_PLUS_NATIVE_LEAVES")
        self.assertEqual(result["scientific_assurance"], "UNKNOWN")
        self.assertEqual(result["application_status"], "UNKNOWN")
        self.assertEqual([stage["name"] for stage in result["stages"]], list(progression.STAGES))
        model, egraph, native = result["stages"]
        self.assertEqual(model["result"]["assurance"], "BOUNDED_FINITE_MODEL_VERIFIED")
        self.assertEqual(model["result"]["combinations_checked"], 4)
        self.assertEqual(egraph["result"]["assurance"], "BOUNDED_REWRITE_CHECK")
        self.assertEqual(egraph["result"]["certificate_status"], "NOT_EMITTED")
        self.assertEqual(egraph["result"]["transport"]["status"], "PASS")
        self.assertEqual(native["result"]["assurance"], "LEAN_KERNEL_CHECKED")
        self.assertEqual(native["result"]["transport"]["pairs_checked"], 4)
        self.assertEqual(len(seen), 4)
        self.assertTrue(all(leaf["certificate"] and leaf["status"] == "PASS"
                            for leaf in native["result"]["leaves"]))
        self.assertEqual([(item["left"], item["right"]) for item in seen],
                         [("0", "0"), ("0", "0"), ("0", "0"), ("1", "1")])
        self.assertTrue(all(item["kind"] == "lean_obligation" and item["relation"] == "eq"
                            for item in seen))
        for index, stage in enumerate(result["stages"]):
            self.assertTrue(progression._check_handoff(
                stage, progression.STAGES[index], result["claim_sha256"],
                None if index == 0 else result["stages"][index - 1]["stage_sha256"]))
            self.assertEqual(stage["claim_sha256"], result["claim_sha256"])

    def test_concise_request_derives_the_fixed_claim_and_transport_contract(self):
        result = progression.run_progression(CONCISE_SPEC, native_verify=native_pass)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["claim"]["id"], progression.CLAIM_ID)
        self.assertEqual(result["claim"]["assumptions"], [])
        self.assertEqual(result["claim"]["transport_obligations"], list(progression.TRANSPORTS))
        self.assertTrue(all(stage["claim_sha256"] == result["claim_sha256"]
                            for stage in result["stages"]))

    def test_concise_request_does_not_ignore_incompatible_manual_contract_fields(self):
        cases = (
            {**CONCISE_SPEC, "assumptions": ["assume the result"]},
            {**CONCISE_SPEC, "claim_id": "unrelated_claim"},
            {**CONCISE_SPEC, "transport_obligations": []},
        )
        with mock.patch("rds_operators.BoundedFiniteModelOperator.verify_cayley_property") as dispatch:
            dispatch.side_effect = AssertionError("Invalid contract fields must not reach a proof stage")
            for case in cases:
                with self.subTest(case=case):
                    result = progression.run_progression(case, native_verify=native_pass)
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertEqual([stage["result"]["status"] for stage in result["stages"]],
                                     ["SKIPPED"] * 3)
            dispatch.assert_not_called()

    def test_empty_invalid_unrelated_and_oversized_declarations_stay_unknown(self):
        cases = []
        for domain in ([], ["0", "0/1"], ["0", "1", "2", "3", "4"]):
            cases.append({**SPEC, "domain": domain})
        cases.extend(({**SPEC, "claim_id": "unrelated_claim"},
                      {**SPEC, "assumptions": ["assume the result"]},
                      {**SPEC, "transport_obligations": list(reversed(progression.TRANSPORTS))},
                      {**SPEC, "stages": [{"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED"}]},
                      {**SPEC, "domain": [True]},
                      {**SPEC, "domain": [0.5]},
                      {**SPEC, "limits": {**SPEC["limits"], "max_native_pairs": 17}},
                      {**SPEC, "limits": {**SPEC["limits"], "max_domain_size": True}},
                      {"schema": True}))
        with mock.patch("rds_operators.BoundedFiniteModelOperator.verify_cayley_property") as unused:
            unused.side_effect = AssertionError("Invalid input must be rejected before a stage dispatch")
            for case in cases:
                with self.subTest(case=case):
                    result = progression.run_progression(case, native_verify=native_pass)
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertEqual([stage["result"]["status"] for stage in result["stages"]],
                                     ["SKIPPED"] * 3)
                    unused.assert_not_called()

    def test_budget_skip_is_visible_and_does_not_upgrade_egraph_pass(self):
        spec = {**SPEC, "limits": {**SPEC["limits"], "max_native_pairs": 3}}
        result = progression.run_progression(spec, native_verify=native_pass)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([item["result"]["status"] for item in result["stages"]],
                         ["PASS", "PASS", "SKIPPED"])
        self.assertIn("budget", result["stages"][2]["result"]["reason"])

    def test_missing_or_lower_assurance_lean_never_becomes_pass(self):
        for answer in ({"status": "UNKNOWN", "assurance": "NONE", "reason": "native Lean unavailable"},
                       {"status": "PASS", "assurance": "CERTIFICATE_CHECKED", "certificate": {}}):
            with self.subTest(answer=answer):
                result = progression.run_progression(SPEC, native_verify=lambda _spec: answer)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["stages"][-1]["result"]["status"], "UNKNOWN")
                self.assertEqual(result["stages"][-1]["result"]["assurance"], "NONE")

    def test_unaccepted_native_leaf_never_passes_regardless_of_diagnostic(self):
        diagnostics = ({}, {"reason": None}, {"reason": ""}, {"reason": "  "}, {"reason": 0},
                       {"reason": ["unavailable"]}, {"reason": {"message": "unavailable"}},
                       {"tactics": [{"reason": 7}]}, {"tactics": None})
        responses = ({"status": "UNKNOWN", "assurance": "NONE"},
                     {"status": "FAIL", "assurance": "NONE"},
                     {"status": "PASS", "assurance": "NONE"},
                     {"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED"},
                     {"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED",
                      "certificate": {"verdict": "PASS", "spec_sha256": "foreign"}},
                     {"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED",
                      "certificate": {"verdict": "UNKNOWN", "spec_sha256": "foreign"}})
        for response in responses:
            for diagnostic in diagnostics:
                with self.subTest(response=response, diagnostic=diagnostic):
                    check = mock.Mock(return_value={**response, **diagnostic})
                    result = progression.run_progression({**SPEC, "domain": ["1"]}, native_verify=check)
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertEqual(result["assurance"], "NONE")
                    native = result["stages"][-1]["result"]
                    self.assertEqual(native["status"], "UNKNOWN")
                    self.assertEqual(native["assurance"], "NONE")
                    self.assertEqual(native["transport"]["pairs_checked"], 0)
                    self.assertIsNone(native["leaves"][0]["certificate"])
                    self.assertEqual(native["leaves"][0]["status"], "UNKNOWN")
                    self.assertIsInstance(native["reason"], str)
                    self.assertTrue(native["reason"])
                    self.assertEqual(check.call_count, 1)

    def test_native_failure_stops_dispatch_and_does_not_count_as_a_checked_pair(self):
        for failure_index in (0, 1, 3):
            with self.subTest(failure_index=failure_index):
                seen = []

                def check(spec):
                    seen.append(spec)
                    if len(seen) == failure_index + 1:
                        return {"status": "UNKNOWN", "assurance": "NONE", "reason": None}
                    return native_pass(spec)

                result = progression.run_progression(SPEC, native_verify=check)
                native = result["stages"][-1]["result"]
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["assurance"], "NONE")
                self.assertEqual(native["status"], "UNKNOWN")
                self.assertEqual(native["assurance"], "NONE")
                self.assertEqual(len(seen), failure_index + 1)
                self.assertEqual(native["transport"]["pairs_checked"], failure_index)
                self.assertEqual([leaf["status"] for leaf in native["leaves"]],
                                 ["PASS"] * failure_index + ["UNKNOWN"])
                self.assertTrue(all(leaf["certificate"] for leaf in native["leaves"][:-1]))
                self.assertIsNone(native["leaves"][-1]["certificate"])

    def test_cli_unknown_native_leaf_with_null_reason_returns_two(self):
        import rds_theory_tools

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "spec.json"
            path.write_text(json.dumps({**SPEC, "domain": ["1"]}), encoding="utf-8")
            output = io.StringIO()
            with mock.patch("rds_verify.LeanFormalEngine.verify", return_value={
                    "status": "UNKNOWN", "assurance": "NONE", "reason": None}) as native, \
                    mock.patch.object(sys, "argv", ["rds_theory_tools.py", "--progression", str(path)]), \
                    contextlib.redirect_stdout(output):
                exit_code = rds_theory_tools.main()
            self.assertEqual(exit_code, 2, output.getvalue())
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "UNKNOWN")
            self.assertEqual(report["assurance"], "NONE")
            self.assertEqual([stage["result"]["status"] for stage in report["stages"]],
                             ["PASS", "PASS", "UNKNOWN"])
            leaf = report["stages"][-1]["result"]["leaves"][0]
            self.assertEqual(leaf["status"], "UNKNOWN")
            self.assertEqual(leaf["assurance"], "NONE")
            self.assertIsNone(leaf["certificate"])
            native.assert_called_once()

    def test_bounded_values_cannot_be_promoted_by_a_different_assurance(self):
        with mock.patch("rds_operators.BoundedFiniteModelOperator.verify_cayley_property",
                        return_value={"status": "PASS", "assurance": "CERTIFICATE_CHECKED",
                                      "property": "commutative", "domain_size": 2,
                                      "combinations_checked": 4}):
            result = progression.run_progression(SPEC, native_verify=native_pass)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([item["result"]["status"] for item in result["stages"]],
                         ["UNKNOWN", "SKIPPED", "SKIPPED"])

    def test_egraph_unknown_skips_lean_and_keeps_unknown(self):
        answer = {"status": "UNKNOWN", "assurance": "NONE", "certificate_status": "NOT_EMITTED"}
        with mock.patch("rds_operators.EGraphEquivalenceOperator.verify_algebraic_equivalence",
                        return_value=answer):
            result = progression.run_progression(SPEC, native_verify=native_pass)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["stages"][1]["result"]["status"], "UNKNOWN")
        self.assertEqual(result["stages"][2]["result"]["status"], "SKIPPED")

    def test_egraph_pass_with_a_supplied_certificate_is_not_accepted(self):
        answer = {"status": "PASS", "assurance": "BOUNDED_REWRITE_CHECK", "equivalent": True,
                  "domain": "rational_polynomials", "variables": ["a", "b"],
                  "input_sha256": "a" * 64, "certificate_status": "CERTIFICATE_CHECKED"}
        with mock.patch("rds_operators.EGraphEquivalenceOperator.verify_algebraic_equivalence",
                        return_value=answer):
            result = progression.run_progression(SPEC, native_verify=native_pass)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["stages"][1]["result"]["status"], "UNKNOWN")
        self.assertEqual(result["stages"][1]["result"]["reported_status"], "PASS")
        self.assertEqual(result["stages"][1]["result"]["transport"]["status"], "UNKNOWN")
        self.assertEqual(result["stages"][2]["result"]["status"], "SKIPPED")

    def test_failed_finite_counterexample_stops_later_stages(self):
        counterexample = {"status": "FAIL", "assurance": "COUNTEREXAMPLE_FOUND",
                          "property": "commutative", "counterexample": {"witness": ["0", "1"]}}
        with mock.patch("rds_operators.BoundedFiniteModelOperator.verify_cayley_property",
                        return_value=counterexample):
            result = progression.run_progression(SPEC, native_verify=native_pass)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual([stage["result"]["status"] for stage in result["stages"]],
                         ["FAIL", "SKIPPED", "SKIPPED"])

    def test_nonclosed_domain_does_not_misstate_a_failed_finite_check_as_refutation(self):
        result = progression.run_progression({**SPEC, "domain": ["1", "2"]}, native_verify=native_pass)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["stages"][0]["result"]["assurance"], "CLOSURE_VIOLATION")
        self.assertEqual(result["stages"][1]["result"]["status"], "SKIPPED")

    def test_tampered_or_foreign_stage_handoff_is_rejected(self):
        result = progression.run_progression(SPEC, native_verify=native_pass)
        finite = result["stages"][0]
        self.assertFalse(progression._check_handoff({**finite, "claim_sha256": "f" * 64},
                                                    progression.STAGES[0], result["claim_sha256"], None))
        self.assertFalse(progression._check_handoff({**finite, "stage_sha256": "0" * 64},
                                                    progression.STAGES[0], result["claim_sha256"], None))
        egraph = result["stages"][1]
        self.assertFalse(progression._check_handoff(egraph, progression.STAGES[1],
                                                    result["claim_sha256"], "foreign-stage"))

    def test_invalid_rational_domain_is_unknown_without_native_dispatch(self):
        native = mock.Mock(side_effect=AssertionError("Invalid input must not dispatch Lean"))
        result = progression.run_progression({**SPEC, "domain": ["1/0"]}, native_verify=native)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["assurance"], "NONE")
        self.assertEqual([stage["result"]["status"] for stage in result["stages"]],
                         ["SKIPPED", "SKIPPED", "SKIPPED"])
        native.assert_not_called()

    def test_real_cli_rejects_zero_denominator_and_deep_json_before_dispatch(self):
        payloads = (json.dumps({**SPEC, "domain": ["1/0"]}), "[" * 8000 + "]" * 8000)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "spec.json"
            command = [sys.executable, "-B", str(ROOT / "scripts/rds_theory_tools.py"),
                       "--progression", str(path)]
            for payload in payloads:
                with self.subTest(input_size=len(payload)):
                    path.write_text(payload, encoding="utf-8")
                    result = subprocess.run(command, cwd=folder, capture_output=True,
                                            encoding="utf-8", timeout=20)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertEqual(report["status"], "UNKNOWN")
                    self.assertEqual(report["assurance"], "NONE")
                    self.assertEqual([stage["result"]["status"] for stage in report["stages"]],
                                     ["SKIPPED", "SKIPPED", "SKIPPED"])
                    self.assertTrue(all("certificate" not in stage["result"]
                                        for stage in report["stages"]))

    def test_json_loader_rejects_duplicate_keys_float_nonfinite_and_oversized_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "spec.json"
            for raw in (b'{"schema":1,"schema":1}', b'{"x":NaN}', b'{"x":1.0}',
                        b" " * (progression.MAX_SPEC_BYTES + 1)):
                path.write_bytes(raw)
                with self.subTest(prefix=raw[:32]), self.assertRaises(progression.InvalidProgression):
                    progression.load_spec(path)

    def test_real_cli_fails_closed_without_native_lean_and_reports_skipped_work(self):
        command = [sys.executable, "-B", str(ROOT / "scripts/rds_theory_tools.py"),
                   "--progression", str(ROOT / "examples/theory-reformulation/progression.json")]
        with tempfile.TemporaryDirectory() as folder:
            env = os.environ.copy()
            env["RDS_LEAN_EXECUTABLE"] = str(Path(folder) / "missing-lean")
            result = subprocess.run(command, cwd=folder, env=env, capture_output=True,
                                    encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual([stage["result"]["status"] for stage in report["stages"]],
                         ["PASS", "PASS", "UNKNOWN"])
        self.assertNotIn("certificate", report["stages"][-1]["result"])

    @unittest.skipUnless(installed_native_lean(), "No native Lean toolchain is installed")
    def test_real_cli_native_lean_progression(self):
        command = [sys.executable, "-B", str(ROOT / "scripts/rds_theory_tools.py"),
                   "--progression", str(ROOT / "examples/theory-reformulation/progression.json")]
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-2000:])
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["stages"][-1]["result"]["assurance"], "LEAN_KERNEL_CHECKED")
        self.assertEqual(report["stages"][-1]["result"]["transport"]["pairs_checked"], 4)


if __name__ == "__main__":
    unittest.main()
