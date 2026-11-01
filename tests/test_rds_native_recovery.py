"""Receipt recovery is a native ledger operation, not an optional plugin hook."""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from rds_project import ProjectStore
import rds_project_hooks as hooks
import rds_quick_ledger  # Load the compatibility registration before poisoning it.
import test_rds_project


class NativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_rds_project.ProjectTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store = self.fixture.store
        self.callback = Mock(side_effect=AssertionError('optional QUICK provider invoked'))
        guard = patch.dict(hooks._providers, {'quick': SimpleNamespace(_recover_prospective=self.callback)})
        guard.start()
        self.addCleanup(guard.stop)

    def test_retained_receipt_uses_native_recovery_without_reexecution(self):
        self.store.register(self.fixture.spec())
        receipt = self.store.execute('r1')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        before = self.store.snapshot()
        with self.store._db(True) as db:
            original_events = list(db.execute('SELECT body FROM events ORDER BY id'))
        with patch.object(ProjectStore, 'execute', side_effect=AssertionError('rerun')), \
                patch.object(ProjectStore, '_finish', side_effect=AssertionError('resettlement')):
            self.assertEqual(self.store.recover('r1'), receipt)
            self.assertEqual(self.store.recover('r1'), receipt)
        self.callback.assert_not_called()
        self.assertEqual(self.store.snapshot(), before)
        with self.store._db(True) as db:
            self.assertEqual(list(db.execute('SELECT body FROM events ORDER BY id')), original_events)

    def test_interrupted_recovery_settles_once_without_optional_provider(self):
        self.store.register(self.fixture.spec())
        with self.store._db() as db:
            run = self.store._run(db, 'r1')
            run.update(status='RUNNING', attempt_id='lost-native-recovery', worker_pid=None, pid=None,
                       started_at=time.time() - 2, observed_wall_seconds=0.3)
            self.store._save(db, run)
        with patch.object(ProjectStore, 'execute', side_effect=AssertionError('rerun')):
            receipt = self.store.recover('r1')
            after = self.store.snapshot()
            self.assertEqual(self.store.recover('r1'), receipt)
        self.callback.assert_not_called()
        self.assertEqual(receipt['run_status'], 'INTERRUPTED')
        self.assertIsNone(receipt['resources']['wall_seconds']['measured'])
        self.assertEqual(receipt['resources']['wall_seconds']['charged_estimate'], 2.2)
        self.assertEqual(receipt['resources']['wall_seconds']['observed_lower_bound'], 0.3)
        self.assertEqual(self.store.snapshot(), after)
        self.assertEqual(len(after['receipts']), 1)
        self.assertFalse((self.fixture.root / 'outputs/r1.json').exists())
        with self.store._db(True) as db:
            finished = [json.loads(row['body']) for row in db.execute('SELECT body FROM events')
                        if json.loads(row['body']).get('kind') == 'ATTEMPT_FINISHED']
        self.assertEqual(len(finished), 1)


if __name__ == '__main__':
    unittest.main()
