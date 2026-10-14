"""User withdrawal is a real launch refusal, not an opaque stale selection."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
from rds_project import ProjectStore
from rds_steering import submit
import test_rds_steering as fixtures


class LaunchRefusalTests(unittest.TestCase):
    def test_late_withdrawal_settles_original_attempt_without_launch(self):
        case = fixtures.SteeringCLITests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.initialize()
        case.create()
        store = ProjectStore(case.root)
        prepare = store._advisor_prepare_run
        calls = []

        def withdraw(*args, **kwargs):
            token = prepare(*args, **kwargs)
            calls.append(token)
            if len(calls) == 3:
                submit(store, case.request('redirect', withdraw=['baseline']),
                       user_directed=True, source='current-user-message:withdraw-before-launch')
            return token

        with patch.object(store, '_advisor_prepare_run', side_effect=withdraw):
            receipt = store.execute('baseline')
        self.assertEqual(len(calls), 3)
        self.assertIs(receipt['process_started'], False)
        self.assertIsNone(receipt['pid'])
        self.assertEqual(receipt['run_status'], 'FAILED')
        self.assertTrue(any('withdrew' in error for error in receipt['errors']))
        self.assertEqual(case.starts(), [])
        before = store.snapshot()
        self.assertEqual(len(before['receipts']), 1)
        self.assertEqual(before['runs'][0]['attempt_id'], receipt['attempt_id'])
        store.recover('baseline')
        after = store.snapshot()
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(after['runs'][0]['attempt_id'], receipt['attempt_id'])
        self.assertEqual(case.starts(), [])


if __name__ == '__main__':
    unittest.main()
