"""Native coherent state reads without importing decision/controller services."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_rds_project as fixture
from rds_owned_history import read_state
from rds_project_read_services import ProjectReadServices

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class OwnedStateTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.ProjectTests('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.store = self.fx.store

    def cold_read(self, code):
        prefix = "import sys; sys.path.insert(0, sys.argv[1]); "
        result = subprocess.run([sys.executable, '-I', '-B', '-c', prefix + code,
                                 str(SCRIPTS), str(self.store.root)],
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        return result

    def test_reader_import_does_not_load_planners(self):
        self.cold_read("import rds_owned_history; assert not "
                       "{'rds_owned_advisor','rds_autonomy','rds_quick','rds_cli'}"
                       ".intersection(sys.modules)")

    def test_real_dialogue_snapshot_does_not_load_advisor_or_controller(self):
        self.store.register(self.fx.spec('r1'))
        before = self.store.snapshot()
        result = self.cold_read("from rds_project import ProjectStore; import json; "
            "value=ProjectStore(sys.argv[2]).snapshot(_dialogue=True); "
            "assert 'error' not in value['_dialogue_context'], value['_dialogue_context']; "
            "assert not {'rds_owned_advisor','rds_autonomy','rds_quick','rds_cli'}"
            ".intersection(sys.modules); "
            "print(json.dumps(value['_dialogue_context']['state']))")
        state = json.loads(result.stdout)
        self.assertEqual(state['contract'], before['contract'])
        self.assertEqual(state['runs'], before['runs'])
        self.assertEqual(self.store.snapshot(), before)

    def test_same_snapshot_and_legacy_entry_keep_original_values(self):
        import rds_owned_advisor as advisor
        self.store.register(self.fx.spec('r1'))
        with self.store._db(True) as db:
            db.execute('BEGIN')
            before = db.total_changes
            actual = read_state(self.store, db)
            self.assertEqual(actual, advisor._state(self.store, db))
            self.assertEqual(advisor._fingerprint(actual),
                             advisor._fingerprint(advisor._state(self.store, db)))
            actual['runs'][0]['status'] = 'INVENTED'
            self.assertEqual(read_state(self.store, db)['runs'][0]['status'], 'RESERVED')
            self.assertTrue(db.in_transaction)
            self.assertEqual(db.total_changes, before)

    def test_dialogue_read_does_not_call_compatibility_provider(self):
        import rds_owned_advisor as advisor
        with patch.object(advisor, '_state', side_effect=AssertionError('planner callback')):
            value = self.store.snapshot(_dialogue=True)
        self.assertNotIn('error', value['_dialogue_context'])

    def test_terminal_receipt_requires_original_completion_event(self):
        receipt = self.fx.run_spec(self.fx.spec('r1'))
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        before = self.store.snapshot()
        with self.store._db(True) as db:
            state = read_state(self.store, db)
            self.assertEqual(state['completion_events'], [receipt['sha256']])
            with patch.object(ProjectReadServices, 'has_completion', return_value=False):
                with self.assertRaisesRegex(ValueError, 'no matching completion event'):
                    read_state(self.store, db)
            self.assertEqual(db.total_changes, 0)
        self.assertEqual(self.store.snapshot(), before)

    def test_checkpoint_failure_retains_original_repair_message(self):
        with self.store._db(True) as db:
            with patch('rds_owned_history.history_cut', side_effect=ValueError('damaged checkpoint')):
                with self.assertRaisesRegex(ValueError,
                        'Repair checkpoint integrity before executing a candidate: damaged checkpoint'):
                    read_state(self.store, db)
            self.assertEqual(db.total_changes, 0)


if __name__ == '__main__':
    unittest.main()
