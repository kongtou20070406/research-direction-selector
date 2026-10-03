"""Successor roots (#178): `project init --supersedes` links frozen roots into one study ledger."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from rds_advisor import RDSAdvisor  # noqa: E402
from rds_checkpoints import save_checkpoint  # noqa: E402
from rds_project import ProjectStore  # noqa: E402
from test_rds_advisor_search import fact, node  # noqa: E402
import test_rds_project  # noqa: E402  (module import keeps its TestCase out of this module's discovery)

CONTEXT = {"decision": {"id": "choose", "goal_revision": "g1", "scope": {"dataset": "dev"}},
           "facts": {"root-done": fact(False), "x": fact(False)}}


def graph():
    value = {"nodes": [node("root", [{"fact": "x", "value": False}])], "edges": []}
    value["nodes"][0]["executable"]["action"].update(
        target={"name": "advisor_candidate_filter", "type": "boolean"}, intervention={"value": True})
    return value


def ledger_dump(root):
    """Every table row of a ledger plus its file bytes, to show a predecessor is never written."""
    path = Path(root) / ".rds" / "project.sqlite3"
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rows = list(db.iterdump())
    finally:
        db.close()
    return rows, hashlib.sha256(path.read_bytes()).hexdigest()


def tamper(root, table, column, value):
    """Simulate disk damage: bypass the append-only trigger, as only an outside writer could."""
    db = sqlite3.connect(Path(root) / ".rds" / "project.sqlite3")
    try:
        trigger = "checkpoint_no_update" if table == "checkpoints" else f"{table}_no_update"
        db.execute(f"DROP TRIGGER {trigger}")
        db.execute(f"UPDATE {table} SET {column}=?", (value,))
        db.commit()
    finally:
        db.close()


class SuccessorTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.first, self.store, self.contract, self.spec = helper.root, helper.store, helper.contract, helper.spec
        self.tmp = tempfile.TemporaryDirectory(prefix="rds successor ")
        self.addCleanup(self.tmp.cleanup)

    def phase(self, name, description=None):
        """A new, uninitialized root with the same bound files; a later study phase."""
        root = Path(self.tmp.name) / name
        shutil.copytree(self.first, root, ignore=shutil.ignore_patterns(".rds", "outputs"))
        contract = dict(self.contract, description=description or f"phase {name}")
        return root, contract

    def record(self, root, identity, candidate, outcome="rejected"):
        decision = CONTEXT["decision"]
        return save_checkpoint(root, identity, ProjectStore(root).snapshot(), kind="project", decision={
            "question_id": decision["id"], "goal_revision": decision["goal_revision"], "scope": decision["scope"],
            "candidate": candidate, "outcome": outcome, "evidence": CONTEXT["facts"]})

    def search(self, root, context=None):
        state = ProjectStore(root).snapshot()
        state["advisor_context"] = copy.deepcopy(context or CONTEXT)
        rows = RDSAdvisor(root).recommend_next_directions(state, graph())
        return next(row for row in rows if row["type"] == "EXECUTABLE_DIRECTION_SEARCH")

    def candidate(self):
        return self.search(self.first)["search"]["candidates"][0]

    def test_link_pins_digests_and_never_writes_the_predecessor(self):
        self.store.register(self.spec())
        self.store.execute("r1")
        self.record(self.first, "phase1-choice", self.candidate())
        before = ledger_dump(self.first)
        root, contract = self.phase("second")
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        self.assertEqual(ledger_dump(self.first), before)
        [hop] = ProjectStore(root).snapshot()["predecessor_chain"]
        self.assertEqual(hop["status"], "VERIFIED")
        self.assertEqual(Path(hop["root"]), self.first.resolve())
        self.assertEqual(hop["checkpoint_ids"], ["phase1-choice"])
        self.assertEqual((hop["unpinned_receipts"], hop["unpinned_checkpoints"]), (0, 0))
        # The successor is an ordinary root: it registers and executes under its own frozen contract.
        successor = ProjectStore(root)
        successor.register(self.spec())
        self.assertEqual(successor.execute("r1")["run_status"], "SUCCEEDED")
        save_checkpoint(root, "phase2", successor.snapshot(), kind="project")
        self.assertEqual(ledger_dump(self.first), before)
        # The record is append-only like the contract.
        db = sqlite3.connect(root / ".rds" / "project.sqlite3")
        try:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                db.execute("UPDATE predecessor SET body='{}'")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                db.execute("DELETE FROM predecessor")
        finally:
            db.close()

    def test_unusable_predecessors_are_refused_and_nothing_is_written(self):
        self.store.register(self.spec())
        self.store.execute("r1")
        empty = Path(self.tmp.name) / "no-ledger"
        empty.mkdir()
        cases = [(Path(self.tmp.name) / "missing", "Predecessor has no project ledger"),
                 (empty, "Predecessor has no project ledger")]
        for index, (predecessor, message) in enumerate(cases):
            root, contract = self.phase(f"refused-{index}")
            with self.subTest(predecessor=predecessor.name), self.assertRaisesRegex(ValueError, message):
                ProjectStore(root).initialize(contract, supersedes=str(predecessor))
            self.assertFalse((root / ".rds").exists())
        with self.assertRaisesRegex(ValueError, "cannot supersede itself"):
            ProjectStore(self.first).initialize(self.contract, supersedes=str(self.first))
        tamper(self.first, "receipts", "body", '{"run_id": "r1"}')
        root, contract = self.phase("damaged")
        with self.assertRaisesRegex(ValueError, "Predecessor ledger cannot be superseded: Project receipt integrity failure"):
            ProjectStore(root).initialize(contract, supersedes=str(self.first))
        self.assertFalse((root / ".rds").exists())

    def test_frozen_link_and_reinit(self):
        root, contract = self.phase("second")
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        ProjectStore(root).initialize(contract, supersedes=str(self.first))  # Same contract and link: unchanged.
        ProjectStore(root).initialize(contract)
        other, other_contract = self.phase("other")
        ProjectStore(other).initialize(other_contract)
        with self.assertRaisesRegex(ValueError, "Predecessor is frozen with the contract"):
            ProjectStore(root).initialize(contract, supersedes=str(other))
        with self.assertRaisesRegex(ValueError, "Predecessor is frozen with the contract"):
            ProjectStore(other).initialize(other_contract, supersedes=str(self.first))
        # The frozen-contract rejection names the link that keeps the next phase in one study ledger.
        with self.assertRaises(ValueError) as caught:
            ProjectStore(root).initialize(dict(contract, description="changed"))
        self.assertTrue(str(caught.exception).startswith("Contract is frozen; use a new project root"))
        self.assertIn("project init --supersedes", str(caught.exception))
        self.assertIn(str(root.resolve()), str(caught.exception))

    def test_later_damage_is_reported_and_the_successor_keeps_working(self):
        self.record(self.first, "phase1-choice", self.candidate())
        root, contract = self.phase("second")
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        tamper(self.first, "checkpoints", "sha", "0" * 64)
        [hop] = ProjectStore(root).snapshot()["predecessor_chain"]
        self.assertEqual(hop["status"], "MISMATCH")
        self.assertIn("Checkpoint integrity failure: phase1-choice", hop["reason"])
        review = self.search(root)["search"]["loop_review"]
        flag = next(f for f in review["flags"] if f["kind"] == "PREDECESSOR_CHAIN_UNVERIFIED")
        self.assertEqual(Path(flag["root"]), self.first.resolve())
        self.assertNotIn("LOOP_HISTORY_REVIEW_ERROR", [f["kind"] for f in review["flags"]])

    def test_advisor_reads_pinned_decisions_across_the_chain(self):
        candidate = self.candidate()
        self.record(self.first, "phase1-rejected", candidate)
        root, contract = self.phase("second")
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        output = self.search(root)["search"]
        self.assertEqual(output["candidates"], [])
        blocked = output["blocked_candidates"][-1]["loop_review"]
        self.assertEqual(blocked["kind"], "REPEAT_REJECTED_ROUTE")
        self.assertEqual(blocked["checkpoint_id"], "phase1-rejected")
        self.assertEqual(Path(blocked["root"]), self.first.resolve())
        self.assertTrue(any("predecessor root" in text and "not scientific verification" in text
                            for text in output["loop_review"]["limitations"]))
        # A later decision in the successor still wins over the predecessor's.
        self.record(root, "phase2-accepted", candidate, outcome="accepted")
        self.assertEqual([c["id"] for c in self.search(root)["search"]["candidates"]], [candidate["id"]])

    def test_records_added_to_a_predecessor_after_supersession_are_not_read(self):
        candidate = self.candidate()
        root, contract = self.phase("second")
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        self.record(self.first, "late-rejected", candidate)
        [hop] = ProjectStore(root).snapshot()["predecessor_chain"]
        self.assertEqual((hop["status"], hop["unpinned_checkpoints"]), ("VERIFIED", 1))
        self.assertEqual([c["id"] for c in self.search(root)["search"]["candidates"]], [candidate["id"]])

    def test_depth_limit_truncates_the_chain(self):
        candidate = self.candidate()
        self.record(self.first, "phase1-rejected", candidate)
        second, contract = self.phase("second")
        ProjectStore(second).initialize(contract, supersedes=str(self.first))
        third, contract = self.phase("third")
        ProjectStore(third).initialize(contract, supersedes=str(second))
        chain = ProjectStore(third).snapshot()["predecessor_chain"]
        self.assertEqual([hop["status"] for hop in chain], ["VERIFIED", "VERIFIED"])
        self.assertEqual([hop["status"] for hop in ProjectStore(third).predecessor_chain(1)], ["VERIFIED", "TRUNCATED"])
        self.assertEqual(self.search(third)["search"]["candidates"], [])
        output = self.search(third, dict(CONTEXT, predecessor_depth=1))["search"]
        self.assertEqual([c["id"] for c in output["candidates"]], [candidate["id"]])
        self.assertTrue(any(text.startswith("PREDECESSOR_CHAIN_TRUNCATED") for text in output["loop_review"]["limitations"]))
        with self.assertRaisesRegex(ValueError, "Predecessor depth must be an integer in 0..32"):
            ProjectStore(third).predecessor_chain(33)

    def test_roots_without_a_predecessor_are_unchanged(self):
        snapshot = self.store.snapshot()
        self.assertNotIn("predecessor_chain", snapshot)
        db = sqlite3.connect(self.first / ".rds" / "project.sqlite3")
        try:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='predecessor'").fetchone())
        finally:
            db.close()
        self.assertEqual(self.store.predecessor_chain(), [])

    def test_cli_supersedes(self):
        root, contract = self.phase("second")
        (root / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "rds_cli.py"), "--root", str(root),
                                    "project", "init", "--contract", str(root / "contract.json"),
                                    "--supersedes", str(self.first)], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["predecessor_chain"][0]["status"], "VERIFIED")
        missing, contract = self.phase("third")
        (missing / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "rds_cli.py"), "--root", str(missing),
                                    "project", "init", "--contract", str(missing / "contract.json"),
                                    "--supersedes", str(Path(self.tmp.name) / "nowhere")],
                                   capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("[RDS-REJECT] Predecessor has no project ledger", completed.stderr)
        self.assertFalse((missing / ".rds").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
