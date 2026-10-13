"""Preparation must precede both parent and child write-lock acquisition."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_rds_owned_advisor as fixtures
from rds_admission_read import StaleAdmission
from rds_project import ProjectStore, canonical


class ExecutePrevalidationTests(unittest.TestCase):
    def fixture(self):
        value = fixtures.OwnedAdvisorCLITests('runTest')
        value.setUp()
        self.addCleanup(value.doCleanups)
        value.initialize()
        store = ProjectStore(value.root)
        store.register(value.manifests['baseline'])
        return value, store

    def test_prepare_before_real_parent_lock_and_guard_before_attempt(self):
        child, store = self.fixture()
        _, parent = self.fixture()
        order, active = [], []

        @contextmanager
        def parent_context():
            with parent._db() as db:
                db.execute('BEGIN IMMEDIATE')
                active.append(db)
                order.append('parent-lock')
                try:
                    yield
                finally:
                    order.append('parent-release')
                    active.clear()

        def prepare(name, original):
            def check(*args, **kwargs):
                self.assertFalse(active, name + ' runs under the parent write lock')
                # Actual independent writers can acquire both files here.
                for path in (parent.path, store.path):
                    db = sqlite3.connect(path, timeout=0)
                    try:
                        db.execute('BEGIN IMMEDIATE')
                        db.rollback()
                    finally:
                        db.close()
                order.append(name)
                return original(*args, **kwargs)
            return check

        def guard(db, run):
            self.assertTrue(db.in_transaction)
            self.assertTrue(active[0].in_transaction)
            self.assertIsNone(run['attempt_id'])
            order.append('guard')

        with patch.object(store, '_advisor_prepare_run',
                          side_effect=prepare('advisor', store._advisor_prepare_run)):
            receipt = store.execute('baseline', admission_context=parent_context, admission_guard=guard)
        self.assertEqual(order[:4], ['advisor', 'parent-lock', 'guard', 'parent-release'])
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(child.starts(), ['baseline'])
        before = store.snapshot()
        self.assertEqual(store.recover('baseline'), receipt)
        self.assertEqual(store.snapshot()['budget'], before['budget'])
        self.assertEqual(child.starts(), ['baseline'])

    def test_changed_state_after_preparation_is_stale_without_attempt_or_recharge(self):
        child, store = self.fixture()
        before = store.snapshot()

        @contextmanager
        def concurrent_write():
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO events(body) VALUES (?)',
                           (canonical({'kind': 'SYNTHETIC_CONCURRENT_EVENT'}),))
            yield

        with patch.object(store, '_execute_claim', side_effect=AssertionError('must not launch')):
            with self.assertRaises(StaleAdmission):
                store.execute('baseline', admission_context=concurrent_write)
        after = store.snapshot()
        for key in ('budget', 'runs', 'receipts', 'exposures', 'contract_sha256'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(child.starts(), [])

    def test_failed_preparation_never_enters_parent_context(self):
        _, store = self.fixture()
        before = store.snapshot()

        @contextmanager
        def forbidden_context():
            self.fail('Parent context entered before failed preparation')
            yield

        with patch.object(store, '_advisor_prepare_run', side_effect=ValueError('prepare refused')):
            with self.assertRaisesRegex(ValueError, 'prepare refused'):
                store.execute('baseline', admission_context=forbidden_context)
        self.assertEqual(store.snapshot(), before)

    def test_context_refusal_preserves_reserved_run_without_start(self):
        child, store = self.fixture()
        before = store.snapshot()

        @contextmanager
        def refusal():
            raise ValueError('parent refused')
            yield

        with self.assertRaisesRegex(ValueError, 'parent refused'):
            store.execute('baseline', admission_context=refusal)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(child.starts(), [])


if __name__ == '__main__':
    unittest.main()
