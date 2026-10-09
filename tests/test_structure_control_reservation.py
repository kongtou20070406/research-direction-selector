"""Actual ledger reservations and process interruption, without controller receipts."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore
import rds_structure as structure

spec = importlib.util.spec_from_file_location('reservation_structure_fixture', REPO / 'examples/problem-structure/run.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class StructureControlReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-reserved-control-')
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture.prepare(Path(self.tmp.name) / 'project')
        self.store = ProjectStore(self.root)

    def budget(self, store=None):
        return (store or self.store).snapshot()['budget']['wall_seconds']

    def finished(self, ident):
        return next(e for e in structure._events(self.store)
                    if e['kind'] == structure.PREFIX + 'CONTROL_FINISHED' and e['id'] == ident)

    def test_parent_reserves_full_allowance_and_children_bill_one_total(self):
        before = self.budget()
        with structure.reserved_control(self.store, 'test-parent', 14) as parent:
            reserved = self.budget()
            self.assertEqual(reserved['reserved'], before['reserved'] + 14)
            self.assertEqual(reserved['spent_measured'], before['spent_measured'])
            children = []
            for name in ('first-child', 'second-child'):
                with structure._meter(self.store, name) as child:
                    children.append(child.id)
                    time.sleep(.01)
                    self.assertEqual(self.budget(), reserved)
            self.assertEqual(self.budget(), reserved)
            self.assertEqual(parent.used, 4)
        after = self.budget()
        settled = self.finished(parent.id)
        self.assertEqual(after['reserved'], before['reserved'])
        self.assertAlmostEqual(after['spent_measured'] - before['spent_measured'], settled['wall_seconds'])
        self.assertGreater(settled['wall_seconds'], 0)
        self.assertEqual(after['charged_estimate'], before['charged_estimate'])
        for ident in children:
            event = self.finished(ident)
            self.assertEqual(event['reservation_owner'], parent.id)
            self.assertEqual(event['accounting'], 'PARENT_STRUCTURE_RESERVATION')
        self.assertEqual(self.store.snapshot()['receipts'], [])

    def test_original_worker_receipt_is_paid_separately_from_suspended_parent(self):
        task = structure.request(self.root)['tasks'][0]
        manifest = fixture.proposal(self.root, task)['experiment']['runs'][0]
        self.store.register(manifest)
        before = self.budget()
        with structure.reserved_control(self.store, 'test-worker-parent', 2) as parent:
            with parent.suspend():
                receipt = self.store.execute(manifest['id'])
            self.assertEqual(receipt['run_status'], 'SUCCEEDED')
            self.assertFalse(parent.paused)
            self.assertGreater(parent.suspended, 0)
            worker_paid = self.budget()['spent_measured'] - before['spent_measured']
            self.assertGreater(worker_paid, 0)
            with structure._meter(self.store, 'read-worker-original'):
                self.assertEqual(self.store.snapshot()['receipts'][0]['sha256'], receipt['sha256'])
        after = self.budget()
        self.assertAlmostEqual(after['spent_measured'] - before['spent_measured'],
                               worker_paid + self.finished(parent.id)['wall_seconds'])
        self.assertEqual(after['reserved'], 0)
        self.assertEqual(after['charged_estimate'], before['charged_estimate'])
        self.assertEqual(len(self.store.snapshot()['receipts']), 1)

    def test_worker_pause_exception_restores_controller_and_preserves_cost(self):
        before = self.budget()
        with structure.reserved_control(self.store, 'pause-failure', 2) as parent:
            with self.assertRaisesRegex(RuntimeError, 'wait failed'):
                with parent.suspend():
                    time.sleep(.01)
                    raise RuntimeError('wait failed')
            self.assertFalse(parent.paused)
            with structure._meter(self.store, 'after-wait-failure'):
                pass
        after = self.budget()
        self.assertGreater(parent.suspended, 0)
        self.assertAlmostEqual(after['spent_measured'] - before['spent_measured'],
                               self.finished(parent.id)['wall_seconds'])
        self.assertEqual(after['reserved'], before['reserved'])

    def test_copied_context_cannot_pause_or_borrow_from_another_thread(self):
        with structure.reserved_control(self.store, 'thread-owner', 2) as parent:
            def pause():
                with parent.suspend():
                    self.fail('copied context paused the owning thread')
            def borrow():
                with structure._meter(self.store, 'foreign-thread'):
                    self.fail('copied context borrowed the owning thread allowance')
            with ThreadPoolExecutor(max_workers=1) as pool:
                for action in (pause, borrow):
                    context = copy_context()
                    future = pool.submit(context.run, action)
                    with self.assertRaises(ValueError):
                        future.result(timeout=5)
            self.assertEqual(parent.suspended, 0)
            self.assertFalse(parent.paused)
            self.assertEqual(parent.used, 0)
            with structure._meter(self.store, 'owner-thread'):
                pass
        with self.assertRaises(ValueError):
            with parent.suspend():
                self.fail('released token remained usable')

    def test_phase_limit_and_other_project_fail_without_borrowing_budget(self):
        other = ProjectStore(fixture.prepare(Path(self.tmp.name) / 'other-project'))
        other_before = other.snapshot()
        with structure.reserved_control(self.store, 'limited-parent', 2) as parent:
            with self.assertRaisesRegex(ValueError, 'another project'):
                with structure._meter(other, 'wrong-project'):
                    self.fail('wrong project borrowed reservation')
            self.assertEqual(parent.used, 0)
            with structure._meter(self.store, 'only-phase'):
                pass
            with self.assertRaisesRegex(ValueError, 'Insufficient reserved'):
                with structure._meter(self.store, 'extra-phase'):
                    self.fail('phase cap exceeded')
            self.assertEqual(parent.used, 2)
        self.assertEqual(other.snapshot(), other_before)

    def test_child_exception_preserves_parent_charge_and_original_failure(self):
        before = self.budget()
        with self.assertRaisesRegex(RuntimeError, 'original control failure'):
            with structure.reserved_control(self.store, 'failing-parent', 2) as parent:
                with structure._meter(self.store, 'failing-child') as child:
                    raise RuntimeError('original control failure')
        after = self.budget()
        self.assertEqual(after['reserved'], before['reserved'])
        self.assertAlmostEqual(after['spent_measured'] - before['spent_measured'],
                               self.finished(parent.id)['wall_seconds'])
        self.assertEqual(self.finished(child.id)['reservation_owner'], parent.id)
        self.assertEqual(structure.recover_control(self.root)['reconciled_controls'], 0)

    def test_real_dead_parent_and_child_recover_once_and_live_owner_refuses(self):
        for child in (False, True):
            with self.subTest(child=child):
                root = fixture.prepare(Path(self.tmp.name) / ('crash-child' if child else 'crash-parent'))
                store = ProjectStore(root)
                before = self.budget(store)
                ready, release = root / 'controller-ready', root / 'controller-release'
                code = '''import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/scripts')
from rds_project import ProjectStore
import rds_structure as structure
root = Path(sys.argv[2])
def exit_after_release():
    (root / 'controller-ready').touch()
    deadline = time.monotonic() + 15
    while not (root / 'controller-release').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('test release barrier expired')
        time.sleep(.01)
    os._exit(73)
with structure.reserved_control(ProjectStore(root), 'crashed-parent', 14):
    if sys.argv[3] == 'child':
        with structure._meter(ProjectStore(root), 'crashed-child'):
            exit_after_release()
    else:
        exit_after_release()
'''
                process = subprocess.Popen([sys.executable, '-B', '-c', code, str(REPO), str(root),
                                            'child' if child else 'parent'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 10
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(ready.exists(), 'real controller did not reach held reservation')
                    live = store.snapshot()
                    self.assertEqual(self.budget(store)['reserved'], before['reserved'] + 14)
                    with self.assertRaisesRegex(ValueError, 'may still be active'):
                        structure.recover_control(root)
                    self.assertEqual(store.snapshot(), live)
                    release.touch()
                    _, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 73, stderr.decode('utf-8', errors='replace'))
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
                recovered = structure.recover_control(root)
                self.assertEqual(recovered['reconciled_controls'], 2 if child else 1)
                after = self.budget(store)
                self.assertEqual(after['reserved'], before['reserved'])
                self.assertEqual(after['charged_estimate'], before['charged_estimate'] + 14)
                self.assertEqual(after['spent_measured'], before['spent_measured'])
                events = [e for e in structure._events(store) if e['kind'] == structure.PREFIX + 'CONTROL_FINISHED']
                self.assertEqual(sum(e['charged_estimate'] for e in events), 14)
                self.assertTrue(all(e['status'] == 'UNKNOWN' for e in events))
                settled = store.snapshot()
                self.assertEqual(structure.recover_control(root)['reconciled_controls'], 0)
                self.assertEqual(store.snapshot(), settled)
                self.assertEqual(settled['runs'], [])
                self.assertEqual(settled['receipts'], [])


if __name__ == '__main__':
    unittest.main()
