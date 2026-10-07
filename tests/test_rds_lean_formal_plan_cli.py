"""Trusted specification planning and budget refusal through the real formal CLI."""
from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_affine_toolchain as affine
import rds_cli as cli
import rds_dynamics_verify as dynamics
import rds_lean_verify as lean
import rds_verify as engine
from rds_proof_cache import ProofCache


def example(name="affine_fixed_point_synthesis.json"):
    return json.loads((ROOT / "examples/formal" / name).read_text(encoding="utf-8"))


def native_lean_available():
    try:
        lean._executable()
        return True
    except (lean.NoNativeLean, ValueError, OSError):
        return False


class FormalPlanCLITests(unittest.TestCase):
    def assert_unproved(self, result, status="UNKNOWN"):
        self.assertEqual(result["status"], status, result)
        self.assertEqual(result["assurance"], "NONE", result)
        self.assertNotIn("certificate", result)

    def guarded_cli(self, spec, action, *options):
        """Run the actual parser/dispatcher while forbidding tools, replay and cache."""
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            forbidden = [stack.enter_context(mock.patch.object(owner, name,
                         side_effect=AssertionError("Unexpected " + name)))
                         for owner, name in [(lean, "verify"), (dynamics, "verify"),
                                             (affine, "_verify_composition"),
                                             (engine, "checked_result"), (ProofCache, "verify")]]
            argv = ["rds_cli.py", "--root", directory, "formal", action,
                    "--spec", str(path), *options]
            output = io.StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stdout(output):
                code = cli._main()
            for function in forbidden:
                function.assert_not_called()
            return code, json.loads(output.getvalue())

    def test_inspection_never_calls_any_registered_generator(self):
        with mock.patch.object(engine.ProofRule, "generate", side_effect=AssertionError("proof search")) as generate:
            result = engine.plan(example())
        generate.assert_not_called()
        self.assert_unproved(result, "READY")
        report = result["optimization"]
        self.assertEqual([step["name"] for step in report["schedule"]],
                         ["fixed_point_exact", "coordinate_obligations"])
        self.assertEqual(report["estimated_work_units"], 6)
        self.assertEqual(report["schedule"][1]["depends_on"], ["fixed_point_exact"])
        self.assertEqual(report["schedule"][1]["required_assurance"], "LEAN_KERNEL_CHECKED")

    def test_cli_plan_is_ready_without_cache_replay_or_child_generators(self):
        code, result = self.guarded_cli(example(), "plan")
        self.assertEqual(code, 0)
        self.assert_unproved(result, "READY")

    def test_cli_over_budget_refuses_before_child_generators(self):
        for action in ("plan", "verify"):
            for maximum in (0, 5):
                with self.subTest(action=action, maximum=maximum):
                    code, result = self.guarded_cli(example(), action, "--max-work-units", str(maximum))
                    self.assertEqual(code, 2)
                    self.assert_unproved(result)
                    self.assertIn("exceeds", result["reason"])
                    report = result["optimization" if action == "plan" else "plan_optimization"]
                    self.assertEqual(report["budget"], {"max_work_units": maximum, "estimated_work_units": 6})

    def test_invalid_api_budgets_refuse_even_before_witness_search(self):
        with mock.patch.object(affine, "_solve", side_effect=AssertionError("witness search")) as search:
            for maximum in (-1, True, False, 6.0, "6"):
                for entry in (engine.plan, engine.verify, affine.verify):
                    with self.subTest(maximum=maximum, entry=entry.__name__):
                        self.assert_unproved(entry(example(), max_work_units=maximum))
        search.assert_not_called()

    def test_cli_negative_budget_is_unknown_and_noninteger_is_parse_error(self):
        code, result = self.guarded_cli(example(), "verify", "--max-work-units", "-1")
        self.assertEqual(code, 2)
        self.assert_unproved(result)
        with tempfile.TemporaryDirectory() as directory:
            spec = Path(directory) / "spec.json"
            spec.write_text(json.dumps(example()), encoding="utf-8")
            run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                                  "--root", directory, "formal", "verify", "--spec", str(spec),
                                  "--max-work-units", "6.0"], capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn("invalid int value", run.stderr)
            self.assertFalse((Path(directory) / ".rds/proofs.sqlite3").exists())

    def test_unsupported_kinds_and_theorem_modules_refuse_before_tools(self):
        specs = [{"schema": 1, "kind": "lean_obligation", "relation": "eq", "left": "1", "right": "1"},
                 example("bounded_progression_module.json"),
                 {"schema": 1, "kind": "unregistered"}]
        for spec in specs:
            for action in ("plan", "verify"):
                with self.subTest(kind=spec["kind"], action=action):
                    code, result = self.guarded_cli(spec, action, "--max-work-units", "1000")
                    self.assertEqual(code, 2)
                    self.assert_unproved(result)

    def test_explicit_budget_and_tactics_are_incompatible(self):
        code, result = self.guarded_cli(example(), "verify", "--max-work-units", "6", "--tactics", "lean4")
        self.assertEqual(code, 2)
        self.assert_unproved(result)
        self.assertIn("incompatible", result["reason"])

    def test_invalid_spec_and_absent_witness_do_not_become_proofs(self):
        specs = [{**example(), "untrusted_plan": {}},
                 {"schema": 1, "kind": affine.KIND, "model": {"matrix": [["1"]], "bias": ["1"]}},
                 {"schema": 1, "kind": affine.KIND, "model": {"matrix": [["0"]], "bias": []}}]
        for spec in specs:
            for action in ("plan", "verify"):
                with self.subTest(spec=spec, action=action):
                    code, result = self.guarded_cli(spec, action, "--max-work-units", "1000")
                    self.assertEqual(code, 2)
                    self.assert_unproved(result)

    def test_ordered_composition_uses_the_registered_trusted_plan(self):
        code, result = self.guarded_cli(example("affine_composed_fixed_point.json"), "plan")
        self.assertEqual(code, 0)
        self.assert_unproved(result, "READY")
        schedule = result["optimization"]["schedule"]
        self.assertEqual([step["name"] for step in schedule],
                         ["compose_affine_chain", "fixed_point_exact", "coordinate_obligations"])
        self.assertEqual(schedule[1]["depends_on"], ["compose_affine_chain"])

    def test_user_supplied_proof_plan_is_not_a_public_specification(self):
        code, result = self.guarded_cli(affine.build_proof_plan(example()), "plan")
        self.assertEqual(code, 2)
        self.assert_unproved(result)

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_exact_budget_passes_and_plan_matches_actual_generation(self):
        for name in ("affine_fixed_point_synthesis.json", "affine_composed_fixed_point.json"):
            with self.subTest(name=name):
                spec = example(name)
                planned = engine.plan(spec)["optimization"]
                result = engine.verify(spec, max_work_units=planned["estimated_work_units"])
                self.assertEqual(result["status"], "PASS", result)
                self.assertEqual(result["assurance"], "COMPOSITION_CHECKED")
                self.assertEqual(result["plan_optimization"]["schedule"], planned["schedule"])
                self.assertEqual(result["plan_optimization"]["plan_sha256"], planned["plan_sha256"])
                self.assertEqual(engine.checked_result(spec, result["certificate"])["status"], "PASS")

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_real_cli_cache_cannot_bypass_budget_and_replay_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "spec.json"
            spec.write_text(json.dumps(example()), encoding="utf-8")

            def run(action, *options):
                completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                                            "--root", directory, "formal", action, "--spec", str(spec),
                                            *options], capture_output=True, text=True, timeout=60)
                return completed.returncode, json.loads(completed.stdout)

            code, original = run("verify")
            self.assertEqual(code, 0, original)
            self.assertEqual(original["cache"], {"hit": False, "stored": True})
            code, cached = run("verify")
            self.assertEqual(code, 0, cached)
            self.assertTrue(cached["cache"]["hit"])
            cache_path = root / ".rds/proofs.sqlite3"
            cache_bytes = cache_path.read_bytes()
            code, refused = run("verify", "--max-work-units", "0")
            self.assertEqual(code, 2, refused)
            self.assert_unproved(refused)
            self.assertNotIn("cache", refused)
            self.assertEqual(cache_path.read_bytes(), cache_bytes)
            artifact = root / "proof.json"
            code, exact = run("verify", "--max-work-units", "6", "--output", str(artifact))
            self.assertEqual(code, 0, exact)
            self.assertEqual(exact["assurance"], "COMPOSITION_CHECKED")
            self.assertNotIn("cache", exact)
            self.assertEqual(cache_path.read_bytes(), cache_bytes)
            code, replayed = run("check", "--certificate", str(artifact))
            self.assertEqual(code, 0, replayed)
            self.assertEqual(replayed["status"], "PASS")
            tampered = copy.deepcopy(exact)
            tampered["certificate"]["proof"]["certificate"]["candidate"][0] = "0"
            artifact.write_text(json.dumps(tampered), encoding="utf-8")
            code, rejected = run("check", "--certificate", str(artifact))
            self.assertEqual(code, 2, rejected)
            self.assertEqual(rejected["status"], "UNKNOWN")
            code, unchanged = run("verify")
            self.assertEqual(code, 0, unchanged)
            self.assertTrue(unchanged["cache"]["hit"])


if __name__ == "__main__":
    unittest.main()
