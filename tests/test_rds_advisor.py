"""Regression checks for evidence boundaries in the programmatic advisor."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_advisor import RDSAdvisor
from rds_advisor_search import review_selection


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.advisor = RDSAdvisor(self.root)

    def structured(self, advice):
        for field in ("observations", "alternative_explanations", "minimal_test", "limitations", "evidence"):
            self.assertIn(field, advice)
            self.assertIsInstance(advice[field], list)
        json.dumps(advice, allow_nan=False)
        self.assertEqual(advice["assurance"], "HEURISTIC_ONLY")

    def test_single_loss_pair_never_certifies_fit_or_forces_training(self):
        for losses in ((1.2, 1.3, 1.25), (0.001, 10, 1), (2, 2, None), (-4, -3, -2), (0, 0, 0), (-1e308, 1e308, None)):
            with self.subTest(losses=losses):
                result = self.advisor.diagnose_fit_status(*losses)
                self.structured(result)
                self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")
                self.assertEqual(result["required_actions"], [])
                self.assertEqual(result["forbidden_actions"], [])

    def test_nonfinite_or_boolean_fit_values_are_rejected(self):
        for value in (float("nan"), float("inf"), True, "1.0", 10**1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.advisor.diagnose_fit_status(value, 1)

    def test_curve_patterns_require_protocol_and_calibrated_tolerance(self):
        telemetry = {"train_loss_history": [3, 2, 1], "val_loss_history": [1, 2, 3],
                     "losses_comparable": True, "matched_checkpoints": True, "trend_tolerance": 0.1}
        self.assertEqual(self.advisor.diagnose_fit_status(1, 3, telemetry=telemetry)["verdict"],
                         "POSSIBLE_GENERALIZATION_GAP")
        for key in ("losses_comparable", "matched_checkpoints", "trend_tolerance"):
            incomplete = {k: v for k, v in telemetry.items() if k != key}
            self.assertEqual(self.advisor.diagnose_fit_status(1, 3, telemetry=incomplete)["verdict"],
                             "INSUFFICIENT_EVIDENCE")
        with self.assertRaisesRegex(ValueError, "endpoints"):
            self.advisor.diagnose_fit_status(2, 3, telemetry=telemetry)
        plateau = {**telemetry, "train_loss_history": [0, 0.01, 0], "val_loss_history": [-3, -3, -3]}
        self.assertEqual(self.advisor.diagnose_fit_status(0, -3, telemetry=plateau)["verdict"],
                         "POSSIBLE_OPTIMIZATION_PLATEAU")

    def test_nan_has_priority_and_locates_fault_before_hyperparameters(self):
        result = self.advisor.advise_on_loss_dynamics({"nan_or_inf": True, "peak_grad_norm": 95,
            "grad_norm_limit": 30, "loss_trend": "STAGNANT"})
        self.structured(result)
        self.assertEqual(result["status"], "CRITICAL_ANOMALY")
        self.assertIn("首个非有限", result["action_items"][0])
        self.assertFalse(any("eps=1e-7" in item or "缩小 5" in item for item in result["action_items"]))
        direct = self.advisor.advise_on_loss_dynamics({"peak_grad_norm": float("inf")})
        self.structured(direct)
        self.assertEqual(direct["status"], "CRITICAL_ANOMALY")

    def test_gradient_and_plateau_preserve_severity_without_universal_threshold(self):
        result = self.advisor.advise_on_loss_dynamics({"peak_grad_norm": 95})
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        result = self.advisor.advise_on_loss_dynamics({"peak_grad_norm": 95, "grad_norm_limit": 30,
                                                      "loss_trend": "STAGNANT"})
        self.assertEqual(result["status"], "GRADIENT_SPIKE_SUSPECTED")
        self.assertTrue(any("平台" in item for item in result["diagnostics"]))
        self.assertFalse(any("2~3" in item or "max_norm=1" in item for item in result["action_items"]))

    def test_exploding_trend_and_sparse_telemetry(self):
        self.assertEqual(self.advisor.advise_on_loss_dynamics({"loss_trend": "EXPLODING"})["status"],
                         "DIVERGENCE_REPORTED")
        self.assertEqual(self.advisor.advise_on_loss_dynamics({})["status"], "INSUFFICIENT_EVIDENCE")
        with self.assertRaises(ValueError):
            self.advisor.advise_on_loss_dynamics({"nan_or_inf": "false"})
        with self.assertRaises(ValueError):
            self.advisor.advise_on_loss_dynamics({"loss_variance": -1})

    def test_rejection_never_invents_inverse_or_model_patch(self):
        plan = {"id": "P1", "source": "original.py", "split_id": "actual-dev"}
        before = copy.deepcopy(plan)
        result = self.advisor.advise_on_rejection("Formal gate FAIL: m < 1", plan)
        self.structured(result)
        self.assertEqual(result["recommended_patch"], {})
        self.assertEqual(plan, before)
        result = self.advisor.advise_on_rejection("Budget unavailable or protected for confirmation", plan)
        self.assertEqual(result["recommended_patch"], {})

    def test_supplied_graph_and_real_state_affect_review(self):
        state = {"active_branch": "b", "branches": {"b": {"status": "STAGNATING", "stagnation_count": 3,
                 "orthogonal_dimension": "representation", "hypotheses": ["H1"]}},
                 "hypotheses": {"H1": {"task_gain": "EXPLORATORY", "mechanism": "NOT_TESTED"}}}
        graph = {"nodes": [{"id": "custom-rule", "scope": "local", "discriminator": "unique real test",
                            "sources": ["source-anchor"]}]}
        before = copy.deepcopy((state, graph))
        results = self.advisor.recommend_next_directions(state, graph)
        for result in results:
            self.structured(result)
        branching = next(r for r in results if r["type"] == "ORTHOGONAL_BRANCH_RECOMMENDATION")
        self.assertNotIn("representation", [d["dimension"] for d in branching["candidate_dimensions"]])
        self.assertNotIn("FFT", json.dumps(branching, ensure_ascii=False))
        review = next(r for r in results if r["type"] == "JUDGMENT_GRAPH_REVIEW")
        self.assertEqual(review["candidate_rules"][0]["discriminator"], "unique real test")
        self.assertEqual(review["observations"][0]["mechanism"], "NOT_TESTED")
        self.assertEqual((state, graph), before)

    def test_cache_advice_does_not_promote_unverified_entries(self):
        state = {"baseline_cache": {"key": {"observations": {"s1": {"control": "1"}}, "first_run_id": "RUN-a"}}}
        result = next(r for r in self.advisor.recommend_next_directions(state, {}) if r["type"] == "COMPUTE_REUSE_ADVICE")
        self.assertEqual(result["observations"][0]["first_run_id"], "RUN-a")
        self.assertTrue(any("未独立验证" in s for s in result["limitations"]))
        self.assertNotIn("100%", json.dumps(result))

    def test_graph_analysis_runs_without_decision_and_preserves_reported_evidence(self):
        context = {"decision": "next-experiment", "facts": {"ready": {"value": True, "source": "run-1"}}}
        state, graph = {"advisor_context": context}, {"nodes": []}
        before = copy.deepcopy((state, graph))
        search = {"candidates": [{"action": {"question": "Does the rival explanation predict a different observation?"}}],
                  "limitations": ["Sources remain INPUT_REPORTED"]}
        callback = Mock(return_value=search)
        with patch.dict(sys.modules, {"rds_advisor_search": SimpleNamespace(
                search_directions=callback, review_selection=review_selection)}):
            self.advisor.recommend_next_directions({}, graph)
            callback.assert_called_once_with(graph, {})
            callback.reset_mock()
            reports = self.advisor.recommend_next_directions(state, graph)
        callback.assert_called_once_with(graph, context)
        report = next(r for r in reports if r["type"] == "EXECUTABLE_DIRECTION_SEARCH")
        self.structured(report)
        self.assertEqual(report["limitations"], search["limitations"])
        self.assertEqual(report["minimal_test"], [search["candidates"][0]["action"]])
        self.assertEqual(report["search"]["selection_review"]["basis"], "NO_READY_DIRECTION")
        self.assertEqual((state, graph), before)

    def test_ingestion_is_anchored_unreviewed_and_idempotent(self):
        document = self.root / "guide.md"
        document.write_text("# A guide\nLearning rate choice depends on the task.\n欠拟合须先检查数据。\n", encoding="utf-8")
        result = self.advisor.ingest_document(document)
        self.structured(result)
        self.assertEqual(result["rules_extracted"], 2)
        self.assertEqual(result["adoption_status"], "UNREVIEWED")
        self.assertEqual(result["evidence"][0]["line_number"], 2)
        self.assertEqual(len(result["evidence"][0]["source_sha256"]), 64)
        self.assertEqual(result["evidence"][0]["source_sha256"], hashlib.sha256(document.read_bytes()).hexdigest())
        repeated = self.advisor.ingest_document(document)
        self.assertEqual(repeated["rules_extracted"], 2)
        self.assertEqual(repeated["rules_added"], 0)
        self.assertEqual(repeated["evidence"], [])
        db = sqlite3.connect(self.advisor.knowledge_db)
        try:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            db.close()
        recommendation = next(r for r in self.advisor.recommend_next_directions({}, {}) if r["type"] == "DOC_REVIEW_CANDIDATES")
        self.assertEqual(recommendation["adoption_status"], "UNREVIEWED")

    def test_bad_knowledge_is_preserved_and_invalid_literature_is_reported(self):
        document = self.root / "guide.md"
        document.write_text("learning rate\n", encoding="utf-8")
        knowledge = self.root / ".rds/advisor_knowledge.json"
        knowledge.parent.mkdir()
        for invalid in ('{"not":"a list"}', '["not an object"]', "broken json"):
            knowledge.write_text(invalid, encoding="utf-8")
            with self.assertRaises(ValueError):
                self.advisor.ingest_document(document)
            self.assertEqual(knowledge.read_text(encoding="utf-8"), invalid)
            self.assertFalse(self.advisor.knowledge_db.exists())
            self.assertFalse(any(r["type"] in {"KNOWLEDGE_LOAD_ERROR", "DOC_REVIEW_CANDIDATES"}
                                 for r in self.advisor.recommend_next_directions({}, {})))
        library = self.root / "references/scientific_tuning_principles.json"
        library.parent.mkdir()
        library.write_text('["not an object"]', encoding="utf-8")
        advisor = RDSAdvisor(self.root)
        self.assertEqual(advisor.literature_principles, [])
        self.assertTrue(advisor.literature_load_errors)

    def test_legacy_bundle_hash_does_not_claim_source_document_identity(self):
        legacy = self.root / ".rds/advisor_knowledge.json"
        legacy.parent.mkdir()
        legacy.write_text(json.dumps([{"line_number": 1, "excerpt": "learning rate",
            "topic": "optimization", "source": "missing-original.md", "source_sha256": "f" * 64}]), encoding="utf-8")
        original = legacy.read_bytes()
        self.assertFalse(any(r["type"] == "DOC_REVIEW_CANDIDATES"
                             for r in self.advisor.recommend_next_directions({}, {})))
        document = self.root / "guide.md"
        document.write_text("学习率\n", encoding="utf-8")
        report = self.advisor.ingest_document(document)
        migrated = next(e for e in report["evidence"] if e["source"] == "missing-original.md")
        self.assertNotIn("source_sha256", migrated)
        self.assertEqual(migrated["legacy_bundle_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(migrated["adoption_status"], "UNREVIEWED")
        self.assertEqual(legacy.read_bytes(), original)

    def test_short_and_chinese_literature_queries(self):
        library = self.root / "references/scientific_tuning_principles.json"
        library.parent.mkdir()
        library.write_text(json.dumps([{"id": "lr-record", "topic": "learning rate"},
                                       {"id": "fit-record", "topic": "underfitting already recorded"}]), encoding="utf-8")
        advisor = RDSAdvisor(self.root)
        for query in ("lr", "学习率", "如何选择学习率"):
            self.assertEqual([p["id"] for p in advisor.query_literature_principles(query)], ["lr-record"])
        self.assertEqual([p["id"] for p in advisor.query_literature_principles("欠拟合")], ["fit-record"])


class LedgerLoopTests(unittest.TestCase):
    """Use actual ProjectStore/checkpoints, not self-signed note fixtures."""
    def setUp(self):
        from test_rds_project import ProjectTests
        from test_rds_advisor_search import node, fact
        helper = ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store = helper.root, helper.store
        self.advisor = RDSAdvisor(self.root)
        self.graph = {"nodes": [node("root", [{"fact": "x", "value": False}])], "edges": []}
        self.graph["nodes"][0]["executable"]["action"].update(
            target={"name": "advisor_candidate_filter", "type": "boolean"}, intervention={"value": True})
        self.context = {"decision": {"id": "choose", "goal_revision": "g1", "scope": {"dataset": "dev"}},
                        "facts": {"root-done": fact(False), "x": fact(False)}}

    def search(self, context=None, graph=None):
        state = self.store.snapshot()
        state["advisor_context"] = copy.deepcopy(context or self.context)
        before = copy.deepcopy(state)
        rows = self.advisor.recommend_next_directions(state, graph or self.graph)
        self.assertEqual(state, before)
        return next(row for row in rows if row["type"] == "EXECUTABLE_DIRECTION_SEARCH")

    def record(self, identity, candidate, outcome="rejected", context=None):
        from rds_checkpoints import save_checkpoint
        context = context or self.context
        decision = context["decision"]
        return save_checkpoint(self.root, identity, self.store.snapshot(), kind="project", decision={
            "question_id": decision["id"], "goal_revision": decision["goal_revision"],
            "scope": decision["scope"], "candidate": candidate, "outcome": outcome,
            "evidence": context["facts"]})

    def test_scope_accepts_only_json_atoms_and_locates_non_atomic_fields(self):
        from rds_advisor import _scope
        value = {"null": None, "string": "fixture", "integer": 30, "number": 0.5, "boolean": True}
        original = copy.deepcopy(value)
        self.assertIsNone(_scope(value))
        self.assertEqual(value, original)
        for child, actual in ((["private-value"], "array (list)"), ({"nested": "private-value"}, "object (dict)"),
                              (("private-value",), "tuple"), ({"private-value"}, "set"), (b"private-value", "bytes")):
            with self.subTest(actual=actual), self.assertRaises(ValueError) as caught:
                _scope({"N_range": child, "purpose": "fixture"})
            message = str(caught.exception)
            self.assertIn('field "N_range" must be a JSON atom; got ' + actual, message)
            self.assertIn("structured original input", message)
            self.assertIn("explicit source binding", message)
            self.assertNotIn("private-value", message)
            self.assertNotIn("at most 16", message)
        with self.assertRaises(ValueError) as caught:
            _scope({"x" * 512: ["private-value"]})
        message = str(caught.exception)
        self.assertIn('field "' + "x" * 80 + '"...', message)
        self.assertLess(len(message), 300)
        self.assertNotIn("x" * 81, message)
        self.assertNotIn("private-value", message)

    def test_scope_distinguishes_dictionary_count_and_key_validation(self):
        from rds_advisor import _scope
        for value in (None, [], "fixture", 30):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be a JSON object"):
                _scope(value)
        self.assertIsNone(_scope({}))
        self.assertIsNone(_scope({"field" + str(i): i for i in range(16)}))
        with self.assertRaisesRegex(ValueError, "has 17 fields; at most 16 are allowed"):
            _scope({"field" + str(i): [] for i in range(17)})
        for key in (None, 30, "", "  ", "x" * 513):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "field names must be non-empty strings of at most 512 characters"):
                _scope({key: None})
        self.assertIsNone(_scope({"x" * 512: None, " spaced ": True}))

    def test_scope_keeps_finite_json_validation_after_atomic_validation(self):
        from rds_advisor import _scope
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Out of range float values"):
                _scope({"number": value})
        with self.assertRaisesRegex(ValueError, "finite, bounded JSON"):
            _scope({"text": "\ud800"})

    def test_scope_keeps_2048_utf8_byte_boundary(self):
        from rds_advisor import _scope
        value = {"v": "é" * 1020}
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
        self.assertEqual(len(raw.encode("utf-8")), 2048)
        self.assertIsNone(_scope(value))
        with self.assertRaisesRegex(ValueError, "JSON exceeds byte limit \\(2049 > 2048\\)"):
            _scope({"v": value["v"] + "x"})
        self.assertEqual(value["v"], "é" * 1020)

    def test_rejected_route_is_removed_from_candidates_actions_and_ranking(self):
        candidate = self.search()["search"]["candidates"][0]
        saved = self.record("rejected", candidate)
        before = self.store.snapshot()
        renamed = copy.deepcopy(self.graph)
        renamed["nodes"][0]["executable"]["action"].update(id="new-display-name", description="Same test reworded")
        result = self.search(graph=renamed)
        self.assertEqual(result["search"]["candidates"], [])
        self.assertEqual(result["minimal_test"], [])
        self.assertEqual(result["search"]["ranking"]["cost_unknown"], [])
        blocked = result["search"]["blocked_candidates"][-1]
        self.assertEqual(blocked["status"], "BLOCKED_REJECTED_ROUTE")
        self.assertEqual(blocked["loop_review"]["checkpoint_sha256"], saved["sha256"])
        self.assertEqual(self.store.snapshot(), before)

    def test_changed_information_reopens_review_but_labels_and_unrelated_facts_do_not(self):
        self.record("rejected", self.search()["search"]["candidates"][0])
        variants = []
        for key, value in (("goal_revision", "g2"), ("scope", {"dataset": "other"})):
            context = copy.deepcopy(self.context)
            context["decision"][key] = value
            variants.append((context, self.graph))
        context = copy.deepcopy(self.context)
        context["facts"]["x"]["binding"] = {"run_id": "new-run"}
        variants.append((context, self.graph))
        graph = copy.deepcopy(self.graph)
        graph["nodes"][0]["executable"]["preconditions"].append({"fact": "root-done", "value": False})
        variants.append((self.context, graph))
        for context, graph in variants:
            with self.subTest(context=context, graph=graph):
                candidates = self.search(context, graph)["search"]["candidates"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["loop_review"]["kind"], "REOPEN_REVIEW")
        for modify in (lambda ctx: ctx["decision"].update(new_evidence_refs=["verified-new-evidence"]),
                       lambda ctx: ctx["facts"]["x"].update(source="renamed-label", evidence_status="VERIFIED"),
                       lambda ctx: ctx["facts"].update(unrelated={"value": True, "source": "declared"})):
            context = copy.deepcopy(self.context)
            modify(context)
            self.assertEqual(self.search(context)["search"]["candidates"], [])

    def test_later_choices_supersede_rejection_and_oscillation_only_warns(self):
        candidate = self.search()["search"]["candidates"][0]
        self.record("a-rejected", candidate)
        self.record("a-accepted", candidate, "accepted")
        self.assertEqual(len(self.search()["search"]["candidates"]), 1)
        other = copy.deepcopy(candidate)
        other["action"]["kind"] = "READ_ONLY_REVIEW"
        self.record("b-accepted", other, "accepted")
        self.record("a-returned", candidate, "deferred")
        output = self.search()["search"]
        self.assertEqual(len(output["candidates"]), 1)
        alert = next(flag for flag in output["loop_review"]["flags"] if flag["kind"] == "DECISION_OSCILLATION")
        self.assertEqual(alert["checkpoint_ids"], ["a-accepted", "b-accepted", "a-returned"])
        self.assertEqual(output["loop_review"]["authorization"], "UNCHANGED")

    def test_corrupt_history_cannot_partially_prune_candidates(self):
        candidate = self.search()["search"]["candidates"][0]
        self.record("valid", candidate)
        self.record("corrupt", candidate)
        db = sqlite3.connect(self.root / ".rds/project.sqlite3")
        try:
            db.execute("DROP TRIGGER checkpoint_no_update")
            db.execute("UPDATE checkpoints SET body='{}' WHERE id='corrupt'")
            db.commit()
        finally:
            db.close()
        output = self.search()["search"]
        self.assertEqual(len(output["candidates"]), 1)
        self.assertEqual(output["loop_review"]["flags"][0]["kind"], "LOOP_HISTORY_REVIEW_ERROR")
        self.assertEqual(output["blocked_candidates"], [])

    def checkpoint_ids(self):
        db = sqlite3.connect(self.root / ".rds/project.sqlite3")
        try:
            return [row[0] for row in db.execute("SELECT id FROM checkpoints ORDER BY rowid")]
        finally:
            db.close()

    def test_route_records_review_would_refuse_are_never_saved(self):
        # #153: checkpoints are append-only, so a route record that review refuses would block the question for good.
        candidate = self.search()["search"]["candidates"][0]
        self.record("valid", candidate, "deferred")
        before = self.checkpoint_ids()
        bad = {"string": ("route-label", "deferred"),
               "no-action": ({"id": "route", "description": "free-text plan"}, "deferred"),
               "outcome": (candidate, "maybe")}
        for name, (value, outcome) in bad.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Checkpoint decision for question "choose" would block loop-history review'):
                    self.record("bad-" + name, value, outcome)
                self.assertEqual(self.checkpoint_ids(), before)
        output = self.search()["search"]
        self.assertEqual(len(output["candidates"]), 1)
        self.assertNotIn("LOOP_HISTORY_REVIEW_ERROR", [flag["kind"] for flag in output["loop_review"]["flags"]])

    def test_record_review_cannot_parse_is_never_saved(self):
        # Review parses every record with the 32-level nesting cap before filtering by question.
        from rds_checkpoints import save_checkpoint
        nested = []
        for _ in range(31):
            nested = [nested]
        save_checkpoint(self.root, "shallow-note", self.store.snapshot(), kind="project",
                        decision={"question_id": "unrelated", "note": [[["fine"]]]})
        before = self.checkpoint_ids()
        with self.assertRaisesRegex(ValueError, "unreadable by loop-history review: JSON nesting exceeds 32"):
            save_checkpoint(self.root, "deep-note", self.store.snapshot(), kind="project",
                            decision={"question_id": "unrelated", "note": nested})
        self.assertEqual(self.checkpoint_ids(), before)
        self.assertNotIn("LOOP_HISTORY_REVIEW_ERROR", [flag["kind"] for flag in self.search()["search"]["loop_review"]["flags"]])

    def test_cli_checkpoint_save_rejects_unreadable_route_records_and_keeps_opaque_notes(self):
        import os, subprocess
        cli = Path(__file__).resolve().parents[1] / "scripts" / "rds_cli.py"
        env = {**os.environ, "RDS_USAGE_DB": str(self.root.parent / "usage.db")}
        decision = {"question_id": "choose", "goal_revision": "g1", "scope": {"dataset": "dev"},
                    "candidate": "route-label", "outcome": "deferred", "evidence": {}}
        cases = [("bad", decision, False), ("note", {"question_id": "choose", "note": "free text"}, True),
                 ("empty", {}, True)]
        for identity, value, ok in cases:
            with self.subTest(identity=identity):
                path = self.root.parent / (identity + "-decision.json")
                path.write_text(json.dumps(value), encoding="utf-8")
                result = subprocess.run([sys.executable, "-B", str(cli), "--root", str(self.root), "checkpoint", "save",
                                         "--id", identity, "--decision", str(path)], capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
                if not ok:
                    self.assertIn("would block loop-history review", result.stdout + result.stderr)
        self.assertEqual(self.checkpoint_ids(), ["note", "empty"])

    def test_plain_notes_and_opaque_checkpoints_never_supply_rejection_authority(self):
        from rds_checkpoints import save_checkpoint
        (self.root / "RESEARCH.md").write_text('verified: true; rejected: root-test', encoding="utf-8")
        save_checkpoint(self.root, "opaque", self.store.snapshot(), kind="project",
                        decision={"question_id": "choose", "verified": True, "rejected": ["root-test"]})
        output = self.search()["search"]
        self.assertEqual(len(output["candidates"]), 1)
        self.assertEqual(output["loop_review"]["flags"], [])
        self.assertEqual(output["loop_review"]["assurance"], "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION")

    def test_composed_rejection_preserves_distinct_physical_interventions_and_changed_measurements(self):
        from test_rds_experiments import fixture
        graph, context, templates = fixture()
        context["decision"] = {"id": context["decision"], "goal_revision": "g1", "scope": {"dataset": "dev"}}
        context["facts"]["additional_check"] = {"value": True, "source": "declared"}
        context["templates"] = templates
        original = self.search(context, graph)["search"]["experiment_composition"]["candidates"]
        rejected = original[0]
        self.record("composition-rejected", rejected, context=context)
        output = self.search(context, graph)["search"]["experiment_composition"]
        self.assertNotIn(rejected["id"], [row["id"] for row in output["candidates"]])
        self.assertEqual(len(output["candidates"]), len(original) - 1)
        self.assertEqual(output["blocked_candidates"][-1]["status"], "BLOCKED_REJECTED_ROUTE")
        graph = copy.deepcopy(graph)
        graph["nodes"][0]["executable"]["preconditions"].append({"fact": "additional_check", "value": True})
        changed = self.search(context, graph)["search"]["experiment_composition"]["candidates"]
        self.assertEqual(len(changed), len(original))
        reopened = next(row for row in changed if row["id"] == rejected["id"])
        self.assertEqual(reopened["loop_review"]["kind"], "REOPEN_REVIEW")
        context = copy.deepcopy(context)
        context["templates"][0]["measurement"]["fact"] = "different_metric_measurement"
        # Keep the original rule graph when only a template measurement changes.
        original_graph, _, _ = fixture()
        changed = self.search(context, original_graph)["search"]["experiment_composition"]["candidates"]
        self.assertEqual(len(changed), len(original))

    def test_cli_uses_local_ledger_with_no_obelisk_on_path(self):
        import os
        import subprocess
        self.record("rejected", self.search()["search"]["candidates"][0])
        for name, value in (("context.json", self.context), ("graph.json", self.graph)):
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
            "--root", str(self.root), "advise", "--research-context", str(self.root / "context.json"),
            "--graph", str(self.root / "graph.json")], capture_output=True, encoding="utf-8", timeout=15,
            env=dict(os.environ, PATH=""))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        output = next(row["search"] for row in report["recommendations"] if row["type"] == "EXECUTABLE_DIRECTION_SEARCH")
        self.assertEqual(output["candidates"], [])
        self.assertEqual(output["blocked_candidates"][-1]["loop_review"]["kind"], "REPEAT_REJECTED_ROUTE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
