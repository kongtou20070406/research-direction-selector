"""Workspace-to-ledger binding (#282): one canonical ledger per bound workspace.

Acceptance walks real store and CLI surfaces: a paid failed run before an
attempted abandonment, missing/replaced ledgers, concurrent binding, stale
identity, aliases, and normal canonical continuation. Refusals must leave
attempts, receipts and budget unchanged.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import test_rds_project  # noqa: E402  (module import keeps its TestCase out of this module's discovery)
from rds_project import ProjectStore, canonical, digest, file_sha  # noqa: E402
from rds_workspace import (ASSURANCE, EVENT_KIND, POINTER_NAME, bind, check_admission,  # noqa: E402
                           coverage, pointer_path, read_pointer)


class WorkspaceBindingTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store, self.contract, self.spec = helper.root, helper.store, helper.contract, helper.spec
        self.tmp = tempfile.TemporaryDirectory(prefix="rds workspace bind ")
        self.addCleanup(self.tmp.cleanup)

    def phase(self, name):
        """A fresh, uninitialized root inside the bound workspace tree (the tempting sibling reset)."""
        root = self.root / name
        root.mkdir(exist_ok=True)
        for binding in self.contract["bindings"]:
            shutil.copyfile(self.root / binding["path"], root / binding["path"])
        return root

    def paid_failed_run(self, store):
        """A paid failure: a reserved, executed, FAILED attempt with settled costs and a receipt."""
        spec = self.spec("r1", "nonzero")
        store.register(spec)
        receipt = store.execute(spec["id"])
        self.assertEqual(receipt["run_status"], "FAILED")
        budget = store.snapshot()["budget"]["wall_seconds"]
        self.assertGreater(budget["spent_measured"] + budget["charged_estimate"], 0)
        return receipt

    def bind_root(self, root=None, store=None):
        return bind(store.root if store is not None else (root or self.root))

    # -- binding itself ----------------------------------------------------

    def test_bind_records_append_once_event_and_pointer(self):
        sha_before = file_sha(self.store.path)
        result = bind(self.root)
        self.assertEqual(result["status"], "BOUND")
        pointer = read_pointer(self.root)
        self.assertEqual(pointer["ledger_sha256"], sha_before)
        self.assertEqual(pointer["contract_sha256"], digest(self.contract))
        again = bind(self.root)
        self.assertEqual(again["status"], "ALREADY_BOUND")
        with self.store._db(True) as db:
            events = [json.loads(r["body"]) for r in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='WORKSPACE_BOUND'")]
        self.assertEqual(len(events), 1)  # idempotent recovery does not append a second identity
        self.assertEqual(events[0]["ledger_sha256"], pointer["ledger_sha256"])

    def test_concurrent_binding_yields_one_identity(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self._try_bind(), range(4)))
        # All binders agree on one identity; refusals (if any lost the race)
        # name the conflict rather than recording a second identity.
        pointers = {json.dumps(r["pointer"], sort_keys=True) for r in results
                    if r["status"] in ("BOUND", "ALREADY_BOUND") and r.get("pointer")}
        self.assertEqual(len(pointers), 1)
        refusals = [r for r in results if r["status"] == "REFUSED"]
        for r in refusals:
            self.assertIn("identity", r["reason"])
        with self.store._db(True) as db:
            events = list(db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='WORKSPACE_BOUND'"))
        self.assertEqual(len(events), 1)

    def _try_bind(self):
        try:
            return bind(self.root)
        except ValueError as exc:
            return {"status": "REFUSED", "reason": str(exc)}

    # -- paid failure, then abandonment attempts ---------------------------

    def test_paid_failed_run_then_supersedes_and_separate_project_are_refused(self):
        self.paid_failed_run(self.store)
        self.bind_root(store=self.store)
        before = self._dump(self.root)
        sibling = self.phase("sibling-reset")
        contract = json.loads(canonical(self.contract))
        with self.assertRaisesRegex(ValueError, "--supersedes|second accounting identity"):
            ProjectStore(sibling).initialize(contract, supersedes=str(self.root))
        # A bound workspace declaring a deliberately independent project is refused.
        with self.assertRaisesRegex(ValueError, "--separate-project|second accounting identity"):
            ProjectStore(self.root).initialize(contract, separate_project=True)
        self.assertEqual(self._dump(self.root), before)
        # The paid failure and its settled budget survive unchanged.
        budget = self.store.snapshot()["budget"]["wall_seconds"]
        self.assertGreater(budget["spent_measured"] + budget["charged_estimate"], 0)

    def test_reinit_with_different_contract_bytes_is_refused_and_accounting_unchanged(self):
        self.paid_failed_run(self.store)
        self.bind_root(store=self.store)
        before = self._dump(self.root)
        changed = json.loads(canonical(self.contract))
        changed["budget"]["wall_seconds"] = 999
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.store.initialize(changed)
        self.assertEqual(self._dump(self.root), before)

    def test_replaced_ledger_is_refused_and_original_accounting_survives_elsewhere(self):
        self.paid_failed_run(self.store)
        self.bind_root(store=self.store)
        pointer = read_pointer(self.root)
        # Same contract bytes, new ledger built out of band: the tempting
        # same-contract reset. Supported APIs refuse (initialize above all);
        # only an outside writer can actually replace the file, and the pointer
        # still catches it because the fresh ledger records no bound identity.
        fresh = Path(self.tmp.name) / "same-contract-reset"
        fresh.mkdir()
        for binding in self.contract["bindings"]:
            shutil.copyfile(self.root / binding["path"], fresh / binding["path"])
        ProjectStore(fresh).initialize(self.contract)
        shutil.copyfile(fresh / ".rds" / "project.sqlite3", self.store.path)
        with self.assertRaisesRegex(ValueError, "identity does not match|replaced"):
            check_admission(self.root)
        self.assertEqual(read_pointer(self.root)["event_digest"], pointer["event_digest"])
        self.assertEqual(coverage(self.root)["status"], "MISMATCH")

    def test_missing_ledger_is_refused(self):
        self.bind_root(store=self.store)
        self.store.path.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            check_admission(self.root)
        coverage_now = coverage(self.root)
        self.assertEqual(coverage_now["status"], "MISMATCH")

    def test_stale_pointer_identity_is_reported_as_mismatch(self):
        self.bind_root(store=self.store)
        pointer = read_pointer(self.root)
        stale = {**pointer, "event_digest": "0" * 64}
        pointer_path(self.root).write_text(canonical(stale), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity does not match|replaced"):
            check_admission(self.root)
        self.assertEqual(coverage(self.root)["status"], "MISMATCH")

    def test_pointer_overwrite_is_refused(self):
        self.bind_root(store=self.store)
        pointer = read_pointer(self.root)
        forged = {**pointer, "event_digest": "1" * 64}
        pointer_path(self.root).write_text(canonical(forged), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already bound|different project ledger"):
            bind(self.root)

    # -- canonical continuation -------------------------------------------

    def test_bound_workspace_continues_create_execute_advance_normally(self):
        self.bind_root(store=self.store)
        spec = self.spec("r1", "ok")
        self.store.register(spec)
        receipt = self.store.execute(spec["id"])
        self.assertEqual(receipt["run_status"], "SUCCEEDED")
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["workspace_binding"]["status"], "BOUND")
        self.assertIn("next_command", snapshot["workspace_binding"])

    def test_unbound_legacy_workflow_is_unchanged(self):
        snapshot = self.store.snapshot()
        self.assertNotIn("workspace_binding", snapshot)
        spec = self.spec("r2", "ok")
        self.store.register(spec)
        self.assertEqual(self.store.execute(spec["id"])["run_status"], "SUCCEEDED")
        self.assertEqual(coverage(self.root)["status"], "UNBOUND")

    def test_snapshot_exposes_coverage_and_next_command_when_bound(self):
        self.bind_root(store=self.store)
        cov = self.store.snapshot()["workspace_binding"]
        self.assertEqual(cov["status"], "BOUND")
        cov_view = coverage(self.root)
        self.assertEqual(cov_view["status"], "BOUND")
        self.assertIn("project status", cov_view["next_command"])

    def _dump(self, root):
        """Full ledger rows plus pointer bytes: a refusal must change neither."""
        path = Path(root) / ".rds" / "project.sqlite3"
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            rows = list(db.iterdump())
        finally:
            db.close()
        pointer = Path(root) / POINTER_NAME
        return rows, pointer.read_text(encoding="utf-8") if pointer.is_file() else None

    def test_replaced_ledger_cannot_reserve_or_run_via_register_and_execute(self):
        # P1 review finding: admission must guard every mutating entry, not
        # only initialize, or a replaced ledger can reserve and run work.
        self.paid_failed_run(self.store)
        self.bind_root(store=self.store)
        fresh = Path(self.tmp.name) / "reserve-reset"
        fresh.mkdir()
        for binding in self.contract["bindings"]:
            shutil.copyfile(self.root / binding["path"], fresh / binding["path"])
        ProjectStore(fresh).initialize(self.contract)
        self.store.path.unlink()
        shutil.copyfile(fresh / ".rds" / "project.sqlite3", self.store.path)
        with self.assertRaisesRegex(ValueError, "identity does not match|replaced"):
            self.store.register(self.spec("r9", "ok"))
        with self.assertRaisesRegex(ValueError, "identity does not match|replaced"):
            self.store.execute("r1")
        # Budget still shows only the original paid failure; the reset ledger
        # admitted nothing.
        budget = self.store.snapshot()["budget"]["wall_seconds"]
        self.assertLess(budget["spent_measured"] + budget["charged_estimate"], 5)

    def test_bind_rebuilds_pointer_after_event_committed_before_pointer_write(self):
        # P1 review finding: crash between the event transaction and the
        # pointer write must be recoverable, not a permanent refusal. The
        # rebuilt binding is reported as ALREADY_BOUND: the identity already
        # existed in the ledger; only the pointer file is restored.
        self.bind_root(store=self.store)
        pointer_path(self.root).unlink()
        result = bind(self.root)
        self.assertEqual(result["status"], "ALREADY_BOUND")
        rebuilt = read_pointer(self.root)
        with self.store._db(True) as db:
            events = list(db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='WORKSPACE_BOUND'"))
        self.assertEqual(len(events), 1)  # no second identity event
        # The rebuilt pointer is admitted against the recorded identity.
        # Windows may render the same temp dir as 8.3 or long form; identity is
        # the normalized path, not its rendering.
        self.assertEqual(os.path.normcase(str(check_admission(self.root).root)),
                         os.path.normcase(str(self.root)))
        self.assertEqual(rebuilt["schema"], 1)

    def test_bind_still_refuses_a_genuinely_different_recorded_identity(self):
        self.bind_root(store=self.store)
        pointer_path(self.root).unlink()
        with self.store._db() as db:
            # Simulate a damaged ledger beyond its append-only SQL guards.
            db.execute('DROP TRIGGER events_no_update')
            db.execute("UPDATE events SET body=? WHERE json_extract(body,'$.kind')=?",
                       (canonical({"kind": EVENT_KIND, "assurance": ASSURANCE, "schema": 1,
                                   "event_digest": "2" * 64, "ledger_sha256": "3" * 64,
                                   "contract_sha256": "4" * 64, "root": str(self.root / "elsewhere")}),
                        EVENT_KIND))
        with self.assertRaisesRegex(ValueError, "different workspace identity"):
            bind(self.root)


if __name__ == "__main__":
    unittest.main()
