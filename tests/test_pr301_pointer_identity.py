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

    def test_missing_or_null_anchor_cannot_admit_restored_accounting(self):
        path = workspace.pointer_path(self.root)
        bound_bytes = self.store.path.read_bytes()
        self.store.register(self.spec('r1', 'ok'))
        self.assertGreater(self.store.snapshot()['budget']['wall_seconds']['reserved'], 0)
        pointer = json.loads(path.read_text(encoding='utf-8'))
        self.store.path.write_bytes(bound_bytes)
        for omitted in (True, False):
            with self.subTest(omitted=omitted):
                damaged = {key: value for key, value in pointer.items() if key != 'event_id'}
                if not omitted:
                    damaged['event_id'] = None
                path.write_text(json.dumps(damaged), encoding='utf-8')
                before = path.read_bytes(), self.store.path.read_bytes()
                self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
                with self.assertRaisesRegex(ValueError, 'progress anchor'):
                    self.store.register(self.spec('r1', 'ok'))
                self.assertEqual((path.read_bytes(), self.store.path.read_bytes()), before)

    def test_anchor_type_and_positive_integer_are_required(self):
        path = workspace.pointer_path(self.root)
        pointer = json.loads(path.read_text(encoding='utf-8'))
        for anchor in (False, True, 0, -1, 1.0, '1'):
            with self.subTest(anchor=anchor):
                path.write_text(json.dumps({**pointer, 'event_id': anchor}), encoding='utf-8')
                self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
