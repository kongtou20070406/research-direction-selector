"""Locked native data rechecks, not a full bus or scientific acceptance test."""
from dataclasses import FrozenInstanceError, asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import rds_admission_read as admission
import rds_owned_advisor as owned
import test_rds_owned_advisor as fixtures
from rds_project import ProjectStore


class FenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-admission-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.rds').mkdir()
        self.path = self.root / '.rds/project.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE budget(resource TEXT PRIMARY KEY,spent REAL)')
        self.db.execute("INSERT INTO budget VALUES ('wall_seconds',7)")
        self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        self.spec = {'id': 'fixture', 'schema': 1}

    def packet(self, files=()):
        return admission.prepare(self.db, self.path, self.spec, files, None)

    def check(self, packet):
        return admission.check_locked(self.db, self.path, self.root, self.spec, packet)

    def test_frozen_native_value_and_user_json_is_not_admission(self):
        packet = self.packet()
        with self.assertRaises(FrozenInstanceError):
            packet.schema_version = 2
        with self.assertRaisesRegex(ValueError, 'user JSON is not admission'):
            self.check(asdict(packet))
        before = self.db.total_changes
        self.assertTrue(self.check(packet))
        self.assertEqual(self.db.total_changes, before)
        self.assertTrue(self.db.in_transaction)

    def test_changed_budget_is_stale_and_never_reset(self):
        packet = self.packet()
        self.db.execute("UPDATE budget SET spent=spent+1")
        with self.assertRaises(admission.StaleAdmission) as failure:
            self.check(packet)
        self.assertEqual(failure.exception.status, 'STALE')
        self.assertEqual(self.db.execute('SELECT spent FROM budget').fetchone()[0], 8)

    def test_foreign_ledger_and_changed_manifest_reject(self):
        packet = self.packet()
        with self.assertRaisesRegex(ValueError, 'another ledger'):
            admission.check_locked(self.db, self.root / 'foreign', self.root, self.spec, packet)
        with self.assertRaisesRegex(ValueError, 'manifest binding'):
            admission.check_locked(self.db, self.path, self.root, {'id': 'other'}, packet)

    def test_sql_value_types_do_not_alias(self):
        self.db.execute('CREATE TABLE value(body)')
        self.db.execute("INSERT INTO value VALUES ('6262')")
        packet = self.packet()
        self.db.execute('UPDATE value SET body=?', (b'bb',))
        with self.assertRaises(admission.StaleAdmission):
            self.check(packet)

    def test_original_bytes_changed_at_same_size_reject(self):
        path = self.root / 'original.bin'
        path.write_bytes(b'ab')
        packet = self.packet([{'path': 'original.bin', 'sha256': hashlib.sha256(b'ab').hexdigest(), 'size': 2}])
        path.write_bytes(b'cd')
        with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
            self.check(packet)

    def test_original_paths_cannot_escape(self):
        packet = self.packet([{'path': '../escape', 'sha256': 'a' * 64, 'size': 0}])
        with self.assertRaisesRegex(ValueError, 'escapes project root'):
            self.check(packet)

    def test_cas_address_must_match_hash(self):
        with self.assertRaisesRegex(ValueError, 'outside its CAS'):
            admission.prepare(self.db, self.path, self.spec, [],
                              {'map': {'path': 'data.json', 'sha256': 'a' * 64, 'bytes': 1}})

    def test_fence_bounds_fail_closed(self):
        with patch.object(admission, 'MAX_ROWS', 0):
            with self.assertRaisesRegex(ValueError, 'coverage exceeds'):
                self.packet()

    def test_byte_preflight_rejects_before_materializing_oversized_row(self):
        queries = []
        self.db.set_trace_callback(queries.append)
        with patch.object(admission, 'MAX_ROW_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'coverage exceeds'):
                self.packet()
        self.assertFalse(any('SELECT * FROM' in sql for sql in queries))

    def test_audit_schema_does_not_supply_a_json_loader_or_authority(self):
        schema = json.loads((ROOT / 'schemas/rds-admission-read.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(set(schema['required']), set(asdict(self.packet())))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['schema_version'], {'const': 1})
        self.assertIn('No JSON loader or permission grant', schema['$comment'])

    def test_missing_transaction_rejects(self):
        self.db.rollback()
        with self.assertRaisesRegex(ValueError, 'active transaction'):
            self.packet()


class ProductionCheckTests(unittest.TestCase):
    def test_real_store_policy_check_is_readonly_and_locked_recheck_uses_data(self):
        harness = fixtures.OwnedAdvisorCLITests('test_record_bindings_reach_real_cli_without_promoting_scientific_support')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.initialize()
        store = ProjectStore(harness.root)
        original = owned.check_admission
        connections = []
        def external_check(owner, db, spec, token):
            connections.append(db.execute('PRAGMA query_only').fetchone()[0])
            self.assertEqual(connections[-1], 1)
            return original(owner, db, spec, token)
        with patch.object(owned, 'check_admission', side_effect=external_check):
            store.register(harness.manifests['baseline'])
        self.assertEqual(connections, [1])
        token = store._advisor_prepare_run('baseline')
        with patch.object(owned, 'check_admission', side_effect=RuntimeError('upper callback under lock')):
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                store._advisor_check(db, harness.manifests['baseline'], token)
        result = harness.execute()
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(harness.starts(), ['baseline'])


if __name__ == '__main__':
    unittest.main()
