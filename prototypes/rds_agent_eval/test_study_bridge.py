"""Real owned CLI and synthetic counts; not a paid agent or scientific study."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests")]
import test_rds_project_assembly as fixtures
from prototypes.rds_agent_eval import study_bridge as bridge, study_preflight
from rds_project import file_sha
from rds_steering import plan


class StudyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProjectAssemblyTests("runTest")
        self.fixture.setUp()
        self.fixture._testMethodName = self._testMethodName
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.recipe = deepcopy(self.fixture.recipe)
        self.fixture.write("code.py", Path(__file__).with_name("plant_methods.py").read_bytes())
        self.fixture.write("data.csv", 'introduction.period\tpathway\tn\n' + ''.join(
            f'{p}\t{w}\t{n}\n' for p, cells in zip(
                ("Before 1500", "1501-1900", "1901-1984", "1985-2019"),
                ((5, 1, 3), (2, 4, 1), (1, 7, 3), (1, 9, 2)))
            for w, n in zip(("AgriForest", "Gardening", "Unintentional"), cells)))
        self.recipe["files"]["data"] = ["data.csv"]
        for route, method in zip(self.recipe["routes"], ("period_counts", "gardening_dominance")):
            run = route["run"]
            run["argv"] = [sys.executable, "-B", "code.py", "data.csv", method, run["outpaths"][0]]
            route["observations"][0]["selector"] = {"pointer": "/method"}
        self.recipe["routes"][1]["preconditions"] = [{"fact": "baseline.score", "value": "period_counts"}]
        self.recipe["context"]["decision"]["goal_conditions"] = [{"fact": "repair.score", "value": "gardening_dominance"}]
        self.fixture.write_json("task.json", {"query": "Controlled synthetic period counts"})
        self.fixture.write_json("gold.json", {"not_agent_visible": True})
        self.authorization = {"tasks": [{"id": "old"}], "model": "fixture", "response_model": "fixture",
                              "per_pair_calls": 40, "deadline_epoch": 1000}
        self.declaration = {"schema": 1, "task_id": "new", "task_file": "task.json",
            "agent_files": ["task.json", "data.csv"], "gold_files": ["gold.json"], "prior_task_ids": ["old"],
            "model": "fixture", "response_model": "fixture", "per_arm_calls": 20, "deadline_epoch": 1000,
            "completion_protocol": "finish-function-or-stop-text-v2"}
        self.claim = {"earliest_observed_main_period": "1501-1900",
            "main_periods": ["1501-1900", "1901-1984", "1985-2019"],
            "exact_crossover_year": None, "scope": "RECORDED_PERIOD_COUNTS"}

    def initialize(self):
        with patch.object(study_preflight.time, "time", return_value=100):
            self.prepared = study_preflight.prepare(self.store, self.recipe, self.declaration, self.authorization)
        self.assertEqual(self.fixture.init(self.recipe)["workflow"]["mode"], "FULL")

    def inspect(self, outputs=None, **changes):
        receipts = self.store.snapshot()["receipts"]
        pins = [{"run_id": r["run_id"], "receipt_sha256": r["sha256"],
                 "path": "outputs/" + r["run_id"] + ".json"} for r in receipts]
        args = {"expected_snapshot_sha256": plan(self.store, dialogue=True)["snapshot_sha256"],
                "outputs": pins if outputs is None else outputs, "source_path": "data.csv", "claim": self.claim}
        return bridge.inspect(self.store, self.prepared, **{**args, **changes})

    def test_real_owned_advice_and_receipts_project_all_outputs_without_writes(self):
        self.initialize()
        for expected in ("baseline", "repair"):
            self.assertEqual(self.fixture.call("project", "next")["selected_run"], expected)
            self.fixture.call("project", "advance")
        self.fixture.call("project", "next")
        before = file_sha(self.store.path)
        result = self.inspect()
        self.assertEqual(result["advisor"]["status"], "COMPATIBLE_RECORDED_ADVICE")
        self.assertEqual(len(result["original_observations"]["observations"]), 2)
        self.assertEqual([r["task_correctness"] for r in result["domain_outputs"]], ["PASS", "PASS"])
        self.assertEqual(result["scientific_support"], "UNKNOWN")
        self.assertEqual(result["free_text_review"], "UNKNOWN")
        self.assertEqual(file_sha(self.store.path), before)

    def test_failed_original_attempt_is_retained_unknown_without_verification_or_rebuy(self):
        self.recipe["routes"][0]["run"]["argv"][-2] = "invalid-method"
        self.initialize()
        failed = self.fixture.call("project", "advance", ok=False)
        self.assertEqual(failed.returncode, 1)
        self.fixture.call("project", "next")
        before = file_sha(self.store.path)
        with patch.object(bridge, "verify", side_effect=AssertionError("Failed attempt verified")):
            result = self.inspect()
        self.assertEqual(result["domain_outputs"][0]["run_status"], "FAILED")
        self.assertEqual(result["domain_outputs"][0]["task_correctness"], "UNKNOWN")
        self.assertIsNone(result["domain_outputs"][0]["verification"])
        self.assertTrue(result["original_observations"]["observations"][0]["receipt"]["errors"])
        self.assertEqual(file_sha(self.store.path), before)

    def test_cross_route_bad_pin_changed_source_and_output_reject_without_writes(self):
        self.initialize()
        self.fixture.call("project", "advance")
        self.fixture.call("project", "next")
        receipt = self.store.snapshot()["receipts"][0]
        pin = {"run_id": receipt["run_id"], "receipt_sha256": receipt["sha256"], "path": "outputs/baseline.json"}
        for change in ({"path": "outputs/repair.json"}, {"receipt_sha256": "0" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.inspect([{**pin, **change}])
        original = (self.store.root / "data.csv").read_bytes()
        self.fixture.write("data.csv", b"changed source")
        with self.assertRaises(ValueError):
            self.inspect([pin])
        self.fixture.write("data.csv", original)
        self.fixture.write("outputs/baseline.json", b"changed output")
        before = file_sha(self.store.path)
        with self.assertRaises(ValueError):
            self.inspect([pin])
        self.assertEqual(file_sha(self.store.path), before)

    def test_native_view_recheck_rejects_output_change_after_numeric_projection(self):
        self.initialize()
        self.fixture.call("project", "advance")
        self.fixture.call("project", "next")
        original_verify = bridge.verify
        def changed(*args, **kwargs):
            result = original_verify(*args, **kwargs)
            self.fixture.write("outputs/baseline.json", b"changed after reading")
            return result
        before = file_sha(self.store.path)
        with patch.object(bridge, "verify", side_effect=changed), self.assertRaises(ValueError):
            self.inspect()
        self.assertEqual(file_sha(self.store.path), before)
