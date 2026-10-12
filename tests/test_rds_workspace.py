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
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import test_rds_project  # noqa: E402  (module import keeps its TestCase out of this module's discovery)
import rds_workspace  # noqa: E402
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

    def test_committed_spend_is_anchored_without_a_later_read(self):
        bind(self.root)
        backup = Path(self.tmp.name) / 'post-bind.sqlite3'
        shutil.copyfile(self.store.path, backup)
        spec = self.spec('r1', 'nonzero')
        self.store.register(spec)
        self.assertEqual(self.store.execute(spec['id'])['run_status'], 'FAILED')
        # No status/admission read may advance the pointer before restoring.
        shutil.copyfile(backup, self.store.path)
        before = self._dump(self.root)
        with self.assertRaisesRegex(ValueError, 'snapshot was restored'):
            self.store.register(self.spec('r2', 'ok'))
        self.assertEqual(self._dump(self.root), before)

    def test_controller_refuses_replacement_before_budget_mutation(self):
        from rds_autonomy import _claim
        bind(self.root)
        fresh = Path(self.tmp.name) / 'controller-reset'
        fresh.mkdir()
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], fresh / binding['path'])
        ProjectStore(fresh).initialize(self.contract)
        shutil.copyfile(fresh / '.rds/project.sqlite3', self.store.path)
        before = self._dump(self.root)
        with patch.object(self.store, '_contract', return_value={'advisor_policy': {}}):
            with self.assertRaisesRegex(ValueError, 'identity does not match|replaced'):
                _claim(self.store, controller_wall_seconds=1)
        self.assertEqual(self._dump(self.root), before)

    def test_pointer_publication_failure_rolls_back_budget_and_events(self):
        bind(self.root)
        before = self._dump(self.root)
        with patch('rds_workspace._write_pointer', side_effect=OSError('pointer unavailable')):
            with self.assertRaisesRegex(OSError, 'pointer unavailable'):
                self.store.register(self.spec('r1', 'ok'))
        self.assertEqual(self._dump(self.root), before)

    def test_posix_directory_sync_failure_rolls_back_and_refuses_admission(self):
        self._assert_directory_sync_failure(explicit=False)

    def test_posix_directory_sync_failure_at_explicit_commit_is_failclosed(self):
        self._assert_directory_sync_failure(explicit=True)

    def _assert_directory_sync_failure(self, explicit):
        bind(self.root)
        before_rows, before_pointer = self._dump(self.root)
        with patch('rds_workspace.WINDOWS', False), \
                patch('rds_workspace.os.O_DIRECTORY', 0, create=True), \
                patch('rds_workspace._sync_directory',
                      side_effect=OSError('directory sync failed')):
            with self.assertRaisesRegex(OSError, 'directory sync failed'):
                if explicit:
                    with self.store._db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        db.execute("UPDATE budget SET reserved=reserved+1 WHERE resource='wall_seconds'")
                        db.commit()
                else:
                    self.store.register(self.spec('r1', 'ok'))
        rows, pointer = self._dump(self.root)
        self.assertEqual(rows, before_rows)
        self.assertNotEqual(pointer, before_pointer)
        with self.assertRaisesRegex(ValueError, 'snapshot was restored'):
            check_admission(self.root)
        with self.assertRaisesRegex(ValueError, 'snapshot was restored'):
            bind(self.root)
        self.assertEqual(coverage(self.root)['status'], 'MISMATCH')
        self.assertEqual(list(self.root.glob(f'{POINTER_NAME}.*.tmp')), [])

    def test_windows_write_through_failure_preserves_pointer_and_rolls_back(self):
        bind(self.root)
        before = self._dump(self.root)
        with patch('rds_workspace.WINDOWS', True), \
                patch('rds_workspace._replace_write_through',
                      side_effect=OSError('write-through failed')):
            with self.assertRaisesRegex(OSError, 'write-through failed'):
                self.store.register(self.spec('r1', 'ok'))
        self.assertEqual(self._dump(self.root), before)
        self.assertEqual(coverage(self.root)['status'], 'BOUND')
        self.assertEqual(list(self.root.glob(f'{POINTER_NAME}.*.tmp')), [])

    def test_file_sync_failure_preserves_pointer_and_rolls_back(self):
        bind(self.root)
        before = self._dump(self.root)
        with patch('rds_workspace.os.fsync', side_effect=OSError('file sync failed')):
            with self.assertRaisesRegex(OSError, 'file sync failed'):
                self.store.register(self.spec('r1', 'ok'))
        self.assertEqual(self._dump(self.root), before)
        self.assertEqual(list(self.root.glob(f'{POINTER_NAME}.*.tmp')), [])

    def test_posix_pointer_publication_syncs_file_then_replace_then_directory(self):
        bind(self.root)
        pointer = read_pointer(self.root)
        order = []
        real_fsync, real_replace = os.fsync, os.replace

        def file_sync(descriptor):
            real_fsync(descriptor)
            order.append('file')

        def replace(source, target):
            real_replace(source, target)
            order.append('replace')

        def directory_sync(directory):
            self.assertEqual(directory, self.root)
            self.assertEqual(read_pointer(self.root), pointer)
            order.append('directory')

        with patch('rds_workspace.WINDOWS', False), \
                patch('rds_workspace.os.O_DIRECTORY', 0, create=True), \
                patch('rds_workspace.os.fsync', side_effect=file_sync), \
                patch('rds_workspace.os.replace', side_effect=replace), \
                patch('rds_workspace._sync_directory', side_effect=directory_sync):
            rds_workspace._write_pointer(self.root, pointer)
        self.assertEqual(order, ['file', 'replace', 'directory'])

    def test_explicit_commit_anchors_reservation_and_reads_do_not_write(self):
        bind(self.root)
        initial = self._dump(self.root)
        check_admission(self.root)
        coverage(self.root)
        self.assertEqual(self._dump(self.root), initial)
        backup = Path(self.tmp.name) / 'before-reservation.sqlite3'
        shutil.copyfile(self.store.path, backup)
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE budget SET reserved=reserved+1 WHERE resource='wall_seconds'")
            db.commit()
        shutil.copyfile(backup, self.store.path)
        before = self._dump(self.root)
        with self.assertRaisesRegex(ValueError, 'snapshot was restored'):
            check_admission(self.root)
        self.assertEqual(self._dump(self.root), before)

    def test_idempotent_bind_refuses_a_copied_root_without_mutation(self):
        bind(self.root)
        copied = Path(self.tmp.name) / 'copied-bound-root'
        shutil.copytree(self.root, copied)
        before = self._dump(copied)
        with self.assertRaisesRegex(ValueError, 'copy of a bound workspace'):
            bind(copied)
        self.assertEqual(coverage(copied)['status'], 'MISMATCH')
        self.assertEqual(self._dump(copied), before)

    def test_idempotent_bind_refuses_restored_snapshot_without_lowering_anchor(self):
        bind(self.root)
        backup = Path(self.tmp.name) / 'idempotent-post-bind.sqlite3'
        shutil.copyfile(self.store.path, backup)
        self.store.register(self.spec('r1', 'ok'))
        pointer = read_pointer(self.root)
        shutil.copyfile(backup, self.store.path)
        before = self._dump(self.root)
        with self.assertRaisesRegex(ValueError, 'snapshot was restored'):
            bind(self.root)
        self.assertEqual(self._dump(self.root), before)
        self.assertEqual(read_pointer(self.root), pointer)
        self.assertEqual(coverage(self.root)['status'], 'MISMATCH')

    def test_corrupt_bound_ledger_coverage_retains_reason_without_writes(self):
        bind(self.root)
        self.store.path.write_bytes(b'not a SQLite database')
        before = self.store.path.read_bytes(), pointer_path(self.root).read_bytes()
        result = coverage(self.root)
        self.assertEqual(result['status'], 'MISMATCH')
        self.assertIn('not a database', result['mismatch'])
        self.assertIsNone(result['next_command'])
        self.assertEqual((self.store.path.read_bytes(), pointer_path(self.root).read_bytes()), before)

    def test_unreadable_bound_ledger_coverage_retains_reason(self):
        bind(self.root)
        before = self._dump(self.root)
        for error in (sqlite3.OperationalError('unable to open database file'),
                      PermissionError('ledger access denied')):
            with self.subTest(error=error):
                with patch('rds_workspace._recorded_event', side_effect=error):
                    result = coverage(self.root)
                self.assertEqual(result['status'], 'MISMATCH')
                self.assertEqual(result['mismatch'], str(error))
                self.assertIsNone(result['next_command'])
        self.assertEqual(self._dump(self.root), before)

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

    def _identity(self, root):
        """Ledger rows plus the pointer's identity fields: accounting identity.

        The pointer's `event_id` anchor is monotone admission bookkeeping — it
        moves forward on supported reads — so it is excluded here. A refusal
        must change the identity and the accounting, never the anchor's
        meaning.
        """
        rows, pointer_text = self._dump(root)
        pointer = json.loads(pointer_text) if pointer_text else None
        identity = {k: v for k, v in pointer.items() if k != "event_id"} if pointer else None
        return rows, identity

    def test_reinit_with_different_contract_bytes_is_refused_and_accounting_unchanged(self):
        self.paid_failed_run(self.store)
        self.bind_root(store=self.store)
        check_admission(self.root)  # settle the pointer's progress anchor first
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

    def test_fresh_child_requires_a_live_bound_enclosing_owner(self):
        child = self.root / '.rds' / 'exec' / 'fresh-child'
        child.mkdir(parents=True)
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], child / binding['path'])
        with self.assertRaisesRegex(ValueError, 'enclosing bound owner'):
            ProjectStore(child).initialize(self.contract, fresh_workspace=self.store)
        self.assertFalse(ProjectStore(child).path.exists())
        bind(self.root)
        self.store.path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            ProjectStore(child).initialize(self.contract, fresh_workspace=self.store)
        self.assertFalse(ProjectStore(child).path.exists())

    def test_empty_child_lock_anchor_cannot_hide_enclosing_binding(self):
        bind(self.root)
        child = self.root / '.rds' / 'exec' / 'empty-anchor'
        child.mkdir(parents=True)
        child_store = ProjectStore(child)
        child_store.state_dir.mkdir(parents=True)
        sqlite3.connect(child_store.path).close()
        self.assertEqual(read_pointer(child)['event_digest'], read_pointer(self.root)['event_digest'])
        with self.assertRaisesRegex(ValueError, 'identity does not match'):
            child_store.initialize(self.contract)

    def test_deep_empty_anchor_inherits_binding_and_refuses_successor(self):
        bind(self.root)
        before = self._dump(self.root)
        intermediate = self.root / 'B'
        anchor = intermediate / '.rds'
        anchor.mkdir(parents=True)
        successor = intermediate / 'C'
        successor.mkdir()
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], successor / binding['path'])
        successor_store = ProjectStore(successor)
        self.assertEqual(list(anchor.iterdir()), [])
        self.assertEqual(read_pointer(successor), read_pointer(self.root))
        self.assertEqual(coverage(successor)['status'], 'MISMATCH')
        for options, reason in (({'separate_project': True}, '--separate-project'),
                                ({'supersedes': str(self.root)}, '--supersedes')):
            with self.subTest(options=options):
                with self.assertRaisesRegex(ValueError, reason):
                    successor_store.initialize(self.contract, **options)
                self.assertFalse(successor_store.state_dir.exists())
                self.assertFalse(pointer_path(successor).exists())
                self.assertEqual(list(anchor.iterdir()), [])
                self.assertEqual(self._dump(self.root), before)

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
        # the resolved path, not its rendering.
        self.assertEqual(os.path.normcase(str(check_admission(self.root).root.resolve())),
                         os.path.normcase(str(self.root.resolve())))
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


