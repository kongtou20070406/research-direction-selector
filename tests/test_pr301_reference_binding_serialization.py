"""Supported reference writers already share binding's full mutation gate."""
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_checkpoints as checkpoints
import rds_workspace as workspace
import test_pr301_reference_checkpoint_admission as fixture
import test_rds_project


class ReferenceBindingSerializationTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store, self.contract = helper.root, helper.store, helper.contract
        reference = fixture.ReferenceCheckpointAdmissionTests()
        self.state, self.snapshot = reference.reference(self.root)

    def test_reference_transaction_holds_binding_out_until_commit(self):
        attempted = threading.Event()

        def bind():
            attempted.set()
            return workspace.bind(self.root)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.state.transaction() as (db, _):
                future = pool.submit(bind)
                self.assertTrue(attempted.wait(3))
                self.assertFalse(future.done())
                self.assertFalse(workspace.pointer_path(self.root).exists())
                self.state.event(db, 'REFERENCE_BEFORE_BIND')
            self.assertEqual(future.result(timeout=5)['status'], 'BOUND')
        before = self.state.db_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'reference accounting is refused'):
            with self.state.transaction():
                self.fail('A bound reference writer must not enter')
        self.assertEqual(self.state.db_path.read_bytes(), before)

    def test_reference_checkpoint_holds_gate_between_check_and_writer(self):
        opened, release, attempted = threading.Event(), threading.Event(), threading.Event()
        original = checkpoints.sqlite3.connect
        target = self.state.db_path.as_uri() + '?mode=rw'

        def connect(database, *args, **kwargs):
            if database == target:
                opened.set()
                if not release.wait(3):
                    raise RuntimeError('Checkpoint publication test release timed out')
            return original(database, *args, **kwargs)

        def bind():
            attempted.set()
            return workspace.bind(self.root)

        with patch('rds_checkpoints.sqlite3.connect', side_effect=connect), \
                ThreadPoolExecutor(max_workers=2) as pool:
            saving = pool.submit(checkpoints.save_checkpoint, self.root, 'before-bind',
                                 self.snapshot, kind='reference')
            try:
                self.assertTrue(opened.wait(3))
                binding = pool.submit(bind)
                self.assertTrue(attempted.wait(3))
                self.assertFalse(binding.done())
                self.assertFalse(workspace.pointer_path(self.root).exists())
            finally:
                release.set()
            self.assertEqual(saving.result(timeout=5)['status'], 'SAVED')
            self.assertEqual(binding.result(timeout=5)['status'], 'BOUND')
        before = self.state.db_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'reference accounting is refused'):
            checkpoints.save_checkpoint(self.root, 'after-bind', self.snapshot, kind='reference')
        self.assertEqual(self.state.db_path.read_bytes(), before)

    def test_reference_writer_waits_for_binding_then_rechecks_admission(self):
        publishing, release, attempted = threading.Event(), threading.Event(), threading.Event()
        publish = workspace._write_pointer

        def paused_publish(root, pointer):
            publishing.set()
            if not release.wait(3):
                raise RuntimeError('Binding publication test release timed out')
            publish(root, pointer)

        def write():
            attempted.set()
            with self.state.transaction() as (db, _):
                self.state.event(db, 'MUST_NOT_WRITE_AFTER_BIND')

        before = self.state.db_path.read_bytes()
        with patch.object(workspace, '_write_pointer', side_effect=paused_publish), \
                ThreadPoolExecutor(max_workers=2) as pool:
            binding = pool.submit(workspace.bind, self.root)
            try:
                self.assertTrue(publishing.wait(3))
                writing = pool.submit(write)
                self.assertTrue(attempted.wait(3))
                self.assertFalse(writing.done())
            finally:
                release.set()
            self.assertEqual(binding.result(timeout=5)['status'], 'BOUND')
            with self.assertRaisesRegex(ValueError, 'reference accounting is refused'):
                writing.result(timeout=5)
        self.assertEqual(self.state.db_path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
