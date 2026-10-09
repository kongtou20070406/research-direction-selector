"""Read concurrency and race-safe admission regressions (no wall-time assertions)."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark.run import Project, C7_CONTROL, C7_FORMAL
import rds_cli as cli


class CliConcurrencyTests(unittest.TestCase):
    def project(self, **kwargs):
        project = Project(**kwargs).init()
        self.addCleanup(project.close)
        return project

    def spec_args(self, project, spec):
        path = project.root / "direct-plan.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return argparse.Namespace(spec=str(path), plan=str(path))

    def test_status_reads_committed_snapshot_while_writer_is_active(self):
        project = self.project()
        state = cli.RDSState(project.root)
        with state.transaction():
            status = cli.cmd_status(None, state)
            self.assertEqual(status["budget"]["spent"]["runs"], 0)

    def test_status_does_not_rewrite_state(self):
        project = self.project()
        state = cli.RDSState(project.root)
        before = state.db_path.read_bytes()
        cli.cmd_status(None, state)
        self.assertEqual(before, state.db_path.read_bytes())

    def test_old_state_versions_remain_readable(self):
        project = self.project()
        state = cli.RDSState(project.root)
        for version in ("5.1.0", "5.2.0", "5.3.0", "5.4.0", "5.5.0-rc.1", "5.5.0-rc.2", "5.6.0-rc.1", "5.6.0-rc.2", "5.7.0"):
            with state.transaction() as (_, snapshot):
                snapshot["version"] = version
            self.assertEqual(cli.cmd_status(None, state)["version"], version)
        with state.transaction() as (_, snapshot):
            snapshot["engine_sha256"] = "0" * 64
        self.assertEqual(cli.cmd_status(None, state)["version"], "5.7.0")
        with self.assertRaisesRegex(ValueError, "a new contract is required"):
            cli.cmd_plan(self.spec_args(project, project.plan()), state)
        self.assertEqual(cli.cmd_status(None, state)["budget"]["reserved"]["runs"], 0)

    def test_advisor_accepts_valid_plan_without_nested_writer_lock(self):
        project = self.project()
        args = self.spec_args(project, project.plan())
        args.root = str(project.root)
        answer = cli.cmd_advise(args, cli.RDSState(project.root))
        self.assertEqual(answer["advisor_type"], "PLAN_COMPLIANCE_PASS")
        self.assertEqual(cli.cmd_status(None, cli.RDSState(project.root))["budget"]["reserved"]["runs"], 0)

    def test_advisor_solver_does_not_hold_reader_across_writer_commit(self):
        project = self.project()
        state = cli.RDSState(project.root)
        args = self.spec_args(project, project.plan())
        args.root = str(project.root)

        def committing_writer(hypothesis, source):
            with state.transaction() as (db, _):
                cli.RDSState.event(db, "CONCURRENT_ADVISOR_WRITE")
            return {"status": "PASS", "assurance": "AST_ONLY", "backend": "ast"}

        with patch.object(cli, "formal_gate", side_effect=committing_writer):
            answer = cli.cmd_advise(args, state)
        self.assertEqual(answer["advisor_type"], "PLAN_COMPLIANCE_PASS")
        self.assertEqual(cli.cmd_status(None, state)["event_count"], 3)

    def test_solver_runs_without_writer_lock_and_budget_is_rechecked(self):
        project = self.project(budget=25000, floor=0)
        state = cli.RDSState(project.root)
        args = self.spec_args(project, project.plan(runtime=20000))

        def concurrent_reservation(hypothesis, source):
            with state.transaction() as (_, snapshot):
                other = project.plan("other", runtime=20000)
                snapshot["plans"]["other"] = {"spec": other, "run_status": "RESERVED"}
                snapshot["budget"]["reserved"] = dict(other["resources"])
            return {"status": "PASS", "assurance": "AST_ONLY", "backend": "ast"}

        with patch.object(cli, "formal_gate", side_effect=concurrent_reservation):
            with self.assertRaisesRegex(ValueError, "Budget unavailable"):
                cli.cmd_plan(args, state)
        self.assertEqual(set(cli.cmd_status(None, state)["plans"]), {"other"})

    def test_source_change_during_solver_rejects_stale_binding(self):
        project = self.project()
        state = cli.RDSState(project.root)
        args = self.spec_args(project, project.plan())

        def changed_source(hypothesis, source):
            (project.root / "model.py").write_text(
                "def control(x): return x\ndef treatment(x): return 3*x\n", encoding="utf-8")
            return {"status": "PASS", "assurance": "AST_ONLY", "backend": "ast"}

        with patch.object(cli, "formal_gate", side_effect=changed_source):
            with self.assertRaisesRegex(ValueError, "Admission binding changed"):
                cli.cmd_plan(args, state)
        self.assertEqual(cli.cmd_status(None, state)["plans"], {})

    def test_confirmation_exposure_during_solver_is_rechecked(self):
        project = self.project()
        state = cli.RDSState(project.root)
        args = self.spec_args(project, project.plan(split_id="final", purpose="confirm"))

        def exposed_split(hypothesis, source):
            with state.transaction() as (_, snapshot):
                snapshot["exposures"].append({"split_id": "final", "purpose": "manual_inspect"})
            return {"status": "PASS", "assurance": "AST_ONLY", "backend": "ast"}

        with patch.object(cli, "formal_gate", side_effect=exposed_split):
            with self.assertRaisesRegex(ValueError, "Confirmation lineage already exposed"):
                cli.cmd_plan(args, state)
        self.assertEqual(cli.cmd_status(None, state)["plans"], {})

    def test_exact_certificate_is_rechecked_and_reused(self):
        project = Project(source=C7_CONTROL + "def treatment(x): return 2*x/(1+x)\n").init(formal=C7_FORMAL)
        self.addCleanup(project.close)
        state = cli.RDSState(project.root)
        cli.cmd_plan(self.spec_args(project, project.plan("first")), state)
        with patch.object(cli, "formal_gate", side_effect=AssertionError("certificate should be reused")):
            second = cli.cmd_plan(self.spec_args(project, project.plan("second")), state)
        self.assertEqual(second["binding"]["admission_probe"]["assurance"], "CERTIFICATE_CHECKED")

    def test_tampered_certificate_falls_back_to_fresh_verification(self):
        project = Project(source=C7_CONTROL + "def treatment(x): return 2*x/(1+x)\n").init(formal=C7_FORMAL)
        self.addCleanup(project.close)
        state = cli.RDSState(project.root)
        cli.cmd_plan(self.spec_args(project, project.plan("first")), state)
        with state.transaction() as (_, snapshot):
            snapshot["plans"]["first"]["binding"]["admission_probe"]["certificate"]["source_sha256"] = "0" * 64
        with patch.object(cli, "formal_gate", wraps=cli.formal_gate) as fresh:
            second = cli.cmd_plan(self.spec_args(project, project.plan("second")), state)
        fresh.assert_called_once()
        self.assertEqual(second["run_status"], "RESERVED")

    def test_symbolic_result_without_certificate_is_not_replayed(self):
        project = Project(source=C7_CONTROL + "def treatment(x): return 2*x/(1+x)\n").init(formal=C7_FORMAL)
        self.addCleanup(project.close)
        state = cli.RDSState(project.root)
        cli.cmd_plan(self.spec_args(project, project.plan("first")), state)
        with state.transaction() as (_, snapshot):
            probe = snapshot["plans"]["first"]["binding"]["admission_probe"]
            probe.pop("certificate")
            probe["assurance"] = "SYMBOLIC_CHECKED"
        with patch.object(cli, "formal_gate", wraps=cli.formal_gate) as fresh:
            cli.cmd_plan(self.spec_args(project, project.plan("second")), state)
        fresh.assert_called_once()

    def test_cache_requires_source_hypothesis_and_engine_binding(self):
        project = Project(source=C7_CONTROL + "def treatment(x): return 2*x/(1+x)\n").init(formal=C7_FORMAL)
        self.addCleanup(project.close)
        state = cli.RDSState(project.root)
        binding = cli.cmd_plan(self.spec_args(project, project.plan("first")), state)["binding"]
        with state.snapshot() as (_, snapshot):
            hypothesis = snapshot["hypotheses"]["H1"]["spec"]
        for key in ("source_sha256", "hypothesis_sha256", "engine_sha256"):
            old = {**binding, key: "0" * 64}
            self.assertIsNone(cli.cached_admission({"plans": {"first": {"binding": old}}},
                                                  binding, hypothesis, binding["source"]))

    def test_explicit_separation_overrides_legacy_necessity_kind(self):
        contract = {"primary_metric": {"min_useful_delta": "1/100"},
                    "evaluation_scope": "finite_locked_dataset"}
        result = {"gain": "1", "probe": {"status": "PASS", "necessity_counterexamples": ["sample"]}}
        formal = {**C7_FORMAL, "kind": "threshold_necessity", "statement": "threshold_separation"}
        self.assertEqual(cli.assess(result, contract, "explore", False, formal)["mechanism"], "INCONCLUSIVE")
        formal.pop("statement")
        self.assertEqual(cli.assess(result, contract, "explore", False, formal)["mechanism"], "REFUTED")

    def test_runner_gain_can_exceed_declared_input_rational_limits(self):
        project = Project(source="def control(x): return 0\ndef treatment(x): return x\n")
        self.addCleanup(project.close)
        (project.root / "dev.csv").write_text(
            "sample_id,x,y\nsmall,1/340282366920938463463374607431768211457,1\n", encoding="utf-8")
        project.init()
        outcome = project.run()
        self.assertGreater(len(outcome["gain"]), 80)
        self.assertEqual(outcome["task_gain"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