class WorkspacePublicationTests(unittest.TestCase):
    def test_windows_replacement_requests_write_through_without_copy_fallback(self):
        with patch('ctypes.WinDLL', create=True) as library:
            move = library.return_value.MoveFileExW
            move.return_value = 1
            rds_workspace._replace_write_through(Path('source.tmp'), Path('pointer.json'))
        library.assert_called_once_with('kernel32', use_last_error=True)
        move.assert_called_once_with('source.tmp', 'pointer.json', 0x1 | 0x8)

    def test_windows_replacement_error_propagates_without_fallback(self):
        error = OSError('Windows denied replacement')
        with patch('ctypes.WinDLL', create=True) as library, \
                patch('ctypes.get_last_error', return_value=5, create=True), \
                patch('ctypes.WinError', return_value=error, create=True) as win_error:
            move = library.return_value.MoveFileExW
            move.return_value = 0
            with self.assertRaises(OSError) as caught:
                rds_workspace._replace_write_through(Path('source.tmp'), Path('pointer.json'))
        self.assertIs(caught.exception, error)
        win_error.assert_called_once_with(5)
        self.assertEqual(move.call_count, 1)

    def test_directory_sync_error_propagates_and_closes_descriptor(self):
        with patch('rds_workspace.os.O_DIRECTORY', 0, create=True), \
                patch('rds_workspace.os.open', return_value=123) as opened, \
                patch('rds_workspace.os.fsync', side_effect=OSError('directory unavailable')) as synced, \
                patch('rds_workspace.os.close') as closed:
            with self.assertRaisesRegex(OSError, 'directory unavailable'):
                rds_workspace._sync_directory(Path('workspace'))
        opened.assert_called_once_with(Path('workspace'), os.O_RDONLY)
        synced.assert_called_once_with(123)
        closed.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
