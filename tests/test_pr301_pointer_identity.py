"""Pointer identity fields must agree with the append-only binding event."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
import test_rds_project


class PointerIdentityTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store, self.spec = helper.root, helper.store, helper.spec
        workspace.bind(self.root)

    def test_corrupt_hash_fields_refuse_observation_and_writes_without_changes(self):
        path = workspace.pointer_path(self.root)
        original = json.loads(path.read_text(encoding='utf-8'))
        for field in ('contract_sha256', 'ledger_sha256'):
            with self.subTest(field=field):
                path.write_text(json.dumps({**original, field: '0' * 64}), encoding='utf-8')
                pointer_before, ledger_before = path.read_bytes(), self.store.path.read_bytes()
                self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
                with self.assertRaisesRegex(ValueError, 'identity'):
                    workspace.check_admission(self.root)
                with self.assertRaisesRegex(ValueError, 'identity'):
                    self.store.register(self.spec('r1', 'ok'))
                self.assertEqual(path.read_bytes(), pointer_before)
                self.assertEqual(self.store.path.read_bytes(), ledger_before)
        path.write_text(json.dumps(original), encoding='utf-8')
        self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')

    def test_consistent_identity_allows_progress_without_rehashing_database(self):
        before = workspace.read_pointer(self.root)
        self.store.register(self.spec('r1', 'ok'))
        after = workspace.read_pointer(self.root)
        self.assertGreater(after['event_id'], before['event_id'])
        for field in ('contract_sha256', 'ledger_sha256', 'event_digest'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')
