"""Successor roots (#178): `project init --supersedes` links frozen roots into one study ledger."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from rds_advisor import RDSAdvisor  # noqa: E402
from rds_checkpoints import save_checkpoint  # noqa: E402
from rds_project import ProjectStore, canonical, digest  # noqa: E402
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


def relink(root, body, sha):
    """Replace a root's predecessor link out of band, creating the table for a root that had none."""
    db = sqlite3.connect(Path(root) / ".rds" / "project.sqlite3")
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='predecessor'").fetchone():
            db.execute("DROP TRIGGER IF EXISTS predecessor_no_update")
            db.execute("UPDATE predecessor SET body=?, sha256=?", (body, sha))
        else:
            db.execute("CREATE TABLE predecessor(id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("INSERT INTO predecessor VALUES (1,?,?)", (sha, body))
        db.commit()
    finally:
        db.close()


def link_to(successor, predecessor):
    """A hash-consistent link record pointing successor at predecessor, as project init would write it."""
    pins, _ = ProjectStore(predecessor)._ledger_pins()
    record = {"schema": 1, "root_path": os.path.relpath(Path(predecessor).resolve(), Path(successor).resolve()), **pins,
              "assurance": "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION"}
    return canonical(record), digest(record)


def null_body(root, table, key):
    """Rebuild a table without its NOT NULL guard and null one body, as only an outside writer could."""
    db = sqlite3.connect(Path(root) / ".rds" / "project.sqlite3")
    try:
        db.execute(f"ALTER TABLE {table} RENAME TO damaged")
        db.execute(f"CREATE TABLE {table}({key} PRIMARY KEY, sha256 TEXT, body TEXT)")
        db.execute(f"INSERT INTO {table} SELECT {key}, sha256, NULL FROM damaged")
        db.execute("DROP TABLE damaged")
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

    def activate_first(self):
        from rds_project_lifecycle import enable_advisor
        declaration = copy.deepcopy(CONTEXT['decision'])
        declaration['goal_conditions'] = [{'fact': 'run.r1.completed', 'value': True}]
        policy = {'schema': 1, 'context': {'decision': declaration}, 'graph': graph(),
                  'routes': [{'candidate': 'root-test', 'manifest': self.spec()}], 'observations': []}
        preview = enable_advisor(self.store, policy)
        enabled = enable_advisor(self.store, policy, apply=True, expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual(enabled['status'], 'ADVISOR_ENABLED')
        return enabled

    def test_activated_predecessor_retains_pinned_genesis_rejection(self):
        candidate = self.candidate()
        self.record(self.first, 'genesis-rejected', candidate)
        self.activate_first()
        save_checkpoint(self.first, 'activated-opaque', self.store.snapshot(), kind='project')
        before = ledger_dump(self.first)
        root, contract = self.phase('after-activation')
        successor = ProjectStore(root)
        successor.initialize(contract, supersedes=str(self.first))
        self.assertEqual(successor.predecessor_chain()[0]['status'], 'VERIFIED')
        output = self.search(root)['search']
        self.assertEqual(output['candidates'], [])
        self.assertEqual(output['blocked_candidates'][-1]['loop_review']['checkpoint_id'], 'genesis-rejected')
        self.assertFalse(any(flag['kind'] in {'PREDECESSOR_CHAIN_UNVERIFIED', 'LOOP_HISTORY_REVIEW_ERROR'}
                             for flag in output['loop_review']['flags']))
        self.assertEqual(ledger_dump(self.first), before)

    def test_activated_predecessor_rejects_manual_choice_and_successor_retains_genesis(self):
        candidate = self.candidate()
        self.record(self.first, 'genesis-accepted', candidate, outcome='accepted')
        self.activate_first()
        before = ledger_dump(self.first)
        with self.assertRaisesRegex(ValueError, 'owns decision checkpoints'):
            self.record(self.first, 'activated-rejected', candidate)
        self.assertEqual(ledger_dump(self.first), before)
        root, contract = self.phase('mixed-lineage')
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        output = self.search(root)['search']
        self.assertEqual([row['id'] for row in output['candidates']], [candidate['id']])
        self.assertFalse(any(flag['kind'] == 'PREDECESSOR_CHAIN_UNVERIFIED'
                             for flag in output['loop_review']['flags']))
        # A legacy successor's own later decision still takes precedence over genesis.
        self.record(root, 'successor-rejected', candidate)
        self.assertEqual(self.search(root)['search']['candidates'], [])
        self.assertEqual(self.search(root)['search']['blocked_candidates'][-1]['loop_review']['checkpoint_id'], 'successor-rejected')

    def test_activation_after_supersession_invalidates_original_contract_pin(self):
        self.record(self.first, 'genesis-rejected', self.candidate())
        root, contract = self.phase('pinned-before-activation')
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        self.activate_first()
        chain = ProjectStore(root).predecessor_chain()
        self.assertEqual(chain[0]['status'], 'MISMATCH')
        self.assertIn('Predecessor contract differs', chain[0]['reason'])
        output = self.search(root)['search']
        self.assertTrue(output['candidates'])
        self.assertIn('PREDECESSOR_CHAIN_UNVERIFIED', {flag['kind'] for flag in output['loop_review']['flags']})

    def test_predecessor_lineage_is_revalidated_after_chain_check(self):
        candidate = self.candidate()
        self.record(self.first, 'genesis-rejected', candidate)
        self.activate_first()
        root, contract = self.phase('lineage-revalidation')
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        original_check, checked = ProjectStore.predecessor_chain, []
        def corrupt_after_check(store, *args, **kwargs):
            chain = original_check(store, *args, **kwargs)
            if not checked:
                checked.append(chain)
                db = sqlite3.connect(self.first / '.rds/project.sqlite3')
                try:
                    row = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')='ADVISOR_POLICY_ENABLED'").fetchone()
                    value = json.loads(row[1])
                    value['parent_sha256'] = '0' * 64
                    value['sha256'] = digest({key: field for key, field in value.items() if key != 'sha256'})
                    db.execute('DROP TRIGGER IF EXISTS events_no_update')
                    db.execute('DROP TRIGGER IF EXISTS event_no_update')
                    db.execute('UPDATE events SET body=? WHERE id=?', (canonical(value), row[0]))
                    db.commit()
                finally:
                    db.close()
            return chain
        with mock.patch.object(ProjectStore, 'predecessor_chain', corrupt_after_check):
            output = self.search(root)['search']
        self.assertEqual(checked[0][0]['status'], 'VERIFIED')
        review = output['loop_review']
        refusal = next(flag for flag in review['flags'] if flag['kind'] == 'PREDECESSOR_CHAIN_UNVERIFIED')
        self.assertIn('Advisor activation lineage mismatch', refusal['reason'])
        self.assertTrue(output['candidates'])
        self.assertFalse(output.get('blocked_candidates'))
        self.assertFalse(any('predecessor root(s)' in line for line in review['limitations']))

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
        self.assertTrue(str(caught.exception).startswith("Contract is frozen; reuse this project for additional runs."))
        self.assertIn("project enable-advisor", str(caught.exception))
        self.assertIn("project revise", str(caught.exception))
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

    def test_a_changed_ancestor_link_is_a_mismatch_and_is_never_traversed(self):
        candidate = self.candidate()
        self.record(self.first, "phase1-rejected", candidate)
        second, contract = self.phase("second")
        ProjectStore(second).initialize(contract, supersedes=str(self.first))
        third, contract = self.phase("third")
        ProjectStore(third).initialize(contract, supersedes=str(second))
        self.assertEqual(self.search(third)["search"]["candidates"], [])
        alternate, contract = self.phase("alternate")
        ProjectStore(alternate).initialize(contract)
        # Second's link now names another root; the record is self-consistent, but third pinned the old one.
        relink(second, *link_to(second, alternate))
        chain = ProjectStore(third).snapshot()["predecessor_chain"]
        self.assertEqual([hop["status"] for hop in chain], ["MISMATCH"])
        self.assertEqual(Path(chain[0]["root"]), second.resolve())
        self.assertIn("own link differs", chain[0]["reason"])
        review = self.search(third)["search"]["loop_review"]
        flag = next(f for f in review["flags"] if f["kind"] == "PREDECESSOR_CHAIN_UNVERIFIED")
        self.assertEqual(Path(flag["root"]), second.resolve())
        self.assertNotIn("LOOP_HISTORY_REVIEW_ERROR", [f["kind"] for f in review["flags"]])
        self.assertFalse(any("predecessor root" in text for text in review["limitations"]))
        # A link added to a root that superseded nothing is the same change: the absence was pinned too.
        fourth, contract = self.phase("fourth")
        ProjectStore(fourth).initialize(contract, supersedes=str(alternate))
        relink(alternate, *link_to(alternate, self.first))
        [hop] = ProjectStore(fourth).predecessor_chain()
        self.assertEqual((hop["status"], Path(hop["root"])), ("MISMATCH", alternate.resolve()))
        self.assertIn("own link differs", hop["reason"])

    def test_advisor_uses_only_the_pinned_checkpoint_bytes(self):
        candidate = self.candidate()
        self.record(self.first, "phase1-rejected", candidate)
        root, contract = self.phase("second")
        ProjectStore(root).initialize(contract, supersedes=str(self.first))
        self.record(root, "phase2-rejected", candidate)
        state = ProjectStore(root).snapshot()
        state["advisor_context"] = copy.deepcopy(CONTEXT)
        chain_check, calls = ProjectStore.predecessor_chain, []

        def replace_after_check(store, *args, **kwargs):
            chain = chain_check(store, *args, **kwargs)
            if not calls:  # One real row replacement right after the Advisor's own chain check.
                calls.append(chain)
                path = self.first / ".rds" / "project.sqlite3"
                db = sqlite3.connect(path)
                try:
                    body = json.loads(db.execute("SELECT body FROM checkpoints WHERE id='phase1-rejected'").fetchone()[0])
                    body["decision"]["outcome"] = "accepted"
                    raw = json.dumps(body, sort_keys=True)
                    db.execute("DROP TRIGGER checkpoint_no_update")
                    db.execute("UPDATE checkpoints SET body=?, sha=? WHERE id='phase1-rejected'",
                               (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()))
                    db.commit()
                finally:
                    db.close()
            return chain

        with mock.patch.object(ProjectStore, "predecessor_chain", replace_after_check):
            rows = RDSAdvisor(root).recommend_next_directions(state, graph())
        self.assertEqual([hop["status"] for hop in calls[0]], ["VERIFIED"])
        output = next(row for row in rows if row["type"] == "EXECUTABLE_DIRECTION_SEARCH")["search"]
        review = output["loop_review"]
        flag = next(f for f in review["flags"] if f["kind"] == "PREDECESSOR_CHAIN_UNVERIFIED")
        self.assertEqual(Path(flag["root"]), self.first.resolve())
        self.assertIn("differs from the pinned digest: phase1-rejected", flag["reason"])
        self.assertNotIn("LOOP_HISTORY_REVIEW_ERROR", [f["kind"] for f in review["flags"]])
        self.assertFalse(any("predecessor root" in text for text in review["limitations"]))
        # The successor's own history still decides.
        self.assertEqual(output["candidates"], [])
        blocked = output["blocked_candidates"][-1]["loop_review"]
        self.assertEqual((blocked["checkpoint_id"], blocked.get("root")), ("phase2-rejected", None))
        # An ordinary chain check sees the same replacement.
        [hop] = ProjectStore(root).predecessor_chain()
        self.assertEqual(hop["status"], "MISMATCH")

    def test_malformed_link_records_are_a_mismatch(self):
        second, contract = self.phase("second")
        ProjectStore(second).initialize(contract, supersedes=str(self.first))
        third, contract = self.phase("third")
        ProjectStore(third).initialize(contract, supersedes=str(second))
        good = json.loads(link_to(second, self.first)[0])
        cases = {"schema only": {"schema": 1}, "not an object": [], "empty root path": dict(good, root_path=""),
                 "schema 2": dict(good, schema=2), "missing link digest": {k: v for k, v in good.items() if k != "predecessor_sha256"},
                 "checkpoint pins": dict(good, checkpoint_shas=[{"id": "x"}]), "receipt pins": dict(good, receipt_digests={})}
        for index, (name, record) in enumerate([*cases.items(), ("not JSON", None)]):
            with self.subTest(name):
                body, sha = ("{", "0" * 64) if record is None else (canonical(record), digest(record))
                relink(second, body, sha)
                [hop] = ProjectStore(second).snapshot()["predecessor_chain"]
                self.assertEqual((hop["status"], Path(hop["root"])), ("MISMATCH", second.resolve()))
                review = self.search(second)["search"]["loop_review"]
                self.assertIn("PREDECESSOR_CHAIN_UNVERIFIED", [f["kind"] for f in review["flags"]])
                self.assertNotIn("LOOP_HISTORY_REVIEW_ERROR", [f["kind"] for f in review["flags"]])
                # A successor that pinned the old link reports the damaged predecessor and keeps working.
                [hop] = ProjectStore(third).snapshot()["predecessor_chain"]
                self.assertEqual((hop["status"], Path(hop["root"])), ("MISMATCH", second.resolve()))
                fourth, contract = self.phase(f"fourth-{index}")
                with self.assertRaisesRegex(ValueError, "Predecessor ledger cannot be superseded"):
                    ProjectStore(fourth).initialize(contract, supersedes=str(second))
                self.assertFalse((fourth / ".rds").exists())

    def test_an_older_hop_failing_at_read_keeps_the_newer_history(self):
        candidate = self.candidate()
        self.record(self.first, "phase1-accepted", candidate, outcome="accepted")
        second, contract = self.phase("second")
        ProjectStore(second).initialize(contract, supersedes=str(self.first))
        self.record(second, "phase2-rejected", candidate)
        third, contract = self.phase("third")
        ProjectStore(third).initialize(contract, supersedes=str(second))
        state = ProjectStore(third).snapshot()
        state["advisor_context"] = copy.deepcopy(CONTEXT)
        chain_check = ProjectStore.predecessor_chain

        def delete_after_check(store, *args, **kwargs):
            chain = chain_check(store, *args, **kwargs)
            db = sqlite3.connect(self.first / ".rds" / "project.sqlite3")
            try:
                db.execute("DROP TRIGGER checkpoint_no_delete")
                db.execute("DELETE FROM checkpoints")
                db.commit()
            finally:
                db.close()
            return chain

        with mock.patch.object(ProjectStore, "predecessor_chain", delete_after_check):
            rows = RDSAdvisor(third).recommend_next_directions(state, graph())
        output = next(row for row in rows if row["type"] == "EXECUTABLE_DIRECTION_SEARCH")["search"]
        flag = next(f for f in output["loop_review"]["flags"] if f["kind"] == "PREDECESSOR_CHAIN_UNVERIFIED")
        self.assertEqual(Path(flag["root"]), self.first.resolve())
        self.assertIn("Pinned predecessor checkpoint is missing: phase1-accepted", flag["reason"])
        self.assertTrue(any(text.startswith("History includes pinned records from 1 predecessor root(s)")
                            for text in output["loop_review"]["limitations"]))
        # The newer hop's rejection is still read; the dropped older acceptance never reaches the review.
        self.assertEqual(output["candidates"], [])
        blocked = output["blocked_candidates"][-1]["loop_review"]
        self.assertEqual((blocked["checkpoint_id"], Path(blocked["root"])), ("phase2-rejected", second.resolve()))

    def test_damage_of_any_shape_at_the_boundary_is_a_mismatch(self):
        candidate = self.candidate()
        self.store.register(self.spec())
        self.store.execute("r1")
        second, contract = self.phase("second")
        ProjectStore(second).initialize(contract, supersedes=str(self.first))
        self.record(second, "phase2-rejected", candidate)
        third, contract = self.phase("third")
        ProjectStore(third).initialize(contract, supersedes=str(second))
        # A predecessor receipt with no body (TypeError inside the receipt reader).
        null_body(self.first, "receipts", "run_id")
        [hop] = ProjectStore(second).snapshot()["predecessor_chain"]
        self.assertEqual((hop["status"], Path(hop["root"])), ("MISMATCH", self.first.resolve()))
        # A middle root's link with no body.
        null_body(second, "predecessor", "id")
        [hop] = ProjectStore(third).snapshot()["predecessor_chain"]
        self.assertEqual((hop["status"], Path(hop["root"])), ("MISMATCH", second.resolve()))
        # The successor's own link nested past the JSON parser's recursion limit; its own history still decides.
        relink(second, "[" * 200000, "0" * 64)
        [hop] = ProjectStore(second).snapshot()["predecessor_chain"]
        self.assertEqual((hop["status"], Path(hop["root"])), ("MISMATCH", second.resolve()))
        output = self.search(second)["search"]
        kinds = [f["kind"] for f in output["loop_review"]["flags"]]
        self.assertIn("PREDECESSOR_CHAIN_UNVERIFIED", kinds)
        self.assertNotIn("LOOP_HISTORY_REVIEW_ERROR", kinds)
        self.assertEqual(output["candidates"], [])
        self.assertEqual(output["blocked_candidates"][-1]["loop_review"]["checkpoint_id"], "phase2-rejected")

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
                                    "project", "init", "--mode", "quick", "--contract", str(root / "contract.json"),
                                    "--supersedes", str(self.first)], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["predecessor_chain"][0]["status"], "VERIFIED")
        missing, contract = self.phase("third")
        (missing / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "rds_cli.py"), "--root", str(missing),
                                    "project", "init", "--mode", "quick", "--contract", str(missing / "contract.json"),
                                    "--supersedes", str(Path(self.tmp.name) / "nowhere")],
                                   capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("[RDS-REJECT] Predecessor has no project ledger", completed.stderr)
        self.assertFalse((missing / ".rds").exists())
        # Roots that have a relative path keep storing it.
        self.assertFalse(os.path.isabs(ProjectStore(root)._predecessor()["root_path"]))

    def test_a_predecessor_with_no_relative_path_is_linked_by_absolute_path(self):
        # On Windows no relative path exists between two drives, or a drive and a UNC share; relpath raises (#185).
        before = ledger_dump(self.first)
        root, contract = self.phase("second")
        no_relative = ValueError("path is on mount 'C:', start on mount 'D:'")
        with mock.patch("rds_project.os.path.relpath", side_effect=no_relative):
            ProjectStore(root).initialize(contract, supersedes=str(self.first))
            ProjectStore(root).initialize(contract, supersedes=str(self.first))  # Same link: unchanged.
            other, other_contract = self.phase("other")
            ProjectStore(other).initialize(other_contract)
            with self.assertRaisesRegex(ValueError, "Predecessor is frozen with the contract"):
                ProjectStore(root).initialize(contract, supersedes=str(other))
        self.assertEqual(ProjectStore(root)._predecessor()["root_path"], str(self.first.resolve()))
        [hop] = ProjectStore(root).snapshot()["predecessor_chain"]
        self.assertEqual((hop["status"], Path(hop["root"])), ("VERIFIED", self.first.resolve()))
        self.assertEqual(ledger_dump(self.first), before)

    @unittest.skipUnless(os.name == "nt", "drive letters are a Windows path boundary")
    def test_cli_links_roots_on_different_windows_drives(self):
        try:
            far = Path(tempfile.mkdtemp(prefix="rds-successor-drive-", dir=ROOT.parent))
        except OSError:
            self.skipTest("no writable directory beside the checkout")
        self.addCleanup(shutil.rmtree, far, True)
        if far.drive.lower() == Path(self.tmp.name).resolve().drive.lower():
            self.skipTest("the checkout and the temporary directory are on the same drive")

        def init(root, contract, predecessor):
            (root / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
            completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "rds_cli.py"), "--root", str(root),
                                        "project", "init", "--mode", "quick", "--contract", str(root / "contract.json"),
                                        "--supersedes", str(predecessor)], capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            chain = json.loads(completed.stdout)["predecessor_chain"]
            self.assertEqual((chain[0]["status"], Path(chain[0]["root"])), ("VERIFIED", Path(predecessor).resolve()))
            return chain

        # Temporary drive to checkout drive, then back.
        middle = far / "middle"
        shutil.copytree(self.first, middle, ignore=shutil.ignore_patterns(".rds", "outputs"))
        self.assertEqual(len(init(middle, dict(self.contract, description="middle"), self.first)), 1)
        last, contract = self.phase("last")
        chain = init(last, contract, middle)
        self.assertEqual([(hop["status"], Path(hop["root"])) for hop in chain],
                         [("VERIFIED", middle.resolve()), ("VERIFIED", self.first.resolve())])


if __name__ == "__main__":
    unittest.main(verbosity=2)
