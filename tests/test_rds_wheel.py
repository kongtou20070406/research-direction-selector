"""Offline wheel tests. Fake project responses are unit fixtures; one test uses real CLI receipts."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import rds_wheel as wheel_module
from rds_wheel import Wheel, canonical, classify, digest, read_json, rsi_gate, sha

spec = importlib.util.spec_from_file_location("wheel_fixture_prepare", REPO / "examples/wheel/prepare.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class FakeProject:
    """Labelled synthetic status/receipt fixture, never used by production wheel."""
    def __init__(self, root):
        self.root, self.calls = root, []
        self.contract, self.runs, self.receipts = None, {}, {}
        self.remaining = None

    def __call__(self, action, *args):
        assert action in {"init", "create", "execute", "status", "costs"}
        self.calls.append((action, *args))
        if action == "init":
            self.contract = read_json(args[1])
            return {"initialized": True}
        if action == "status":
            remaining = self.remaining if self.remaining is not None else self.contract["budget"]["wall_seconds"] - len(self.receipts) * .02
            return deepcopy({"contract": self.contract, "contract_sha256": digest(self.contract),
                             "runs": list(self.runs.values()), "receipts": list(self.receipts.values()),
                             "budget": {"wall_seconds": {"remaining": remaining}}})
        if action == "create":
            manifest = read_json(args[1])
            assert manifest["id"] not in self.runs, "duplicate run registration"
            assert manifest["argv"] in self.contract["allowed_commands"]
            self.runs[manifest["id"]] = {"id": manifest["id"], "manifest": manifest, "status": "RESERVED"}
            return self.runs[manifest["id"]]
        if action == "execute":
            run = self.runs[args[1]]
            assert args[1] not in self.receipts, "duplicate execution"
            manifest = run["manifest"]
            argv = manifest["argv"]
            token = argv[argv.index("--factor") + 1]
            row = read_json(self.root / "trusted/data.json")[token]
            row = row if isinstance(row, dict) else {"metric": row, "exit_code": 0}
            path = self.root / manifest["outpaths"][0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical({"loss": row.get("metric")}), encoding="utf-8")
            run["status"] = "FAILED" if row.get("exit_code", 0) else "SUCCEEDED"
            receipt = {"run_id": args[1], "arm": manifest["arm"], "control_id": manifest["control_id"],
                       "run_status": run["status"], "exit_code": row.get("exit_code", 0),
                       "manifest_sha256": digest(manifest), "ended_at": len(self.receipts) + 1,
                       "bindings_before": self.contract["bindings"], "bindings_after": self.contract["bindings"],
                       "protocol": read_json(self.root / manifest["protocol"]["path"]),
                       "artifacts": [{"kind": "project_output", "path": manifest["outpaths"][0], "sha256": sha(path)}]}
            receipt["sha256"] = digest(receipt)
            self.receipts[args[1]] = receipt
            return receipt
        return {"synthetic": True}


class WheelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rds-wheel-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"

    def prepare(self, *, factors=None, rows=None, bootstrap=True, direction="min"):
        self.project = FakeProject(self.root)
        self.wheel = fixture.prepare(self.root, project=self.project, factors=factors, data=rows, direction=direction)
        if bootstrap:
            fixture.bootstrap(self.wheel)
        self.project.calls.clear()
        return self.wheel

    def tick(self):
        with patch.object(wheel_module, "Wheel", return_value=self.wheel), patch("sys.stderr", new=io.StringIO()):
            return wheel_module.main(["--root", str(self.root)])

    def executed(self):
        return [call[-1] for call in self.project.calls if call[0] == "execute"]

    def row(self, factor="screen_same", proposer="researcher-a", **updates):
        row = {"proposal_id": str(uuid.uuid4()), "proposer_id": proposer, "kind": "factor", "factor": factor,
               "parent_contract_id": "synthetic-wheel-v1", "ts": "2026-10-08T00:00:00Z"}
        return {**row, **updates}

    def inbox(self, *rows):
        for row in rows:
            self.wheel.append("inbox.jsonl", canonical(row) + "\n")

    def test_unknown_does_not_execute(self):
        rows = read_json(fixture.HERE / "data.json")
        rows["seed"] = None
        self.prepare(rows=rows)
        self.assertEqual(self.tick(), 2)
        self.assertEqual(read_json(self.wheel.state_path("cells/treatment.json"))["state"], "UNKNOWN")
        self.assertEqual(self.executed(), [])
        self.assertFalse(any(c[0] == "create" for c in self.project.calls))

    def test_spin_appends_dead_and_skips_execute(self):
        rows = read_json(fixture.HERE / "data.json")
        rows["seed"] = 10
        self.prepare(rows=rows)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.wheel.tokens("dead.txt"), ["seed"])
        self.assertEqual(self.executed(), [])
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), ["flat"])
        self.assertEqual(self.wheel.tokens("dead.txt"), ["seed"])

    def test_changed_cell_creates_one_manifest(self):
        self.prepare()
        self.assertEqual(self.tick(), 0)
        manifests = list(self.wheel.state_path("manifests").glob("*.json"))
        self.assertEqual(len(manifests), 1)
        manifest = read_json(manifests[0])
        self.assertEqual(manifest["id"], "flat")
        self.assertEqual(self.executed(), ["flat"])
        protocol = read_json(self.root / manifest["protocol"]["path"])
        self.assertEqual(protocol["evaluator_sha256"], self.wheel.contract["evaluator_sha256"])
        self.assertEqual(manifest["argv"].count("--factor"), 1)

    def test_inbox_row_cannot_execute_before_screen(self):
        self.prepare(factors=["flat"])
        row = self.row("screen_change")
        self.inbox(row)
        original = self.wheel.state_path("inbox.jsonl").read_bytes()
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), ["flat"])
        self.assertFalse(self.wheel.state_path("screen").exists())
        self.assertEqual(self.tick(), 0)  # flat is dead, no execute in this tick
        self.assertEqual(self.executed(), ["flat"])
        self.assertEqual(self.tick(), 0)  # now screen, without admitting to factors
        self.assertEqual(self.executed(), ["flat", "screen_screen_change"])
        self.assertNotIn("screen_change", self.wheel.tokens("factors.txt"))
        self.assertEqual(self.tick(), 0)  # receipt changes FALSE -> TRUE
        self.assertIn("screen_change", self.wheel.tokens("factors.txt"))
        self.assertEqual(read_json(self.wheel.state_path("quota.json")), {"researcher-a": 2})
        self.assertEqual(self.wheel.state_path("inbox.jsonl").read_bytes(), original)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), ["flat", "screen_screen_change", "screen_change"])

    def test_quota_halves_on_unchanged_screen(self):
        self.prepare(factors=[])
        self.inbox(self.row(proposer="persistent-proposer"))
        original = self.wheel.state_path("inbox.jsonl").read_bytes()
        self.wheel.write("quota.json", {"persistent-proposer": 2, "another-proposer": 1})
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(read_json(self.wheel.state_path("quota.json")), {"persistent-proposer": 1, "another-proposer": 1})
        self.assertEqual(self.wheel.tokens("factors.txt"), [])
        self.assertEqual(self.wheel.state_path("inbox.jsonl").read_bytes(), original)
        self.assertEqual(self.tick(), 0)
        self.assertTrue(self.wheel.state_path("pause.json").exists())
        self.assertEqual(len(self.executed()), 1)

    def test_pause_is_terminal(self):
        self.prepare()
        self.wheel.pause("operator fixture")
        before = {p.relative_to(self.wheel.directory): p.read_bytes() for p in self.wheel.directory.rglob("*") if p.is_file()}
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.project.calls, [])
        after = {p.relative_to(self.wheel.directory): p.read_bytes() for p in self.wheel.directory.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_screen_quota_commit_recovers_once_after_interruption(self):
        self.prepare(factors=[])
        self.inbox(self.row())
        self.wheel.write("quota.json", {"researcher-a": 2})
        self.assertEqual(self.tick(), 0)
        write = self.wheel.write

        def interrupted(name, value, **kwargs):
            write(name, value, **kwargs)
            if name == "quota.json":
                raise OSError("simulated interruption after quota replacement")

        with patch.object(self.wheel, "write", side_effect=interrupted):
            self.assertEqual(self.tick(), 2)
        self.assertEqual(read_json(self.wheel.state_path("quota.json")), {"researcher-a": 1})
        self.assertEqual(self.tick(), 0)
        self.assertEqual(read_json(self.wheel.state_path("quota.json")), {"researcher-a": 1})
        self.assertEqual(self.executed(), ["screen_screen_same"])
        transitions = self.wheel.state_path("transitions.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(transitions), 2)

    def test_changed_zero_screen_records_admission_but_dead_prevents_execution(self):
        rows = read_json(fixture.HERE / "data.json")
        rows.update(seed=0, screen_same=10)
        self.prepare(factors=[], rows=rows)
        self.inbox(self.row())
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), ["screen_screen_same"])
        self.assertEqual(self.wheel.tokens("factors.txt"), ["screen_same"])
        self.assertEqual(self.wheel.tokens("dead.txt"), ["screen_same"])
        self.assertEqual(read_json(self.wheel.state_path("quota.json")), {"researcher-a": 2})
        self.assertEqual(self.tick(), 0)
        self.assertTrue(self.wheel.state_path("pause.json").exists())
        self.assertEqual(self.executed(), ["screen_screen_same"])

    def duplicate_screen_case(self, same_proposer, interrupt):
        self.prepare(factors=[])
        self.wheel.write("quota.json", {"first": 2})
        duplicate_proposer = "first" if same_proposer else "second"
        self.inbox(self.row(proposer="first"), self.row(proposer=duplicate_proposer),
                   self.row("screen_change", proposer="later"))
        original_inbox = self.wheel.state_path("inbox.jsonl").read_bytes()
        self.assertEqual(self.tick(), 0)
        original_receipt = deepcopy(self.project.receipts["screen_screen_same"])
        if interrupt:
            write = self.wheel.write

            def interrupted(name, value, **kwargs):
                write(name, value, **kwargs)
                if name == "quota.json":
                    raise OSError("interrupted screen receipt commit")

            with patch.object(self.wheel, "write", side_effect=interrupted):
                self.assertEqual(self.tick(), 2)
        else:
            self.assertEqual(self.tick(), 0)
        for _ in range(6):
            self.assertEqual(self.tick(), 0)
            if self.wheel.state_path("pause.json").exists():
                break
        self.assertTrue(self.wheel.state_path("pause.json").exists())
        self.assertEqual(self.executed(), ["screen_screen_same", "screen_screen_change", "screen_change"])
        self.assertEqual(read_json(self.wheel.state_path("quota.json")), {"first": 1, "later": 2})
        self.assertEqual(self.wheel.state_path("inbox.jsonl").read_bytes(), original_inbox)
        self.assertEqual(self.project.receipts["screen_screen_same"], original_receipt)
        self.assertIsNone(read_json(self.wheel.state_path("state.json"))["pending"])

    def test_duplicate_screen_from_another_proposer_does_not_block_later_mapping(self):
        self.duplicate_screen_case(same_proposer=False, interrupt=False)

    def test_duplicate_screen_with_positive_quota_recovers_after_interruption(self):
        self.duplicate_screen_case(same_proposer=True, interrupt=True)

    def test_known_mapping_collision_is_rejected_before_pending_intent(self):
        self.prepare()
        manifest = self.wheel.manifest("flat", "main")
        self.wheel.write("manifests/flat.json", manifest, once=True)
        state = read_json(self.wheel.state_path("state.json"))
        before = {name: self.wheel.state_path(name).read_bytes() for name in ("state.json", "factors.txt")}
        with self.assertRaises(wheel_module.WheelError):
            self.wheel.dispatch(state, manifest, "flat", "main", self.project("status"))
        self.assertIsNone(state["pending"])
        self.assertEqual(before, {name: self.wheel.state_path(name).read_bytes() for name in before})
        self.assertEqual(self.executed(), [])

    def test_ambiguous_execute_is_not_resent(self):
        self.prepare()
        original = self.wheel.project

        def interrupted(action, *args):
            if action == "execute":
                raise OSError("delivery unknown")
            return original(action, *args)

        with patch.object(self.wheel, "project", side_effect=interrupted):
            self.assertEqual(self.tick(), 2)
        self.assertEqual(self.tick(), 4)
        self.assertEqual(self.executed(), [])
        self.assertEqual(sum(c[0] == "create" for c in self.project.calls), 1)
        self.assertEqual(read_json(self.wheel.state_path("state.json"))["pending"]["factor"], "flat")

    def test_receiptless_failed_cli_result_stays_an_operation_error(self):
        self.prepare()
        response = subprocess.CompletedProcess([], 1, b'{"receipt":null}', b'')
        with patch.object(wheel_module.subprocess, "run", return_value=response):
            with self.assertRaises(wheel_module.WheelError) as error:
                self.wheel._project("execute", "--id", "flat")
        self.assertEqual(error.exception.code, 2)

    def test_receipt_artifact_tampering_is_unknown(self):
        self.prepare()
        (self.root / "outputs/treatment.json").write_text('{"loss":0}', encoding="utf-8")
        self.assertEqual(self.tick(), 2)
        self.assertEqual(self.executed(), [])

    def test_receipt_from_a_different_manifest_is_unknown(self):
        self.prepare()
        receipt = self.project.receipts["treatment"]
        receipt["manifest_sha256"] = "a" * 64
        receipt["sha256"] = digest({k: v for k, v in receipt.items() if k != "sha256"})
        self.assertEqual(self.tick(), 2)
        self.assertEqual(self.executed(), [])

    def test_evaluator_hash_mismatch_exits_3(self):
        self.prepare()
        with (self.root / "trusted/evaluator.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# changed evaluator\n")
        self.assertEqual(self.tick(), 3)
        self.assertEqual(self.executed(), [])

    def test_missing_tick_zero_receipts_exit_4(self):
        self.prepare(bootstrap=False)
        self.assertEqual(self.tick(), 4)
        self.assertEqual(self.executed(), [])

    def test_exit_code_does_not_classify_the_cell(self):
        rows = read_json(fixture.HERE / "data.json")
        rows["seed"] = {"metric": 0, "exit_code": 7}
        self.prepare(rows=rows)
        self.assertEqual(self.project.receipts["treatment"]["exit_code"], 7)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(read_json(self.wheel.state_path("cells/treatment.json"))["state"], "TRUE")

    def test_running_arm_is_idle(self):
        self.prepare()
        self.project.runs["treatment"]["status"] = "RUNNING"
        before = self.wheel.state_path("state.json").read_bytes()
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), [])
        self.assertEqual(before, self.wheel.state_path("state.json").read_bytes())

    def test_threshold_cannot_move_after_receipt(self):
        self.prepare()
        changed = {**self.wheel.contract, "threshold": 0}
        self.wheel.write("contract.json", changed)
        self.assertEqual(self.tick(), 3)
        self.assertEqual(self.executed(), [])

    def test_missing_mapping_exits_2_and_keeps_inbox(self):
        self.prepare(factors=[])
        self.inbox(self.row("unmapped"))
        before = self.wheel.state_path("inbox.jsonl").read_bytes()
        self.assertEqual(self.tick(), 2)
        self.assertEqual(self.executed(), [])
        self.assertEqual(before, self.wheel.state_path("inbox.jsonl").read_bytes())

    def test_screen_budget_is_remaining_tenth_and_quota_zero_is_skipped(self):
        self.prepare(factors=[])
        self.project.remaining = 2.5
        self.wheel.write("quota.json", {"spent": 0})
        self.inbox(self.row("screen_change", "spent"), self.row("screen_same", "available"))
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), ["screen_screen_same"])
        manifest = read_json(self.wheel.state_path("screen/screen_screen_same.json"))
        self.assertEqual(manifest["timeout_seconds"], .25)
        self.assertEqual(manifest["resource_estimates"]["wall_seconds"], .25)

    def test_zero_budget_pauses_without_execution(self):
        self.prepare(factors=[])
        self.project.remaining = 0
        self.inbox(self.row())
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), [])
        self.assertTrue(self.wheel.state_path("pause.json").exists())

    def test_frozen_contract_classification_is_exact_and_unknown_is_not_false(self):
        self.assertEqual(classify("a", "x", .1, .3, .2, "max")["state"], "TRUE")
        self.assertEqual(classify("a", "x", .3, .1, .2, "min")["state"], "TRUE")
        for left, right in ((None, None), (1, None), (float("nan"), 0), (True, 0)):
            self.assertEqual(classify("a", "x", left, right, 0, "min")["state"], "UNKNOWN")

    def test_model_handoff_is_after_two_ticks_and_never_inside_tick(self):
        self.prepare(factors=["flat"])
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.wheel.prompt()
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.tick(), 0)
        expected = self.wheel.state_path("dead.txt").read_bytes() + self.wheel.state_path("cells/flat.json").read_bytes()
        self.assertEqual(self.wheel.prompt(), expected)
        self.assertFalse(self.wheel.propose("same-proposer", "two tokens\n"))
        self.assertFalse(self.wheel.propose("same-proposer", " flat\n"))
        self.assertTrue(self.wheel.propose("same-proposer", "screen_change\n"))
        row = read_json(self.wheel.state_path("inbox.jsonl"))
        self.assertEqual(row["proposer_id"], "same-proposer")
        self.assertEqual(row["factor"], "screen_change")
        self.assertEqual(self.executed(), ["flat"])

    def test_three_factor_fixture_reaches_pause_and_inbox_does_not_drive_execution(self):
        self.prepare()
        self.inbox(*(self.row("flat", parent_contract_id="wrong-contract") for _ in range(8)))
        inbox = self.wheel.state_path("inbox.jsonl").read_bytes()
        for _ in range(12):
            self.assertEqual(self.tick(), 0)
            if self.wheel.state_path("pause.json").exists():
                break
        self.assertTrue(self.wheel.state_path("pause.json").exists())
        self.assertEqual(self.wheel.tokens("dead.txt"), ["flat", "plateau"])
        transitions = [json.loads(line) for line in self.wheel.state_path("transitions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(self.executed()), sum(row["changed"] for row in transitions))
        self.assertEqual(len(self.executed()), 3)
        self.assertEqual(inbox, self.wheel.state_path("inbox.jsonl").read_bytes())
        self.assertEqual(read_json(self.wheel.state_path("band.json")), {"width": "narrow"})
        self.assertEqual(read_json(self.wheel.state_path("rsi.json"))["regret"], "UNKNOWN")

    def test_rsi_gate_does_not_edit_rules_or_invent_unpaired_regret(self):
        old = {"violations": 0, "spin_count": 3, "budget_to_true_cell": 10}
        new = {"violations": 0, "spin_count": 2, "budget_to_true_cell": 7}
        self.assertEqual(rsi_gate(old, new, 2), {"adoption_eligible": True, "regret": "UNKNOWN",
                                                "rule_edits": False, "scientific_accuracy_gain": "UNKNOWN"})
        self.assertFalse(rsi_gate(old, {**new, "violations": 1}, 2)["adoption_eligible"])
        self.assertFalse(rsi_gate(old, new, 4)["adoption_eligible"])
        self.assertEqual(rsi_gate(old, new, 2, {"regret": 0})["regret"], "UNKNOWN")


class RealWheelTests(unittest.TestCase):
    def test_real_project_cli_fixture_uses_original_receipts_until_pause(self):
        with tempfile.TemporaryDirectory(prefix="rds-wheel-cli-") as directory:
            root = Path(directory) / "project"
            result = subprocess.run([sys.executable, "-B", str(REPO / "examples/wheel/run.py"), "--root", str(root)],
                                    capture_output=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            report = json.loads(result.stdout)
            self.assertTrue(report["paused"])
            self.assertEqual(report["dead"], ["flat", "plateau"])
            self.assertEqual(report["execute_count_after_tick_0"], report["changed_cells"])
            self.assertEqual(len(report["receipts"]), 5)
            self.assertEqual(report["scientific_accuracy_gain"], "UNKNOWN")
            self.assertTrue(all(len(r["sha256"]) == 64 for r in report["receipts"]))


if __name__ == "__main__":
    unittest.main()
