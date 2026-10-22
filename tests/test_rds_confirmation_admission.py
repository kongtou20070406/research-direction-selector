"""Confirmation preparation does not expose a seed or grant execution rights."""
from contextlib import closing
from dataclasses import FrozenInstanceError, asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import rds_postcommit_confirmation as admission
import rds_postcommit_confirmation as legacy
from rds_admission_read import StaleAdmission
from rds_project_values import digest
import test_rds_postcommit_confirmation as fixtures


class ConfirmationAdmissionTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.PostcommitConfirmationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        _, _, claim, data, kind = case.example.postcommit_sources()
        weights = {'layers': [{'weight': [[0.] * n for _ in range(m)], 'bias': [0.] * m}
                              for n, m in zip(claim['layers'][:-1], claim['layers'][1:])]}
        source = ('import json,pathlib,sys\n'
                  'pathlib.Path(sys.argv[1]).write_text(json.dumps(' + repr(weights) + '),encoding="utf-8")\n')
        with patch.object(case.example, 'postcommit_sources',
                          return_value=(source, source, claim, data, kind)):
            contract = case.build(zero=True)
        store = case.store
        routes = {r['manifest']['id']: r['manifest'] for r in contract['advisor_policy']['routes']}
        store.register(routes['candidate'])
        self.assertEqual(store.execute('candidate')['run_status'], 'SUCCEEDED')
        spec = routes['confirmation']
        run = {'id': spec['id'], 'manifest': spec, 'manifest_sha256': digest(spec)}
        return case, store, contract, spec, run

    def test_import_does_not_load_store_policy_or_model(self):
        code = ('import sys;sys.path.insert(0,sys.argv[1]);import rds_postcommit_confirmation;'
                'assert not {"rds_project", "rds_owned_advisor", "rds_domain_confirmation",'
                '"rds_quick", "rds_cli", "torch"}.intersection(sys.modules)')
        proc = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT / 'scripts')],
                              capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_unrelated_run_does_not_access_ledger(self):
        self.assertIsNone(admission.prepare_binding(object(), {}, {}))
        admission.check_binding(object(), object(), {}, {}, None)
        admission.bind_prepared(object(), object(), {}, {}, None)

    def test_production_validates_on_read_snapshot_before_writer(self):
        case, store, contract, spec, run = self.fixture()
        original, calls = admission._candidate, []

        def read_side(*args):
            self.assertEqual(args[1].execute('PRAGMA query_only').fetchone()[0], 1)
            with closing(sqlite3.connect(store.path, timeout=0)) as probe:
                probe.execute('BEGIN IMMEDIATE')
                probe.rollback()
            calls.append(True)
            return original(*args)

        with patch.object(admission, '_candidate', side_effect=read_side), \
                patch.object(legacy, 'bind_run', side_effect=AssertionError('legacy binder used')):
            result = store.register(spec)
        self.assertEqual(len(calls), 1)
        event = case.events()[0]
        self.assertEqual(result['confirmation_challenge'], event['challenge'])
        before = store.snapshot()
        store.recover('candidate')
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(case.events(), [event])

    def test_preparation_and_locked_recheck_never_reseed_or_publish(self):
        case, store, contract, spec, run = self.fixture()
        before = store.snapshot()
        with patch.object(admission.secrets, 'token_hex', side_effect=AssertionError('premature seed')):
            packet = admission.prepare_binding(store, contract, run)
            with patch.object(admission, '_candidate', side_effect=AssertionError('read under writer')):
                with store._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    admission.check_binding(store, db, contract, run, packet)
        self.assertNotIn('challenge', json.loads(packet.event_json))
        self.assertEqual(case.events(), [])
        self.assertEqual(store.snapshot(), before)

    def test_changed_ledger_is_stale_without_refund_or_seed(self):
        case, store, contract, spec, run = self.fixture()
        packet = admission.prepare_binding(store, contract, run)
        with store._db() as db:
            db.execute("UPDATE budget SET charged=charged+0.25 WHERE resource='wall_seconds'")
        before = store.snapshot()
        with patch.object(admission, 'prepare_binding', return_value=packet), \
                patch.object(admission.secrets, 'token_hex', side_effect=AssertionError('stale seed')):
            with self.assertRaises(StaleAdmission):
                store.register(spec)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(case.events(), [])

    def test_changed_weights_after_preparation_are_rejected(self):
        case, store, contract, spec, run = self.fixture()
        packet = admission.prepare_binding(store, contract, run)
        ref = json.loads(packet.event_json)['weights']
        path = store.root / ref['path']
        raw = path.read_bytes()
        try:
            path.write_bytes(b'!' + raw[1:])
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
                    admission.check_binding(store, db, contract, run, packet)
        finally:
            path.write_bytes(raw)
        self.assertEqual(case.events(), [])

    def test_publication_rechecks_files_after_registration_writes(self):
        case, store, contract, spec, run = self.fixture()
        packet = admission.prepare_binding(store, contract, run)
        ref = json.loads(packet.event_json)['weights']
        path = store.root / ref['path']
        raw = path.read_bytes()
        try:
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                admission.check_binding(store, db, contract, run, packet)
                path.write_bytes(b'!' + raw[1:])
                with patch.object(admission.secrets, 'token_hex', side_effect=AssertionError('stale seed')):
                    with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
                        admission.bind_prepared(store, db, contract, run, packet)
        finally:
            path.write_bytes(raw)
        self.assertEqual(case.events(), [])

    def test_budget_refusal_still_precedes_seed_and_cas_publication(self):
        case, store, contract, spec, run = self.fixture()
        with store._db() as db:
            db.execute("UPDATE budget SET charged=cap WHERE resource='wall_seconds'")
        before = store.snapshot()
        with patch.object(admission.secrets, 'token_hex', side_effect=AssertionError('unadmitted seed')):
            with self.assertRaises(ValueError):
                store.register(spec)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(case.events(), [])

    def test_native_value_and_original_manifest_required(self):
        case, store, contract, spec, run = self.fixture()
        packet = admission.prepare_binding(store, contract, run)
        with self.assertRaises(FrozenInstanceError):
            packet.existing = True
        schema = json.loads((ROOT / 'schemas/rds-confirmation-admission.schema.json').read_text())
        self.assertEqual(set(schema['required']), set(asdict(packet)))
        self.assertTrue((ROOT / 'schemas' / schema['properties']['fence']['$ref']).is_file())
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'user JSON is not admission'):
                admission.check_binding(store, db, contract, run, asdict(packet))
            with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                admission.check_binding(store, db, contract, dict(run, manifest_sha256='0' * 64), packet)
            with self.assertRaises(StaleAdmission):
                admission.check_binding(store, db, dict(contract, note='changed'), run, packet)

    def test_cas_refusal_rolls_back_claims_and_preserves_original_costs(self):
        case, store, contract, spec, run = self.fixture()
        before = store.snapshot()
        with patch('rds_cas.cas_bytes', side_effect=ValueError('CAS admission refused')):
            with self.assertRaisesRegex(ValueError, 'CAS admission refused'):
                store.register(spec)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(case.events(), [])
        with store._db(True) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM output_claims WHERE run_id=?', (spec['id'],)).fetchone())
        store.register(spec)
        original = case.events()
        with patch.object(admission.secrets, 'token_hex', side_effect=AssertionError('duplicate seed')):
            with self.assertRaises(ValueError):
                store.register(spec)
        self.assertEqual(case.events(), original)


if __name__ == '__main__':
    unittest.main()
