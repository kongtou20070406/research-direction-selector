"""Controlled software recipe checks, not a paid experiment or scientific study."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests")]
import test_rds_project_assembly as fixtures
from rds_project import digest

spec = importlib.util.spec_from_file_location("study_preflight", Path(__file__).with_name("study_preflight.py"))
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class StudyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProjectAssemblyTests("runTest")
        self.fixture.setUp()
        self.fixture._testMethodName = self._testMethodName
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.recipe = self.fixture.recipe
        self.fixture.write_json("task.json", {"query": "Controlled software fixture"})
        self.fixture.write_json("gold.json", {"not_agent_visible": True})
        self.authorization = {"tasks": [{"id": "old-task"}], "model": "fixture-deepseek",
                              "response_model": "fixture-resolved-deepseek", "per_pair_calls": 40, "deadline_epoch": 1000}
        self.declaration = {"schema": 1, "task_id": "new-task", "task_file": "task.json",
            "agent_files": ["task.json", "data.json"], "gold_files": ["gold.json"],
            "prior_task_ids": ["old-task"], "model": self.authorization["model"],
            "response_model": self.authorization["response_model"], "per_arm_calls": 20,
            "deadline_epoch": 1000, "completion_protocol": "finish-function-or-stop-text-v2"}

    def prepare(self, declaration=None, recipe=None):
        with patch.object(preflight.time, "time", return_value=100):
            return preflight.prepare(self.store, self.recipe if recipe is None else recipe,
                                     self.declaration if declaration is None else declaration, self.authorization)

    def test_dry_native_compile_preserves_matched_workload_and_writes_nothing(self):
        original = deepcopy((self.recipe, self.declaration, self.authorization))
        before = sorted(p.relative_to(self.store.root).as_posix() for p in self.store.root.rglob("*"))
        result = self.prepare()
        self.assertEqual((self.recipe, self.declaration, self.authorization), original)
        self.assertEqual(result["bare_runs"], [r["manifest"] for r in result["contract"]["advisor_policy"]["routes"]])
        self.assertEqual(result["contract"]["budget"], self.recipe["budget"])
        self.assertEqual(result["contract"]["allowed_commands"], [r["run"]["argv"] for r in self.recipe["routes"]])
        self.assertEqual(set(result["agent_inputs"]), {"task.json", "data.json"})
        self.assertEqual(result["agent_inputs"]["data.json"], self.fixture.sha("data.json"))
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(self.store.path.exists())
        self.assertEqual(result["independent_sources"], "UNKNOWN")
        self.assertEqual(sorted(p.relative_to(self.store.root).as_posix() for p in self.store.root.rglob("*")), before)

    def test_reused_task_missing_inventory_and_gold_or_alias_exposure_reject(self):
        for changes in ({"task_id": "old-task"}, {"prior_task_ids": []},
                        {"agent_files": ["task.json", "data.json", "gold.json"]},
                        {"agent_files": ["task.json", "data.json", "./data.json"]},
                        {"gold_files": ["./data.json"]}, {"gold_files": ["missing.json"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare({**self.declaration, **changes})
        self.assertFalse(self.store.path.exists())

    def test_model_call_cap_protocol_deadline_and_missing_goal_are_not_defaulted(self):
        for changes in ({"model": "other"}, {"response_model": "other"}, {"per_arm_calls": 21},
                        {"completion_protocol": "old"}, {"deadline_epoch": 1001}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare({**self.declaration, **changes})
        with patch.object(preflight.time, "time", return_value=1000), self.assertRaises(ValueError):
            preflight.prepare(self.store, self.recipe, self.declaration, self.authorization)
        incomplete = deepcopy(self.recipe)
        del incomplete["context"]["decision"]["goal_conditions"]
        with self.assertRaises(ValueError):
            self.prepare(recipe=incomplete)
        self.assertFalse(self.store.path.exists())

    def test_prepared_contract_matches_real_full_initialization_without_dispatch(self):
        result = self.prepare()
        initialized = self.fixture.init(self.recipe)
        self.assertEqual(initialized["workflow"]["mode"], "FULL")
        self.assertEqual(initialized["assembly"]["contract_sha256"], digest(result["contract"]))
        self.assertEqual((self.store.root / "protocol.json").read_bytes(), result["protocol_bytes"])
        report = self.fixture.call("project", "next")
        self.assertEqual(report["status"], "REVIEWED")
        self.assertEqual(report["selected_run"], "baseline")
        self.assertEqual(report["scientific_support"], "UNKNOWN")
        self.assertEqual(self.fixture.starts(), [])
        self.assertEqual(self.store.snapshot()["receipts"], [])
