"""Synthetic original-format retained rows in real temporary SQLite fixtures.

No project execution, checkpoint writer, external model or long workload.
"""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_core import canonical, object_digest
import rds_transition as transition

CONTRACT = {"advisor_policy": {"schema": 1, "routes": [], "model": "fixture-policy"}}


def receipt_row(run_id="r1", status="FAILED", **extra):
    body = {"schema": 1, "run_id": run_id, "attempt_id": "a" * 32,
            "run_status": status, "process_status": "COMPLETED" if status == "SUCCEEDED" else status,
            "manifest_sha256": "1" * 64, "effective_contract_sha256": object_digest(CONTRACT),
            "argv": ["worker.py", "--arm", "control"], "cwd": "synthetic-root",
            "executor_sha256": "3" * 64, "runtime_fingerprint": {"python": "3.11", "host": "fixture"},
            "protocol": {"data_split": "unused-test", "numeric_protocol": "fp32"},
            "exit_code": 7, "timeout": status == "TIMED_OUT", "errors": ["synthetic failure"],
            "resources": {"wall_seconds": {"unit": "seconds", "measured": 1.25,
                                           "unknown": False, "charged_estimate": 0.0},
                          "gpu_seconds": {"unit": "seconds", "measured": None,
                                          "unknown": True, "charged_estimate": 5.0,
                                          "observed_lower_bound": 0.5}},
            "assessment": {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN"}, **extra}
    sha = object_digest(body)
    return {"run_id": run_id, "sha256": sha, "body": canonical({**body, "sha256": sha})}


def checkpoint_row(receipt, stage="before", **changes):
    sha = object_digest({"run_id": receipt["run_id"], "manifest_sha256": receipt["manifest_sha256"],
                         "contract_sha256": receipt["effective_contract_sha256"]})[:48]
    identity = "owned-" + stage + "-" + sha
    contract = deepcopy(CONTRACT)
    run = {"id": receipt["run_id"], "run_id": receipt["run_id"],
           "manifest_sha256": receipt["manifest_sha256"],
           "status": "RESERVED" if stage == "before" else receipt["process_status"],
           "attempt_id": None if stage == "before" else receipt["attempt_id"]}
    decision = {"outcome": "plan_locked", "scientific_support": "UNKNOWN",
                "candidate": {"action": {"id": "inspect-original-output", "kind": "fixture"}},
                "prediction": {"status": "UNKNOWN", "model_sha256": "4" * 64, "score": 0.4},
                "model_provenance": {"id": "fixture-predictor", "sha256": "4" * 64},
                "owned_history": {"run_id": receipt["run_id"],
                                  "manifest_sha256": receipt["manifest_sha256"],
                                  "effective_contract_sha256": receipt["effective_contract_sha256"],
                                  "method_sha256": "5" * 64}}
    if stage == "after":
        decision["previous_checkpoint"] = "owned-before-" + sha
        decision["execution"] = {"run_id": receipt["run_id"], "attempt_id": receipt["attempt_id"],
                                 "receipt_sha256": receipt["sha256"], "run_status": receipt["run_status"]}
    record = {"schema": "rds-checkpoint-v1", "id": identity, "kind": "project",
              "created_ns": 10 if stage == "before" else 20,
              "contract_sha256": object_digest(contract),
              "snapshot": {"contract": contract, "runs": [run], "receipts": [],
                           "budget": {"gpu_seconds": {"charged_estimate": 0 if stage == "before" else 5,
                                                      "spent_measured": 0, "reserved": 5 if stage == "before" else 0}}},
              "decision": decision, "decision_assurance": "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION",
              **changes}
    return reseal_checkpoint(record)


def reseal_checkpoint(record, *, pretty=False):
    raw = json.dumps(record, indent=2, ensure_ascii=True) if pretty else canonical(record)
    return {"id": record["id"], "sha": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "body": raw}


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "retained.sqlite3"
        row = receipt_row()
        self.receipt = json.loads(row["body"])
        self.before = checkpoint_row(self.receipt)
        self.after = checkpoint_row(self.receipt, "after")
        db = sqlite3.connect(self.path)
        try:
            db.execute("CREATE TABLE receipts(run_id TEXT PRIMARY KEY, sha256 TEXT, body TEXT)")
            db.execute("CREATE TABLE checkpoints(id TEXT PRIMARY KEY, sha TEXT, body TEXT)")
            db.execute("INSERT INTO receipts VALUES (:run_id,:sha256,:body)", row)
            db.executemany("INSERT INTO checkpoints VALUES (:id,:sha,:body)", [self.before, self.after])
            db.commit()
        finally:
            db.close()

    def retained(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            receipts = [dict(row) for row in db.execute("SELECT run_id,sha256,body FROM receipts ORDER BY run_id")]
            checkpoints = [dict(row) for row in db.execute("SELECT id,sha,body FROM checkpoints ORDER BY id")]
            return receipts, checkpoints
        finally:
            db.close()

    def test_temp_retained_export_is_deterministic_and_does_not_mutate_sources(self):
        original_bytes = self.path.read_bytes()
        receipts, checkpoints = self.retained()
        inputs = deepcopy((receipts, checkpoints))
        first = transition.export_transitions(receipts, checkpoints)
        second = transition.export_transitions(receipts[::-1], checkpoints[::-1])
        self.assertEqual(transition.transition_json(first), transition.transition_json(second))
        self.assertEqual((receipts, checkpoints), inputs)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        item = first["transitions"][0]
        self.assertEqual(item["before"]["value"], json.loads(self.before["body"])["snapshot"])
        self.assertEqual(item["after"]["value"], json.loads(self.after["body"])["snapshot"])
        self.assertEqual(item["observation"]["value"], self.receipt)
        item["before"]["value"]["budget"].clear()
        self.assertEqual((receipts, checkpoints), inputs)

    def test_absent_history_remains_unknown_even_with_after_checkpoint(self):
        item = transition.export_transitions([receipt_row()], [self.after])["transitions"][0]
        for key in ("before", "decision", "prior_prediction"):
            self.assertEqual(item[key]["status"], "UNKNOWN")
        self.assertEqual(item["after"]["scope"], "RETAINED_CHECKPOINT")
        self.assertEqual(item["action"]["scope"], "EXECUTED_COMMAND")

    def test_explicit_receipt_bindings_only_are_not_full_checkpoints(self):
        row = receipt_row(bindings_before={"input": "old"}, bindings_after={"input": "new"})
        item = transition.export_transitions([row])["transitions"][0]
        self.assertEqual(item["before"]["value"], {"input": "old"})
        self.assertEqual(item["after"]["value"], {"input": "new"})
        self.assertEqual(item["before"]["scope"], "BINDINGS_ONLY")
        self.assertEqual(item["before"]["source"]["locator"], "/bindings_before")

    def test_failure_timeout_cancel_and_interruption_keep_cost_dimensions(self):
        rows = [receipt_row("r" + str(i), status, charged_allocation={"api_tokens": 42})
                for i, status in enumerate(["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"])]
        exported = transition.export_transitions(rows)["transitions"]
        self.assertEqual(len(exported), 5)
        for row, item in zip(rows, exported):
            receipt = json.loads(row["body"])
            self.assertEqual(item["run_status"], receipt["run_status"])
            self.assertEqual(item["costs"]["resources"]["value"], receipt["resources"])
            self.assertIsNone(item["costs"]["resources"]["value"]["gpu_seconds"]["measured"])
            self.assertEqual(item["costs"]["charged_allocation"]["value"], {"api_tokens": 42})
            self.assertEqual(item["costs"]["elapsed_ms"]["status"], "UNKNOWN")
            self.assertEqual(item["scientific_support"], "UNKNOWN")
            self.assertEqual(item["observation"]["value"]["exit_code"], 7)

    def test_missing_cost_is_not_zero_and_unknown_assessment_stays_unknown(self):
        body = dict(self.receipt)
        del body["resources"]
        del body["sha256"]
        row = receipt_row(**{key: value for key, value in body.items() if key not in {"run_id", "run_status"}})
        raw = json.loads(row["body"])
        del raw["resources"]
        del raw["sha256"]
        sha = object_digest(raw)
        row = {"run_id": raw["run_id"], "sha256": sha, "body": canonical({**raw, "sha256": sha})}
        item = transition.export_transitions([row])["transitions"][0]
        self.assertTrue(all(cell["status"] == "UNKNOWN" for cell in item["costs"].values()))
        self.assertEqual(item["observation"]["value"]["assessment"], {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN"})

    def test_prediction_and_provenance_are_decision_time_and_hash_located(self):
        item = transition.export_transitions(*self.retained())["transitions"][0]
        prediction = item["prior_prediction"]["value"]["prediction"]
        self.assertEqual(prediction["value"]["score"], 0.4)
        self.assertEqual(prediction["source"]["sha256"], self.before["sha"])
        self.assertEqual(prediction["source"]["locator"], "/decision/prediction")
        self.assertEqual(item["provenance"]["model"]["value"]["sha256"], "4" * 64)
        self.assertEqual(item["provenance"]["policy"]["source"]["kind"], "checkpoint")
        self.assertEqual(item["provenance"]["tool"]["value"]["executor_sha256"]["value"], "3" * 64)
        self.assertEqual(item["action"]["scope"], "SELECTED_ACTION")

    def test_after_forecast_cannot_replace_missing_before_forecast(self):
        before = json.loads(self.before["body"])
        del before["decision"]["prediction"]
        item = transition.export_transitions([receipt_row()], [reseal_checkpoint(before), self.after])["transitions"][0]
        self.assertEqual(item["prior_prediction"]["status"], "UNKNOWN")

    def test_candidate_forecast_remains_prior_and_preserves_its_model_identity(self):
        before = json.loads(self.before["body"])
        del before["decision"]["prediction"]
        before["decision"]["candidate"]["forecast"] = {"score": None, "model_sha256": "6" * 64}
        item = transition.export_transitions([receipt_row()], [reseal_checkpoint(before)])["transitions"][0]
        cell = item["prior_prediction"]["value"]["candidate/forecast"]
        self.assertIsNone(cell["value"]["score"])
        self.assertEqual(cell["value"]["model_sha256"], "6" * 64)
        self.assertEqual(cell["source"]["locator"], "/decision/candidate/forecast")

    def test_native_temporary_failure_receipt_and_checkpoints_export_without_ledger_changes(self):
        spec = importlib.util.spec_from_file_location("transition_native_fixture", ROOT / "tests/test_rds_owned_history.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fixture = module.OwnedHistoryTests("test_actual_failure_completion_retains_decision_time_evidence_and_live_cost")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        run, decision, before = fixture.reserve()
        receipt = fixture.store.execute(run["id"])
        self.assertEqual(receipt["run_status"], "FAILED")
        with fixture.store._db(True) as db:
            db.execute("BEGIN")
            receipt_rows = [dict(row) for row in db.execute("SELECT run_id,sha256,body FROM receipts ORDER BY run_id")]
            checkpoint_rows = [dict(row) for row in db.execute("SELECT id,sha,body FROM checkpoints ORDER BY id")]
            checkpoint_ids = [row["id"] for row in checkpoint_rows]
            # Original validator remains in the fixture caller, never in L2.
            for identity in checkpoint_ids:
                self.assertIsNotNone(module.read_checkpoint(db, identity, root=fixture.root))
            before_bytes = fixture.store.path.read_bytes()
            exported = transition.export_transitions(receipt_rows, checkpoint_rows)
            self.assertEqual(fixture.store.path.read_bytes(), before_bytes)
            self.assertEqual([dict(row) for row in db.execute("SELECT run_id,sha256,body FROM receipts ORDER BY run_id")], receipt_rows)
            self.assertEqual([dict(row) for row in db.execute("SELECT id,sha,body FROM checkpoints ORDER BY id")], checkpoint_rows)
        item = exported["transitions"][0]
        self.assertEqual(item["before"]["source"]["sha256"], before["sha256"])
        self.assertEqual(item["action"]["value"], decision["candidate"]["action"])
        self.assertEqual(item["after"]["scope"], "RETAINED_CHECKPOINT")
        self.assertEqual(item["costs"]["resources"]["value"], receipt["resources"])
        self.assertEqual(item["observation"]["value"], receipt)

    def test_receipt_original_codec_and_original_hash_reject_edits(self):
        with patch.object(transition, "decode_receipt", wraps=transition.decode_receipt) as decode:
            transition.export_transitions([receipt_row()])
            self.assertEqual(decode.call_count, 1)
        row = receipt_row()
        edited = json.loads(row["body"])
        edited["exit_code"] = 0
        row["body"] = canonical(edited)
        with self.assertRaisesRegex(ValueError, "recorded sha256"):
            transition.export_transitions([row])

    def test_checkpoint_raw_hash_and_contract_hash_reject_edits(self):
        row = {**self.before, "body": self.before["body"] + " "}
        with self.assertRaisesRegex(ValueError, "original hash mismatch"):
            transition.export_transitions([receipt_row()], [row])
        edited = json.loads(self.before["body"])
        edited["snapshot"]["contract"]["advisor_policy"]["schema"] = 2
        with self.assertRaisesRegex(ValueError, "contract hash mismatch"):
            transition.export_transitions([receipt_row()], [reseal_checkpoint(edited)])

    def test_noncanonical_spelling_is_valid_when_original_hash_matches(self):
        row = receipt_row(note="\u0394 fixture")
        receipt = json.loads(row["body"])
        row["body"] = json.dumps(receipt, ensure_ascii=True, indent=2)
        before = checkpoint_row(receipt)
        pretty = reseal_checkpoint(json.loads(before["body"]), pretty=True)
        item = transition.export_transitions([row], [pretty])["transitions"][0]
        self.assertEqual(item["before"]["source"]["sha256"], pretty["sha"])
        self.assertEqual(item["observation"]["value"]["note"], "\u0394 fixture")

    def test_duplicate_json_nonfinite_and_excessive_nesting_are_rejected(self):
        for raw in (self.before["body"][:-1] + ',"decision":{}}',
                    self.before["body"][:-1] + ',"extra":NaN}',
                    self.before["body"][:-1] + ',"extra":' + '[' * 40 + '0' + ']' * 40 + '}'):
            row = {**self.before, "sha": hashlib.sha256(raw.encode()).hexdigest(), "body": raw}
            with self.subTest(raw=raw[-70:]), self.assertRaises(ValueError):
                transition.export_transitions([receipt_row()], [row])
        row = receipt_row()
        row["body"] = row["body"][:-1] + ',"run_id":"r1"}'
        with self.assertRaisesRegex(ValueError, "repeats a JSON key"):
            transition.export_transitions([row])

    def test_resealed_cross_attempt_and_future_state_are_rejected(self):
        edits = []
        after = json.loads(self.after["body"])
        after["decision"]["execution"]["attempt_id"] = "other"
        edits.append(after)
        before = json.loads(self.before["body"])
        before["snapshot"]["runs"][0]["status"] = "FAILED"
        edits.append(before)
        before = json.loads(self.before["body"])
        before["snapshot"]["contract"]["advisor_policy"]["model"] = "wrong-original-policy"
        before["contract_sha256"] = object_digest(before["snapshot"]["contract"])
        edits.append(before)
        before = json.loads(self.before["body"])
        before["snapshot"]["receipts"] = [{"run_id": "r1", "sha256": self.receipt["sha256"]}]
        edits.append(before)
        for record in edits:
            with self.subTest(id=record["id"]), self.assertRaises(ValueError):
                transition.export_transitions([receipt_row()], [reseal_checkpoint(record)])

    def test_wrong_checkpoint_order_and_duplicate_identities_are_rejected(self):
        after = json.loads(self.after["body"])
        after["created_ns"] = 1
        with self.assertRaisesRegex(ValueError, "order conflicts"):
            transition.export_transitions([receipt_row()], [self.before, reseal_checkpoint(after)])
        with self.assertRaisesRegex(ValueError, "duplicate receipt"):
            transition.export_transitions([receipt_row(), receipt_row()])
        with self.assertRaisesRegex(ValueError, "duplicate checkpoint"):
            transition.export_transitions([receipt_row()], [self.before, self.before])

    def test_limits_reject_whole_export_and_do_not_drop_failures(self):
        with self.assertRaisesRegex(ValueError, "list limit"):
            transition.export_transitions([receipt_row()] * 129)
        with self.assertRaisesRegex(ValueError, "list limit"):
            transition.export_transitions([], [self.before] * 257)
        with self.assertRaisesRegex(ValueError, "max_row_bytes"):
            transition.export_transitions([receipt_row()], max_row_bytes=10)
        with self.assertRaisesRegex(ValueError, "max_output_bytes"):
            transition.export_transitions([receipt_row()], max_output_bytes=10)
        with patch.object(transition, "MAX_INPUT_BYTES", 10), self.assertRaisesRegex(ValueError, "max_input_bytes"):
            transition.export_transitions([receipt_row()])
        for cap in (0, -1, True, transition.MAX_ROW_BYTES + 1):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                transition.export_transitions([], max_row_bytes=cap)

    def test_maximum_input_depth_remains_serializable_after_export_wrapping(self):
        nested = 0
        for _ in range(31):
            nested = [nested]
        result = transition.export_transitions([receipt_row(extra=nested)])
        self.assertEqual(json.loads(transition.transition_json(result)), result)

    def test_empty_export_and_nonterminal_attempts(self):
        result = transition.export_transitions([])
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["transitions"], [])
        with self.assertRaisesRegex(ValueError, "terminal attempt"):
            transition.export_transitions([receipt_row(status="RUNNING")])

    def test_module_only_imports_stdlib_and_existing_pure_shared_codecs(self):
        tree = ast.parse((ROOT / "scripts/rds_transition.py").read_text(encoding="utf-8"))
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        self.assertLessEqual(imports, {"copy", "hashlib", "json", "math", "re", "rds_core", "rds_project_records"})
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id in {"open", "exec", "eval", "__import__"} for node in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
