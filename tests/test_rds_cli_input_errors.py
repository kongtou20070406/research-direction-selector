"""Malformed L3 contract, hypothesis and plan inputs name the field to repair."""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/reference-run"


def run_cli(root, *argv):
    environment = dict(os.environ, RDS_USAGE_LOG="0")
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--root", str(root), *argv],
                          cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60, env=environment)


def example(name):
    return json.loads((EXAMPLE / name).read_text(encoding="utf-8"))


class CLIInputErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        shutil.copytree(EXAMPLE, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return str(path)

    def assertRejected(self, result, message, init=False):
        self.assertEqual(result.returncode, 1, result.stderr)
        hint = "\n"
        if init:
            # This absolute fixture path contains Windows separators, so its
            # PowerShell display argument is quoted. Keep the entire error and
            # hint exact; recovery discovers this root instead of making a new one.
            root_argument = ("'" + str(self.root).replace("'", "''") + "'"
                             if os.name == 'nt' else shlex.quote(str(self.root)))
            hint += ("[RDS-HINT] Inspect the existing project first: python -B scripts/rds_cli.py --root "
                     + root_argument + " project discover; see docs/project-lifecycle.md\n")
        self.assertEqual(result.stderr, "[RDS-REJECT] " + message + hint)

    def initialize(self):
        self.assertEqual(run_cli(self.root, "init", "--contract", str(self.root / "contract.json")).returncode, 0)
        added = run_cli(self.root, "hypothesis", "add", "--spec", str(self.root / "hypothesis.json"))
        self.assertEqual(added.returncode, 0, added.stderr)

    def status(self):
        result = run_cli(self.root, "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_malformed_contract_names_the_field_and_creates_no_ledger(self):
        contract = example("contract.json")
        split = contract["splits"]["development"]
        cases = [
            ("5", "Contract must be a JSON object"),
            ("null", "Contract must be a JSON object"),
            (json.dumps(" ".join(contract)), "Contract must be a JSON object"),
            ({**contract, "primary_metric": "mse"}, "Contract primary_metric must be a JSON object"),
            ({**contract, "primary_metric": {"name": "mse", "direction": "min"}},
             "Missing contract primary_metric field: min_useful_delta"),
            ({**contract, "budget": []}, "Contract budget must be a JSON object"),
            ({**contract, "budget": {}}, "Missing contract budget field: limits"),
            ({**contract, "budget": {"limits": contract["budget"]["limits"]}},
             "Missing contract budget field: confirmation_floor"),
            ({**contract, "splits": ["development"]}, "Contract splits must be a JSON object"),
            ({**contract, "splits": {"development": "development.csv"}},
             "Contract split development must be a JSON object"),
            ({**contract, "splits": {"development": {k: v for k, v in split.items() if k != "role"}}},
             "Missing contract split development field: role"),
            ({**contract, "splits": {"development": {**split, "role": ["development"]}}}, "Unknown split role"),
            ({**contract, "splits": {"development": {k: v for k, v in split.items() if k != "cohort"}}},
             "Missing contract split development field: cohort"),
            ({**contract, "splits": {"development": {**split, "path": 7}}},
             "Contract split development path must be a non-empty path string"),
            ({k: v for k, v in contract.items() if k != "baseline_source"},
             "Missing contract fields: baseline_source"),
            ({**contract, "baseline_source": ""}, "Contract baseline_source must be a non-empty path string"),
        ]
        for value, message in cases:
            with self.subTest(message=message):
                result = run_cli(self.root, "init", "--contract", self.write("bad-contract.json", value))
                self.assertRejected(result, message, init=True)
                self.assertFalse((self.root / ".rds").exists())

    def test_existing_contract_rejections_keep_their_messages(self):
        contract = example("contract.json")
        cases = [
            ("[]", "Missing contract fields: project_id, claim, primary_metric, budget, splits, baseline_source"),
            ([{"manipulation_verified": True}], "Self-signed verification fields are forbidden"),
            ({k: v for k, v in contract.items() if k != "budget"}, "Missing contract fields: budget"),
            ({**contract, "splits": {}}, "Register at least one data partition"),
            ({**contract, "splits": []}, "Register at least one data partition"),
            ({**contract, "splits": None}, "Register at least one data partition"),
            ({**contract, "primary_metric": {**contract["primary_metric"], "name": "mae"}},
             "The reference runner supports only paired MSE minimization"),
            ({**contract, "budget": {**contract["budget"], "limits": {"runs": 2}}},
             "Budget must explicitly contain runtime_ms and runs"),
        ]
        for value, message in cases:
            with self.subTest(message=message):
                result = run_cli(self.root, "init", "--contract", self.write("bad-contract.json", value))
                self.assertRejected(result, message, init=True)
                self.assertFalse((self.root / ".rds").exists())

    def test_malformed_hypothesis_and_plan_leave_the_ledger_unchanged(self):
        self.initialize()
        before = self.status()
        hypothesis, plan = example("hypothesis.json"), example("plan.json")
        cases = [
            (("hypothesis", "add", "--spec"), [hypothesis], "Hypothesis spec must be a JSON object"),
            (("hypothesis", "add", "--spec"), [{"falsifier_triggered": True}],
             "Self-signed verification fields are forbidden"),
            (("hypothesis", "add", "--spec"), {k: v for k, v in hypothesis.items() if k != "id"},
             "Missing hypothesis field: id"),
            (("plan", "create", "--spec"), [plan], "Plan spec must be a JSON object"),
            (("gate", "check", "--plan"), "null", "Plan spec must be a JSON object"),
            (("plan", "create", "--spec"), {**plan, "id": {"nested": "P1"}},
             "Invalid identity (up to 80 ASCII letters, numbers, _, . or -)"),
            (("plan", "create", "--spec"), {**plan, "hypothesis_id": ["H1"]}, "Unknown hypothesis"),
            (("plan", "create", "--spec"), {**plan, "split_id": {"id": "development"}}, "Unknown split"),
            (("plan", "create", "--spec"), {**plan, "purpose": ["explore"]}, "Unknown purpose"),
            (("plan", "create", "--spec"), {**plan, "source": None}, "Plan source must be a non-empty path string"),
        ]
        for command, value, message in cases:
            with self.subTest(message=message, command=command):
                result = run_cli(self.root, *command, self.write("bad-spec.json", value))
                self.assertRejected(result, message)
                self.assertEqual(self.status(), before)

    def test_unknown_plan_id_is_rejected_without_changing_state(self):
        self.initialize()
        before = self.status()
        for command in (("plan", "cancel"), ("run", "execute"), ("run", "recover")):
            with self.subTest(command=command):
                self.assertRejected(run_cli(self.root, *command, "--id", "P404"), "Unknown plan ID: P404")
                self.assertEqual(self.status(), before)

    def test_malformed_branch_fork_spec_names_the_field_and_keeps_branches(self):
        self.initialize()
        before = run_cli(self.root, "branch", "list")
        self.assertEqual(before.returncode, 0, before.stderr)
        status = self.status()
        fork = {"id": "B2", "orthogonal_dimension": "representation", "rationale": "orthogonal probe"}
        invalid_identity = "Invalid identity (up to 80 ASCII letters, numbers, _, . or -)"
        cases = [
            (["not", "an", "object"], "Branch spec must be a JSON object"),
            ("7", "Branch spec must be a JSON object"),
            ("null", "Branch spec must be a JSON object"),
            ('"B2"', "Branch spec must be a JSON object"),
            ({k: v for k, v in fork.items() if k != "id"}, "Missing branch field: id"),
            # Checks that already named the problem keep their messages and precedence.
            ([{"manipulation_verified": True}], "Self-signed verification fields are forbidden"),
            ({**fork, "id": None}, invalid_identity),
            ({**fork, "id": ["B2"]}, invalid_identity),
        ]
        for value, message in cases:
            with self.subTest(message=message, value=value):
                result = run_cli(self.root, "branch", "fork", "--spec", self.write("bad-branch.json", value))
                self.assertRejected(result, message)
                self.assertEqual(run_cli(self.root, "branch", "list").stdout, before.stdout)
                self.assertEqual(self.status(), status)
        forked = run_cli(self.root, "branch", "fork", "--spec", self.write("branch.json", fork))
        self.assertEqual(forked.returncode, 0, forked.stderr)
        self.assertEqual(json.loads(forked.stdout), {"forked_branch": "B2", "parent_id": "main", "active_branch": "B2"})

    def test_non_object_plan_for_fuzz_and_advise_is_rejected_without_traceback(self):
        self.initialize()
        before = self.status()
        for command in (("meta", "fuzz", "--plan"), ("advise", "--plan")):
            for value in (["P1"], "7", "null"):
                with self.subTest(command=command, value=value):
                    result = run_cli(self.root, *command, self.write("bad-plan.json", value))
                    self.assertRejected(result, "Plan spec must be a JSON object")
                    self.assertEqual(self.status(), before)
        fuzzed = run_cli(self.root, "meta", "fuzz", "--plan", str(self.root / "plan.json"))
        self.assertEqual(fuzzed.returncode, 0, fuzzed.stderr)
        self.assertEqual(json.loads(fuzzed.stdout)["original_plan_id"], "P1")
        advised = run_cli(self.root, "advise", "--plan", str(self.root / "plan.json"))
        self.assertEqual(advised.returncode, 0, advised.stderr)
        self.assertEqual(json.loads(advised.stdout)["advisor_type"], "PLAN_COMPLIANCE_PASS")
        # An object plan that fails the check still gets advice rather than a rejection.
        rejected = run_cli(self.root, "advise", "--plan", self.write("empty-plan.json", {}))
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertNotEqual(json.loads(rejected.stdout)["advisor_type"], "PLAN_COMPLIANCE_PASS")
        self.assertEqual(self.status(), before)

    def test_valid_reference_plan_still_reserves_and_cancels_idempotently(self):
        self.initialize()
        spec = str(self.root / "plan.json")
        created = run_cli(self.root, "plan", "create", "--spec", spec)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertEqual(json.loads(created.stdout)["run_status"], "RESERVED")
        again = run_cli(self.root, "plan", "create", "--spec", spec)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(json.loads(again.stdout)["idempotent"])
        for _ in range(2):
            cancelled = run_cli(self.root, "plan", "cancel", "--id", "P1")
            self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
            self.assertEqual(json.loads(cancelled.stdout)["run_status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
