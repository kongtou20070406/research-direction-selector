"""Prevent adaptive reuse from being passed off as fresh RSI confirmation."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from rds_meta import apply_rule, rollback_rule
from rds_rsi import commit_casepack, evaluate_candidate
from rds_rsi_confirmation import input_fingerprint
from test_rds_rsi import fixture


class ConfirmationExposureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "rsi"
        self.graph, self.rule, self.pack = fixture()
        self.pack["confirmation_campaign"] = "disk100-curriculum"

    def evaluate(self, rule=None, pack=None):
        return evaluate_candidate(rule or self.rule, self.graph, pack or self.pack,
                                  confirmation_dir=self.directory)

    def changed_rule(self):
        rule = deepcopy(self.rule)
        rule["scope"] += "; revised candidate"
        return rule

    def assert_reuse_blocked(self, rule=None, pack=None):
        with patch("rds_rsi._run") as execute:
            report = self.evaluate(rule, pack)
        self.assertEqual(report["status"], "REJECTED", report)
        self.assertEqual(report["cases_evaluated"], 0)
        self.assertIn("exposed", " ".join(report["rejection_reasons"]))
        execute.assert_not_called()

    def test_frozen_pair_can_replay_and_adopt_without_new_confirmation_credit(self):
        report = self.evaluate()
        self.assertTrue(report["adoption_eligible"], report)
        replay = self.evaluate()
        self.assertEqual({k: v for k, v in report.items() if k != "elapsed_ms"},
                         {k: v for k, v in replay.items() if k != "elapsed_ms"})
        self.assertEqual(report["confirmation_exposure"]["independent_sealing"], "NOT_ATTESTED")
        ledger = json.loads((self.directory / "confirmation/exposures.json").read_text())
        self.assertEqual(len(ledger["evaluations"]), 1)
        graph_path = Path(self.temp.name) / "graph.json"
        original = json.dumps(self.graph).encode()
        graph_path.write_bytes(original)
        adoption = apply_rule(self.rule, graph_path, evaluation=report, cases=self.pack,
                              record_dir=self.directory)
        self.assertTrue(adoption["adopted"])
        rollback_rule(adoption["record_path"], graph_path, record_dir=self.directory)
        self.assertEqual(graph_path.read_bytes(), original)

    def test_another_candidate_cannot_reuse_exposed_heldout_inputs(self):
        self.assertTrue(self.evaluate()["adoption_eligible"])
        self.assert_reuse_blocked(self.changed_rule())

    def test_case_renaming_and_expected_grade_changes_cannot_refresh_inputs(self):
        self.evaluate()
        for change in ("names", "grades", "campaign"):
            with self.subTest(change=change):
                pack = deepcopy(self.pack)
                if change == "names":
                    for case in pack["cases"]:
                        case["id"] += "-renamed"
                    pack["proposed_on_ids"] = [v + "-renamed" for v in pack["proposed_on_ids"]]
                    pack["heldout_ids"] = [v + "-renamed" for v in pack["heldout_ids"]]
                elif change == "grades":
                    pack["cases"][1]["expected"] = {"no_ready": True}
                else:
                    pack["confirmation_campaign"] = "pretend-new-campaign"
                self.assert_reuse_blocked(pack=commit_casepack(pack))

    def test_ordinary_regression_exposure_cannot_later_be_relabelled_confirmation(self):
        development = deepcopy(self.pack)
        development.pop("confirmation_campaign")
        report = self.evaluate(pack=development)
        self.assertTrue(report["adoption_eligible"])
        self.assertNotIn("confirmation_exposure", report)
        self.assert_reuse_blocked()

    def test_failed_candidate_still_consumes_its_confirmation_inputs(self):
        failed = deepcopy(self.rule)
        failed.pop("executable")
        report = self.evaluate(rule=failed)
        self.assertFalse(report["adoption_eligible"])
        self.assertEqual(report["cases_evaluated"], 4)
        self.assert_reuse_blocked()

    def test_timed_out_replay_does_not_make_its_inputs_fresh_again(self):
        pack = deepcopy(self.pack)
        pack["budget"]["max_runtime_ms"] = 1
        with patch("rds_rsi.time.perf_counter", side_effect=[0, .002, .003]):
            report = self.evaluate(pack=pack)
        self.assertIn("wall-time", " ".join(report["rejection_reasons"]))
        self.assert_reuse_blocked(rule=self.changed_rule())

    def test_confirmation_without_persistent_history_fails_closed(self):
        report = evaluate_candidate(self.rule, self.graph, self.pack)
        self.assertEqual(report["status"], "REJECTED")
        self.assertIn("persistent", " ".join(report["rejection_reasons"]))

    def test_duplicate_or_development_overlap_is_not_fresh_confirmation(self):
        for original in (self.pack["cases"][0], self.pack["cases"][1]):
            with self.subTest(original=original["id"]):
                pack = deepcopy(self.pack)
                extra = deepcopy(original)
                extra.update(id="fake-independent-case", partition="heldout")
                pack["cases"].append(extra)
                pack["heldout_ids"].append(extra["id"])
                report = self.evaluate(pack=commit_casepack(pack))
                self.assertEqual(report["status"], "REJECTED")
                self.assertEqual(report["cases_evaluated"], 0)

    def test_new_input_payloads_can_confirm_a_new_candidate(self):
        self.evaluate()
        new = deepcopy(self.pack)
        for case in new["cases"]:
            case["context"]["facts"]["new_probe_identity"] = {"value": "fresh-instance", "source": "fixture:new"}
        report = self.evaluate(self.changed_rule(), new)
        self.assertTrue(report["adoption_eligible"], report)
        self.assertFalse(report["research_policy_gain_measured"])
        # A changed payload passes identity checks; that does not prove semantic independence.
        self.assertEqual(report["confirmation_exposure"]["independent_sealing"], "NOT_ATTESTED")

    def test_corrupt_ledger_cannot_be_overwritten_to_reset_history(self):
        self.evaluate()
        ledger_path = self.directory / "confirmation/exposures.json"
        ledger = json.loads(ledger_path.read_text())
        ledger["inputs"] = {}
        original = json.dumps(ledger).encode()
        ledger_path.write_bytes(original)
        with patch("rds_rsi._run") as execute:
            report = self.evaluate()
        self.assertEqual(report["status"], "REJECTED")
        self.assertIn("integrity", " ".join(report["rejection_reasons"]))
        self.assertEqual(ledger_path.read_bytes(), original)
        execute.assert_not_called()

    def test_rewritten_confirmation_assurance_cannot_authorize_adoption(self):
        report = self.evaluate()
        report["confirmation_exposure"]["independent_sealing"] = "ATTESTED"
        graph_path = Path(self.temp.name) / "graph.json"
        original = json.dumps(self.graph).encode()
        graph_path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "independently executed replay"):
            apply_rule(self.rule, graph_path, evaluation=report, cases=self.pack,
                       record_dir=self.directory)
        self.assertEqual(graph_path.read_bytes(), original)

    def test_cli_evaluation_records_exposure_in_the_declared_project(self):
        project = Path(self.temp.name)
        for name, value in (("graph", self.graph), ("rule", self.rule), ("cases", self.pack)):
            (project / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        command = [sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--root", str(project),
                   "meta", "evaluate-rule", "--graph", str(project / "graph.json"),
                   "--rule", str(project / "rule.json"), "--cases", str(project / "cases.json")]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("confirmation_exposure", report)
        self.assertTrue((project / ".rds/rsi/confirmation/exposures.json").is_file())

    def test_input_fingerprint_ignores_names_partitions_and_expected_grades(self):
        """The module's own claim: renaming a case or re-splitting it does not
        refresh the same input, so only engine, context and templates count."""
        base = {"context": {"alpha": 1}, "templates": ["t0"]}
        canonical = input_fingerprint(dict(base, name="a", partition="dev", expected_grade=1))
        renamed = input_fingerprint(dict(base, name="b", partition="test", expected_grade=0))
        self.assertEqual(canonical, renamed,
                         "case name, partition and expected grade must not refresh an input")
        self.assertEqual(canonical, input_fingerprint(dict(base, engine="search")),
                         "an absent engine is the search engine")
        self.assertNotEqual(canonical, input_fingerprint({"context": {"alpha": 2}, "templates": ["t0"]}),
                            "a different context is a different input")
        self.assertNotEqual(canonical, input_fingerprint({"context": {"alpha": 1}, "templates": ["t1"]}),
                            "different templates are a different input")


if __name__ == "__main__":
    unittest.main()
