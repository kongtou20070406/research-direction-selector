"""Ancestor binding must not revoke an outstanding descendant reservation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
from rds_project import ProjectStore
import test_rds_project as fixture


class DescendantBindingTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixture.ProjectTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.root, self.store = self.helper.root, self.helper.store

    def child(self, name='nested/deep'):
        root = self.root / name
        root.mkdir(parents=True)
        for binding in self.helper.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], root / binding['path'])
        store = ProjectStore(root)
        store.initialize(deepcopy(self.helper.contract))
        return store

    def dump(self, store):
        with store._db(True) as db:
            rows = list(db.iterdump())
        pointer = workspace.pointer_path(store.root)
        return rows, pointer.read_bytes() if pointer.exists() else None

    def running(self, own_pointer=False, pause_at='_finish'):
        child = self.child()
        if own_pointer:
            workspace.bind(child.root)
        child.register(self.helper.spec())
        entered, release = threading.Event(), threading.Event()
        original = getattr(ProjectStore, pause_at)

        def paused(store, *args, **kwargs):
            if store.root == child.root:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('Descendant release deadline exceeded')
            return original(store, *args, **kwargs)

        with patch.object(ProjectStore, pause_at, new=paused), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(child.execute, 'r1')
            try:
                self.assertTrue(entered.wait(3))
                run = child.snapshot()['runs'][0]
                self.assertEqual(run['status'], 'RUNNING' if pause_at == '_finish' else 'RESERVED')
                self.assertIsNotNone(run['attempt_id'])
                before, child_before = self.dump(self.store), self.dump(child)
                refusal = None
                try:
                    workspace.bind(self.root)
                except ValueError as exc:
                    refusal = str(exc)
                print('ancestor bind:', refusal or 'UNEXPECTED BOUND', flush=True)
                if refusal is not None:
                    self.assertEqual(self.dump(child), child_before)
            finally:
                release.set()
            try:
                receipt = future.result(timeout=5)
            except ValueError as exc:
                print('original settlement failed:', str(exc), flush=True)
                try:
                    child.recover('r1')
                except ValueError as recovery:
                    print('original recovery failed:', str(recovery), flush=True)
                print('stranded budget:', child.snapshot()['budget'], flush=True)
                raise
        self.assertIsNotNone(refusal, 'Initial ancestor binding must refuse active descendants')
        self.assertIn('active descendant', refusal)
        self.assertEqual(self.dump(self.store), before)
        self.assertEqual(receipt['attempt_id'], run['attempt_id'])
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(child.recover('r1')['sha256'], receipt['sha256'])
        settled = child.snapshot()
        self.assertEqual(len(settled['runs']), 1)
        self.assertEqual(len(settled['receipts']), 1)
        self.assertEqual(settled['budget']['wall_seconds']['reserved'], 0)
        self.assertGreater(settled['budget']['wall_seconds']['spent_measured'], 0)
        print('settled same attempt/receipt:', run['attempt_id'], receipt['sha256'], flush=True)
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_running_descendant_settles_same_attempt_after_refusal(self):
        self.running()

    def test_running_descendant_with_own_pointer_is_protected(self):
        self.running(own_pointer=True)

    def test_admitted_pending_attempt_is_protected(self):
        self.running(pause_at='_execute_claim')

    def test_unstarted_reservation_refusal_has_no_side_effects(self):
        child = self.child()
        child.register(self.helper.spec())
        before = self.dump(self.store), self.dump(child)
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            workspace.bind(self.root)
        self.assertEqual((self.dump(self.store), self.dump(child)), before)

    def test_quiescent_completed_and_failed_descendants_allow_binding(self):
        for name, mode in [('completed', 'ok'), ('failed', 'nonzero')]:
            child = self.child(name)
            child.register(self.helper.spec(mode=mode))
            child.execute('r1')
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_reference_reservation_can_cancel_after_ancestor_refusal(self):
        import rds_cli as cli
        import test_pr301_reference_checkpoint_admission as reference_fixture

        helper = reference_fixture.ReferenceCheckpointAdmissionTests()
        child, _ = helper.reference(self.root / 'reference-child')
        before = self.dump(self.store), child.db_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            workspace.bind(self.root)
        self.assertEqual((self.dump(self.store), child.db_path.read_bytes()), before)
        self.assertEqual(cli.cmd_cancel(argparse.Namespace(id='P1'), child)['run_status'], 'CANCELLED')
        with child.snapshot() as (_, state):
            self.assertEqual(state['budget']['reserved']['runs'], 0)
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_theory_allowance_can_settle_after_ancestor_refusal(self):
        child = self.child()
        allowance = {key: min(1, cap) for key, cap in self.helper.contract['budget'].items()}
        with child.theory_allowance(self.helper.spec(), {'fixture': 'active theory'}, allowance) as record:
            before = self.dump(self.store), self.dump(child)
            with self.assertRaisesRegex(ValueError, 'active descendant'):
                workspace.bind(self.root)
            self.assertEqual((self.dump(self.store), self.dump(child)), before)
            record['status'] = 'PASS'
        outcome = child.theory_record(record['attempt_id'])
        self.assertEqual(outcome['kind'], 'THEORY_OUTCOME')
        self.assertEqual(outcome['status'], 'PASS')
        self.assertEqual(child.snapshot()['budget']['wall_seconds']['charged_estimate'],
                         allowance['wall_seconds'] + outcome['wall_overrun_seconds'])
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_running_reference_can_finish_after_binding_refusal(self):
        import rds_cli as cli
        import test_pr301_reference_checkpoint_admission as reference_fixture

        helper = reference_fixture.ReferenceCheckpointAdmissionTests()
        state, _ = helper.reference(self.root)
        run_process = subprocess.run
        observed = []

        def run(argv, *args, **kwargs):
            if Path(argv[-1]).name == 'rds_probe.py':
                with state.snapshot() as (_, snapshot):
                    self.assertEqual(snapshot['plans']['P1']['run_status'], 'RUNNING')
                    before_spent = snapshot['budget']['spent']
                before = self.dump(self.store), state.db_path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'active descendant'):
                    workspace.bind(self.root)
                self.assertEqual((self.dump(self.store), state.db_path.read_bytes()), before)
                observed.append(before_spent)
            return run_process(argv, *args, **kwargs)

        with patch.object(cli.subprocess, 'run', side_effect=run):
            receipt = cli.cmd_run(argparse.Namespace(id='P1'), state)
        self.assertEqual(len(observed), 1)
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        with state.snapshot() as (db, snapshot):
            self.assertEqual(snapshot['budget']['spent'], observed[0])
            self.assertEqual(snapshot['plans']['P1']['run_status'], 'SUCCEEDED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 1)
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    @unittest.skipUnless(os.name == 'nt', 'Windows case-insensitive paths')
    def test_windows_metadata_casing_cannot_hide_reservation(self):
        root = self.root / 'case-child'
        (root / '.RDS').mkdir(parents=True)
        for binding in self.helper.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], root / binding['path'])
        child = ProjectStore(root)
        child.initialize(deepcopy(self.helper.contract))
        child.register(self.helper.spec())
        before = self.dump(self.store), self.dump(child)
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            workspace.bind(self.root)
        self.assertEqual((self.dump(self.store), self.dump(child)), before)

    def test_symlink_outside_root_is_not_scanned(self):
        external = fixture.ProjectTests()
        external.setUp()
        self.addCleanup(external.tearDown)
        external.store.register(external.spec())
        link = self.root / 'outside'
        try:
            link.symlink_to(external.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest('Symlinks unavailable: ' + str(exc))
        self.addCleanup(link.unlink)
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_paid_quick_pending_child_keeps_idempotent_owner_binding(self):
        from rds_quick import _charge_ledger
        self.store = self.helper.new_store(budget={'wall_seconds': 20})
        self.root = self.store.root
        workspace.bind(self.root)
        root = self.root / '.rds' / 'exec' / 'paid'
        root.mkdir(parents=True)
        for binding in self.helper.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], root / binding['path'])
        request = {'operation': 'declared QUICK request'}
        (root / 'rds-exec-request.json').write_text(json.dumps(request), encoding='utf-8')
        child = ProjectStore(root)
        contract = deepcopy(self.helper.contract)
        contract['budget'] = {'wall_seconds': 5}
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            _charge_ledger(self.root, root, request, 5, _parent_db=db)
            child.initialize(contract, fresh_workspace=self.store,
                             _fresh_workspace_admission=(db, request))
        workspace.bind(root)
        spec = self.helper.spec()
        spec['resource_estimates'] = {'wall_seconds': 2.2}
        child.register(spec)
        before = self.dump(self.store), self.dump(child)
        self.assertEqual(workspace.bind(self.root)['status'], 'ALREADY_BOUND')
        self.assertEqual((self.dump(self.store), self.dump(child)), before)
        receipt = child.execute('r1')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(child.recover('r1')['sha256'], receipt['sha256'])

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_windows_junction_outside_root_is_not_scanned(self):
        external = fixture.ProjectTests()
        external.setUp()
        self.addCleanup(external.tearDown)
        external.store.register(external.spec())
        link = self.root / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(external.root)],
                                capture_output=True, timeout=3,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.addCleanup(os.rmdir, link)
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')


if __name__ == '__main__':
    unittest.main()
