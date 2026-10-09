"""Offline wheel tests. Fake project responses are unit fixtures; real CLI tests verify kernel behavior."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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

    def test_frozen_template_id_collisions_are_rejected_before_initialization(self):
        original_initialize = Wheel.initialize
        selectors = [(('initial',), ('control',)),
                     (('initial',), ('routes', 'flat', 'main')),
                     (('routes', 'flat', 'screen'), ('control',)),
                     (('routes', 'flat', 'screen'), ('initial',)),
                     (('routes', 'flat', 'screen'), ('routes', 'benefit', 'screen')),
                     (('routes', 'flat', 'screen'), ('routes', 'benefit', 'main'))]
        for index, (changed, existing) in enumerate(selectors):
            with self.subTest(changed=changed, existing=existing):
                root = Path(self.temp.name) / ('collision-' + str(index))
                project = FakeProject(root)

                def collision(wheel, contract_path):
                    contract = read_json(contract_path)
                    config = root / 'trusted/wheel-setup.json'
                    setup = read_json(config)
                    target, other = setup, setup
                    for key in changed:
                        target = target[key]
                    for key in existing:
                        other = other[key]
                    target['id'] = other['id']
                    config.write_text(canonical(setup), encoding='utf-8')
                    protocol_path = root / 'trusted/protocol.json'
                    protocol = read_json(protocol_path)
                    protocol['config_sha256'] = sha(config)
                    protocol_path.write_text(canonical(protocol), encoding='utf-8')
                    for binding in contract['bindings']:
                        binding['sha256'] = sha(root / binding['path'])
                    contract_path.write_text(canonical(contract), encoding='utf-8')
                    return original_initialize(wheel, contract_path)

                with patch.object(Wheel, 'initialize', autospec=True, side_effect=collision):
                    with self.assertRaisesRegex(wheel_module.WheelError, 'mapped run IDs must be distinct'):
                        fixture.prepare(root, project=project)
                self.assertFalse((root / '.rds/wheel').exists())
                self.assertEqual(project.calls, [], 'no project initialization or dispatch before rejection')

    def test_hash_bound_non_object_metric_is_unknown_without_new_execution(self):
        for index, value in enumerate((None, [], [1], 1, 'metric', True)):
            with self.subTest(value=value):
                self.root = Path(self.temp.name) / ('metric-' + str(index))
                self.prepare()
                path = self.root / 'outputs/treatment.json'
                path.write_text(canonical(value), encoding='utf-8')
                receipt = self.project.receipts['treatment']
                receipt['artifacts'][0]['sha256'] = sha(path)
                receipt['sha256'] = digest({k: v for k, v in receipt.items() if k != 'sha256'})
                quota = self.wheel.state_path('quota.json').read_bytes()
                self.assertEqual(self.tick(), 2)
                self.assertEqual(read_json(self.wheel.state_path('cells/treatment.json'))['state'], 'UNKNOWN')
                self.assertEqual(self.executed(), [])
                self.assertEqual(self.wheel.state_path('quota.json').read_bytes(), quota)
                self.assertIsNone(read_json(self.wheel.state_path('state.json'))['pending'])

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

    def test_metric_parses_the_same_original_bytes_it_hashes(self):
        self.prepare()
        receipt = self.project.receipts['treatment']
        # Temp directory short names on Windows can resolve to another spelling.
        # Exercise an equivalent spelling on every platform and match file identity.
        target = (self.root / 'outputs' / '..' / receipt['artifacts'][0]['path']).resolve()
        original = target.read_bytes()
        reads = []
        read_bytes = Path.read_bytes
        read_text = Path.read_text
        def changing_read(path):
            if path.resolve() == target:
                reads.append(path)
                return original if len(reads) == 1 else b'{"loss":-1000}'
            return read_bytes(path)
        def changed_text(path, *args, **kwargs):
            return '{"loss":-1000}' if path.resolve() == target else read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_bytes', autospec=True, side_effect=changing_read), \
                patch.object(Path, 'read_text', autospec=True, side_effect=changed_text):
            value = self.wheel.metric(receipt, self.wheel.manifest('seed', 'initial'))
        self.assertEqual(value, json.loads(original)['loss'])
        self.assertEqual(len(reads), 1)

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

    def test_rsi_decimal_boundary_is_exact_without_epsilon(self):
        old = {"violations": 0, "spin_count": 3, "budget_to_true_cell": .3}
        for new_value, expected in ((.1, True), (.10000000000000002, False), (.09999999999999999, True)):
            with self.subTest(new_value=new_value):
                result = rsi_gate(old, {**old, "spin_count": 2, "budget_to_true_cell": new_value}, .2)
                self.assertEqual(result["adoption_eligible"], expected)
                self.assertEqual(result["regret"], "UNKNOWN")

    def handoff_ready(self):
        self.prepare(factors=["flat"])
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.tick(), 0)

    def test_proposal_inbox_limits_preserve_originals_and_quota(self):
        self.handoff_ready()
        inbox = self.wheel.state_path("inbox.jsonl")
        quota = self.wheel.state_path("quota.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "proposer_id"):
            self.wheel.propose("汉" * 1000, "screen_change")
        self.assertEqual(inbox.read_bytes(), b'')
        with patch.object(wheel_module, "MAX_INBOX_ROWS", 2):
            self.assertTrue(self.wheel.propose("one", "screen_change"))
            self.assertTrue(self.wheel.propose("two", "screen_change"))
            before = inbox.read_bytes()
            with self.assertRaisesRegex(ValueError, "inbox"):
                self.wheel.propose("three", "screen_change")
            self.assertEqual(inbox.read_bytes(), before)
        with patch.object(wheel_module, "MAX_INBOX_BYTES", len(before)):
            with self.assertRaisesRegex(ValueError, "inbox"):
                self.wheel.propose("four", "screen_change")
            self.assertEqual(inbox.read_bytes(), before)
        self.assertEqual(self.wheel.state_path("quota.json").read_bytes(), quota)
        self.assertEqual(self.executed(), ["flat"])

    def test_oversized_and_deep_inbox_scan_is_bounded_without_execution(self):
        self.prepare(factors=[])
        inbox = self.wheel.state_path("inbox.jsonl")
        inbox.write_bytes(b'x' * (1024 * 1024 + 1))
        before = inbox.read_bytes()
        self.assertEqual(self.tick(), 2)
        self.assertEqual(inbox.read_bytes(), before)
        self.assertEqual(self.executed(), [])
        # A bounded malformed row must not obstruct a later valid proposal.
        inbox.write_text('[' * 1500 + '0' + ']' * 1500 + '\n', encoding='utf-8')
        self.inbox(self.row('screen_change'))
        before = inbox.read_bytes()
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), ['screen_screen_change'])
        self.assertEqual(inbox.read_bytes(), before)

    def test_accepted_proposal_after_partial_append_keeps_prefix_and_is_selectable(self):
        self.handoff_ready()
        inbox = self.wheel.state_path('inbox.jsonl')
        partial = b'{"partial":'
        inbox.write_bytes(partial)
        # The separator is part of the append allowance, never a rewrite.
        with patch.object(wheel_module, 'MAX_INBOX_BYTES', len(partial) + 1):
            with self.assertRaisesRegex(ValueError, 'inbox'):
                self.wheel.propose('one', 'screen_change')
            self.assertEqual(inbox.read_bytes(), partial)
        self.assertTrue(self.wheel.propose('one', 'screen_change'))
        self.assertTrue(inbox.read_bytes().startswith(partial))
        state = read_json(self.wheel.state_path('state.json'))
        selected = self.wheel.eligible_proposal(state, self.project('status'))
        self.assertIsNotNone(selected)
        self.assertEqual(selected['proposer_id'], 'one')
        self.assertEqual(selected['factor'], 'screen_change')
        self.assertEqual(self.executed(), ['flat'])

    def test_non_string_proposal_ids_are_skipped_without_consuming_quota(self):
        self.prepare(factors=[])
        invalid = [self.row(proposal_id=value) for value in ([], 7, {}, None, True)]
        valid = self.row(factor='screen_change')
        self.inbox(*invalid, valid)
        before = self.wheel.state_path('inbox.jsonl').read_bytes()
        quota = self.wheel.state_path('quota.json').read_bytes()
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.executed(), ['screen_screen_change'])
        pending = read_json(self.wheel.state_path('state.json'))['pending']
        self.assertEqual(pending['proposal'], valid)
        self.assertEqual(self.wheel.state_path('inbox.jsonl').read_bytes(), before)
        self.assertEqual(self.wheel.state_path('quota.json').read_bytes(), quota)


class RealWheelTests(unittest.TestCase):
    def crash_dispatch(self, root, boundary):
        # Each hook exits a separate process after the actual persisted/native operation.
        code = '''
import os, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / 'scripts'))
import rds_wheel as w
old = os.environ.get('RDS_WHEEL_TEST_SOURCE_COMMIT')
if old:
    source = subprocess.check_output(['git', 'show', old + ':scripts/rds_wheel.py'], cwd=sys.argv[1])
    exec(compile(source, w.__file__, 'exec'), w.__dict__)
wheel = w.Wheel(sys.argv[2])
boundary = sys.argv[3]
commit, write, project = wheel.commit, wheel.write, wheel.project
def committed(state, effects):
    result = commit(state, effects)
    if boundary == 'commit' and state.get('pending'):
        os._exit(73)
    return result
def written(name, value, **kwargs):
    result = write(name, value, **kwargs)
    if boundary == 'manifest' and name == 'manifests/flat.json':
        os._exit(73)
    return result
def native(action, *args):
    result = project(action, *args)
    if boundary == 'create' and action == 'create':
        os._exit(73)
    return result
wheel.commit, wheel.write, wheel.project = committed, written, native
wheel.tick()
raise AssertionError('crash boundary was not reached')
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(REPO), str(root), boundary],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr.decode('utf-8', errors='replace'))

    def test_native_dispatch_crash_resumes_only_unstarted_owned_run_once(self):
        for boundary in ('commit', 'manifest', 'create'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory(prefix='rds-wheel-dispatch-') as directory:
                wheel = fixture.prepare(Path(directory) / 'project', factors=['flat'])
                fixture.bootstrap(wheel)
                bootstrap = wheel.project('status')
                frozen = {r['run_id']: canonical(r) for r in bootstrap['receipts']}
                inputs = {p: p.read_bytes() for p in wheel.root.glob('*.json')}
                inputs.update({p: p.read_bytes() for p in (wheel.root / 'trusted').iterdir()})
                self.crash_dispatch(wheel.root, boundary)
                crashed = wheel.project('status')
                self.assertEqual(crashed['receipts'], bootstrap['receipts'])
                state = read_json(wheel.state_path('state.json'))
                self.assertEqual(state['pending']['manifest']['id'], 'flat')
                self.assertFalse(state['pending']['submitted'])
                preserved = {name: wheel.state_path(name).read_bytes()
                             for name in ('factors.txt', 'inbox.jsonl', 'quota.json')}
                self.assertEqual(wheel.tick(), 0)
                completed = wheel.project('status')
                runs = [r for r in completed['runs'] if r['id'] == 'flat']
                receipts = [r for r in completed['receipts'] if r['run_id'] == 'flat']
                self.assertEqual(len(runs), 1)
                self.assertEqual(len(receipts), 1)
                self.assertEqual(runs[0]['status'], 'COMPLETED')
                self.assertEqual(runs[0]['attempt_id'], receipts[0]['attempt_id'])
                self.assertTrue(runs[0]['attempt_id'])
                from rds_project import ProjectStore
                with ProjectStore(wheel.root)._db(True) as db:
                    finishes = db.execute("SELECT COUNT(*) FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                                          "AND json_extract(body,'$.run_id')='flat'").fetchone()[0]
                self.assertEqual(finishes, 1)
                self.assertEqual(frozen, {r['run_id']: canonical(r) for r in completed['receipts']
                                          if r['run_id'] in frozen})
                self.assertEqual(preserved, {name: wheel.state_path(name).read_bytes() for name in preserved})
                self.assertEqual(inputs, {p: p.read_bytes() for p in inputs})
                self.assertEqual(completed['budget']['wall_seconds']['cap'], 90)
                self.assertGreater(completed['budget']['wall_seconds']['spent_measured'],
                                   crashed['budget']['wall_seconds']['spent_measured'])
                self.assertEqual(wheel.tick(), 0)
                self.assertEqual(wheel.project('status')['receipts'], completed['receipts'])
                self.assertEqual(wheel.project('status')['budget'], completed['budget'])

    def test_native_attempt_and_receiptless_terminal_observation_never_redispatch(self):
        for boundary in ('attempt', 'terminal', 'missing_contract'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory(prefix='rds-wheel-attempt-') as directory:
                wheel = fixture.prepare(Path(directory) / 'project', factors=['flat'])
                fixture.bootstrap(wheel)
                self.crash_dispatch(wheel.root, 'create')
                if boundary == 'attempt':
                    code = '''
import os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / 'scripts'))
from rds_project import ProjectStore
ProjectStore._execute_claim = lambda *args, **kwargs: os._exit(73)
ProjectStore(sys.argv[2]).execute('flat')
'''
                    result = subprocess.run([sys.executable, '-B', '-c', code, str(REPO), str(wheel.root)],
                                            capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 73, result.stderr.decode('utf-8', errors='replace'))
                elif boundary == 'terminal':
                    wheel.project('execute', '--id', 'flat')
                actual = wheel.project('status')
                run = next(r for r in actual['runs'] if r['id'] == 'flat')
                if boundary == 'missing_contract':
                    self.assertIsNone(run['attempt_id'])
                else:
                    self.assertTrue(run['attempt_id'])
                observation = deepcopy(actual)
                observation['receipts'] = [r for r in observation['receipts'] if r['run_id'] != 'flat']
                if boundary == 'missing_contract':
                    # Unknown observed ownership is not a current-contract binding.
                    # The actual native row and its unstarted attempt remain intact.
                    next(r for r in observation['runs'] if r['id'] == 'flat').pop('effective_contract_sha256')
                retained = {p: p.read_bytes() for p in wheel.directory.rglob('*') if p.is_file()}
                original = wheel.project
                calls = []
                def observed(action, *args):
                    calls.append((action, *args))
                    return observation if action == 'status' else original(action, *args)
                with patch.object(wheel, 'project', side_effect=observed):
                    with self.assertRaises(wheel_module.WheelError) as error:
                        wheel.tick()
                    self.assertEqual(error.exception.code, 4)
                self.assertFalse(any(c[0] in ('create', 'execute') for c in calls))
                self.assertEqual(wheel.project('status'), actual)
                self.assertEqual(retained, {p: p.read_bytes() for p in retained})

    def test_native_screen_crash_preserves_dynamic_allowance_and_settles_quota_once(self):
        with tempfile.TemporaryDirectory(prefix='rds-wheel-screen-') as directory:
            wheel = fixture.prepare(Path(directory) / 'project', factors=[])
            fixture.bootstrap(wheel)
            row = {'proposal_id': str(uuid.uuid4()), 'proposer_id': 'researcher-a', 'kind': 'factor',
                   'factor': 'screen_change', 'parent_contract_id': wheel.contract['contract_id'],
                   'ts': '2026-10-08T00:00:00Z'}
            wheel.append('inbox.jsonl', canonical(row) + '\n')
            wheel.write('quota.json', {'researcher-a': 1})
            original_inbox = wheel.state_path('inbox.jsonl').read_bytes()
            original_quota = wheel.state_path('quota.json').read_bytes()
            self.crash_dispatch(wheel.root, 'create')
            state = read_json(wheel.state_path('state.json'))
            pending = state['pending']
            self.assertEqual(pending['kind'], 'screen')
            self.assertEqual(pending['proposal'], row)
            allowance = pending['manifest']['resource_estimates']['wall_seconds']
            self.assertGreater(allowance, 0)
            self.assertLessEqual(allowance * 1000, wheel.contract['screen_wall_ms'])
            snapshot = wheel.project('status')
            owned = next(r for r in snapshot['runs'] if r['id'] == 'screen_screen_change')
            self.assertEqual(owned['status'], 'RESERVED')
            self.assertIsNone(owned['attempt_id'])
            self.assertEqual(owned['manifest'], pending['manifest'])
            self.assertEqual(wheel.tick(), 0)
            completed = wheel.project('status')
            receipt = next(r for r in completed['receipts'] if r['run_id'] == 'screen_screen_change')
            run = next(r for r in completed['runs'] if r['id'] == 'screen_screen_change')
            self.assertEqual(run['attempt_id'], receipt['attempt_id'])
            self.assertEqual(run['resource_estimates']['wall_seconds'], allowance)
            self.assertEqual(wheel.state_path('inbox.jsonl').read_bytes(), original_inbox)
            self.assertEqual(wheel.state_path('quota.json').read_bytes(), original_quota)
            self.assertEqual(wheel.tick(), 0)
            self.assertEqual(read_json(wheel.state_path('quota.json')), {'researcher-a': 2})
            self.assertEqual(read_json(wheel.state_path('state.json'))['screened'], [row['proposal_id']])
            self.assertEqual(wheel.tick(), 0)
            self.assertEqual(read_json(wheel.state_path('quota.json')), {'researcher-a': 2})
            self.assertEqual(wheel.state_path('inbox.jsonl').read_bytes(), original_inbox)
            # The next ordinary tick may dispatch the newly admitted MAIN arm.
            # It must not replay SCREEN or apply the proposal quota a second time.
            after = wheel.project('status')
            self.assertEqual(next(r for r in after['receipts'] if r['run_id'] == 'screen_screen_change'), receipt)
            self.assertEqual(read_json(wheel.state_path('state.json'))['screened'], [row['proposal_id']])
            from rds_project import ProjectStore
            with ProjectStore(wheel.root)._db(True) as db:
                finishes = db.execute("SELECT COUNT(*) FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                                      "AND json_extract(body,'$.run_id')='screen_screen_change'").fetchone()[0]
            self.assertEqual(finishes, 1)

    def test_mutable_data_paths_are_rejected_before_native_initialization(self):
        initialize = Wheel.initialize
        class DataRejected(Exception):
            pass
        for relative in ('agent/data.json', 'outputs/data.json', 'inbox/data.json', '.rds/wheel/data.json'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(prefix='rds-wheel-data-') as directory:
                root = Path(directory) / 'project'
                def relocate(wheel, contract_path):
                    contract = read_json(contract_path)
                    setup_path = root / 'trusted/wheel-setup.json'
                    setup = read_json(setup_path)
                    data = root / relative
                    data.parent.mkdir(parents=True, exist_ok=True)
                    data.write_bytes((root / 'trusted/data.json').read_bytes())
                    setup['data_path'] = relative
                    # Isolate explicit agent, output, inbox and wheel roots.
                    setup['agent_writable_roots'] = ['agent']
                    for template in [setup['control'], setup['initial']] + [t for r in setup['routes'].values() for t in r.values()]:
                        template['argv'][template['argv'].index('--data') + 1] = relative
                    setup_path.write_text(canonical(setup), encoding='utf-8')
                    protocol_path = root / 'trusted/protocol.json'
                    protocol = read_json(protocol_path)
                    protocol['config_sha256'] = sha(setup_path)
                    protocol_path.write_text(canonical(protocol), encoding='utf-8')
                    for binding in contract['bindings']:
                        if binding['role'] == 'data':
                            binding['path'] = relative
                        binding['sha256'] = sha(root / binding['path'])
                    contract['allowed_commands'] = [t['argv'] for t in [setup['control'], setup['initial']]
                                                   + [t for r in setup['routes'].values() for t in r.values()]]
                    contract_path.write_text(canonical(contract), encoding='utf-8')
                    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
                    with self.assertRaisesRegex(wheel_module.WheelError, 'data slice is in an agent-writable tree'):
                        initialize(wheel, contract_path)
                    self.assertEqual(before, {p: p.read_bytes() for p in before})
                    self.assertFalse((root / '.rds/project.sqlite3').exists())
                    self.assertFalse(wheel.state_path('state.json').exists())
                    raise DataRejected
                with patch.object(Wheel, 'initialize', autospec=True, side_effect=relocate):
                    with self.assertRaises(DataRejected):
                        fixture.prepare(root)
        with tempfile.TemporaryDirectory(prefix='rds-wheel-trusted-') as directory:
            wheel = fixture.prepare(Path(directory) / 'project')
            self.assertEqual(wheel.project('status')['runs'], [])
            self.assertEqual(read_json(wheel.state_path('state.json'))['ticks'], 0)

    def test_concurrent_initializer_keeps_one_stable_lock_and_publication(self):
        with tempfile.TemporaryDirectory(prefix='rds-wheel-concurrent-') as directory:
            root = Path(directory) / 'project'
            def prepare_only(wheel, contract_path):
                wheel.setup(read_json(contract_path), initialized=False)
            with patch.object(Wheel, 'initialize', autospec=True, side_effect=prepare_only):
                fixture.prepare(root)
            code = ("import sys,time; from pathlib import Path; "
                    "sys.path.insert(0,sys.argv[1]+'/scripts'); from rds_wheel import Wheel; "
                    "root=Path(sys.argv[2]); original=Wheel.initial_state; "
                    "exec('def staged(self, factors):\\n original(self, factors)\\n (root/\"staging-ready\").touch()\\n deadline=time.monotonic()+15\\n while not (root/\"release-staging\").exists():\\n  assert time.monotonic()<deadline, \"test publication barrier timed out\"\\n  time.sleep(.02)'); "
                    "Wheel.initial_state=staged; Wheel(root).initialize(root/'project-contract.json')")
            process = subprocess.Popen([sys.executable, '-B', '-c', code, str(REPO), str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not (root / 'staging-ready').exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root / 'staging-ready').exists(), 'first initializer must hold the actual OS lock')
                self.assertFalse((root / '.rds/wheel').exists())
                lock_path = root / '.rds/wheel-init.lock'
                identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
                with self.assertRaisesRegex(ValueError, 'initialization is busy'):
                    Wheel(root).initialize(root / 'project-contract.json')
                self.assertEqual(identity, (lock_path.stat().st_dev, lock_path.stat().st_ino))
                (root / 'release-staging').touch()
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, err.decode('utf-8', errors='replace'))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            wheel = Wheel(root)
            self.assertEqual(lock_path.read_bytes(), b'0')
            self.assertEqual(len(list(wheel.directory.iterdir())), 10)
            self.assertEqual(wheel.project('status')['runs'], [])
            before = {p.name: p.read_bytes() for p in wheel.directory.iterdir()}
            with self.assertRaisesRegex(ValueError, 'already initialized'):
                wheel.initialize(root / 'project-contract.json')
            self.assertEqual(before, {p.name: p.read_bytes() for p in wheel.directory.iterdir()})

    def test_initialization_write_and_publish_failures_retry_same_native_project(self):
        for point in ('staging', 'publish'):
            with self.subTest(point=point), tempfile.TemporaryDirectory(prefix='rds-wheel-stage-') as directory:
                root = Path(directory) / 'project'
                if point == 'staging':
                    write = Wheel.write
                    def fail_write(wheel, name, value, **kwargs):
                        write(wheel, name, value, **kwargs)
                        if name == 'band.json':
                            raise OSError('injected staging write failure')
                    injection = patch.object(Wheel, 'write', autospec=True, side_effect=fail_write)
                else:
                    injection = patch.object(wheel_module.os, 'rename', side_effect=OSError('injected publish failure'))
                with injection, self.assertRaisesRegex(OSError, 'injected'):
                    fixture.prepare(root)
                self.assertFalse((root / '.rds/wheel').exists())
                wheel = Wheel(root)
                before = wheel.project('status') if point == 'publish' else None
                wheel.initialize(root / 'project-contract.json')
                after = wheel.project('status')
                if before:
                    self.assertEqual(after, before, 'matching init retry must preserve the actual ledger and budget')
                self.assertEqual(after['runs'], [])
                self.assertEqual(after['receipts'], [])
                self.assertEqual(after['contract_sha256'], digest(read_json(root / 'project-contract.json')))
                files = {p.name: p.read_bytes() for p in wheel.directory.iterdir() if p.is_file()}
                self.assertEqual(len(files), 10)
                with self.assertRaisesRegex(ValueError, 'already initialized'):
                    wheel.initialize(root / 'project-contract.json')
                self.assertEqual(files, {p.name: p.read_bytes() for p in wheel.directory.iterdir() if p.is_file()})

    def test_process_exit_after_native_init_allows_matching_retry_only(self):
        with tempfile.TemporaryDirectory(prefix='rds-wheel-crash-') as directory:
            root = Path(directory) / 'project'
            code = ("import sys,os,importlib.util; from pathlib import Path; from unittest.mock import patch; "
                    "sys.path.insert(0,sys.argv[1]+'/scripts'); import rds_wheel; "
                    "s=importlib.util.spec_from_file_location('f',sys.argv[1]+'/examples/wheel/prepare.py'); "
                    "f=importlib.util.module_from_spec(s); s.loader.exec_module(f); "
                    "p=patch.object(rds_wheel.os,'rename',side_effect=lambda *a:os._exit(73)); p.start(); "
                    "f.prepare(Path(sys.argv[2]))")
            result = subprocess.run([sys.executable, '-B', '-c', code, str(REPO), str(root)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 73, result.stderr.decode('utf-8', errors='replace'))
            self.assertFalse((root / '.rds/wheel').exists())
            wheel = Wheel(root)
            before = wheel.project('status')
            contract_path = root / 'project-contract.json'
            original = read_json(contract_path)
            contract_path.write_text(canonical({**original, 'description': 'different contract'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Contract is frozen'):
                wheel.initialize(contract_path)
            self.assertFalse(wheel.directory.exists())
            self.assertEqual(before, wheel.project('status'))
            contract_path.write_text(canonical(original), encoding='utf-8')
            wheel.initialize(contract_path)
            self.assertEqual(before, wheel.project('status'))
            self.assertEqual(read_json(wheel.state_path('state.json'))['ticks'], 0)

    def test_project_init_rejection_leaves_wheel_uninitialized_for_corrected_retry(self):
        with tempfile.TemporaryDirectory(prefix="rds-wheel-init-retry-") as directory:
            root = Path(directory) / "project"
            initialize = Wheel.initialize
            original = {}

            def reject_missing_code(wheel, contract_path):
                contract = read_json(contract_path)
                original.update(deepcopy(contract))
                contract["bindings"] = [b for b in contract["bindings"] if b["role"] != "code"]
                contract_path.write_text(canonical(contract) + "\n", encoding="utf-8")
                return initialize(wheel, contract_path)

            # Keep all wheel-specific inputs valid; the real kernel rejects the
            # missing role. No FakeProject or bootstrap execution is involved.
            with patch.object(Wheel, "initialize", autospec=True, side_effect=reject_missing_code):
                with self.assertRaisesRegex(wheel_module.WheelError, "code/config/data/evaluator/protocol bindings required"):
                    fixture.prepare(root)
            self.assertFalse((root / ".rds/wheel").exists())
            frozen = {p: p.read_bytes() for p in (root / "trusted").iterdir()}
            contract_path = root / "project-contract.json"
            contract_path.write_text(canonical(original) + "\n", encoding="utf-8")
            wheel = Wheel(root)
            wheel.initialize(contract_path)
            status = wheel.project("status")
            self.assertEqual(status["contract"], original)
            self.assertEqual(status["contract_sha256"], digest(original))
            self.assertEqual(status["runs"], [])
            self.assertEqual(status["receipts"], [])
            budget = status["budget"]["wall_seconds"]
            self.assertEqual(budget["cap"], original["budget"]["wall_seconds"])
            self.assertEqual(budget["remaining"], budget["cap"])
            self.assertEqual(read_json(wheel.state_path("state.json"))["ticks"], 0)
            self.assertEqual(frozen, {p: p.read_bytes() for p in frozen})

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
