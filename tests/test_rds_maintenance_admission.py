"""Maintenance graph work stays outside SQLite writes; original costs survive."""
from dataclasses import FrozenInstanceError, asdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import rds_advisor_search as search
import rds_maintenance_admission as admission
from rds_admission_read import StaleAdmission
from rds_project import ProjectStore
import test_rds_project as fixtures


class MaintenanceAdmissionTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.StopPolicyAndMaintenanceTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        store = case.contract_with(maintenance_allowance={'schema': 1, 'wall_seconds': 8, 'max_uses': 4})
        contract = store.snapshot()['contract']
        return case, store, contract, case.spec(timeout=0.1, maintenance=True)

    def test_guard_imports_no_decision_or_store_modules(self):
        code = ('import sys;sys.path.insert(0,sys.argv[1]);import rds_maintenance_admission;'
                'assert not {"rds_project", "rds_advisor_search", "rds_hypergraph",'
                '"rds_quick", "rds_cli"}.intersection(sys.modules)')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                                 str(ROOT / 'scripts')], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_registration_and_launch_graphs_never_hold_writer(self):
        case, store, contract, spec = self.fixture()
        original, checks = search._dependency_review, []

        def outside_writer(*args, **kwargs):
            with closing(sqlite3.connect(store.path, timeout=0)) as probe:
                probe.execute('BEGIN IMMEDIATE')
                probe.rollback()
            checks.append(True)
            return original(*args, **kwargs)

        with patch.object(search, '_dependency_review', side_effect=outside_writer):
            registered = store.register(spec)
            receipt = store.execute(spec['id'])
        self.assertEqual(len(checks), 4)
        self.assertEqual(receipt['maintenance_review'], registered['maintenance_review'])
        self.assertEqual(receipt['assessment']['purpose'], 'MAINTENANCE')
        self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)
        before = store.snapshot()
        self.assertEqual(store.recover(spec['id']), receipt)
        self.assertEqual(store.snapshot(), before)

    def test_locked_recheck_does_not_recompute_review(self):
        case, store, contract, spec = self.fixture()
        packet = store._maintenance_prepare(spec, contract)
        before = store.snapshot()
        with patch.object(store, '_maintenance_review', side_effect=AssertionError('policy under writer')):
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                checked = admission.check_review(store, db, contract, spec, packet)
        self.assertEqual(checked, json.loads(packet.review_json))
        self.assertEqual(store.snapshot(), before)

    def test_budget_change_after_preparation_is_stale_and_not_refunded(self):
        case, store, contract, spec = self.fixture()
        packet = store._maintenance_prepare(spec, contract)
        with store._db() as db:
            db.execute("UPDATE budget SET charged=charged+0.25 WHERE resource='wall_seconds'")
        before = store.snapshot()
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(StaleAdmission):
                admission.check_review(store, db, contract, spec, packet)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(before['runs'], [])

    def test_context_and_original_objective_are_rechecked(self):
        for selected in ('context', 'objective'):
            with self.subTest(selected=selected):
                case, store, contract, spec = self.fixture()
                packet = store._maintenance_prepare(spec, contract)
                ref = packet.fence.files[0 if selected == 'context' else 1]
                path = store.root / ref.relative
                raw = path.read_bytes()
                path.write_bytes(b'!' + raw[1:])
                with store._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
                        admission.check_review(store, db, contract, spec, packet)

    def test_contract_drift_is_stale_not_a_policy_failure(self):
        case, store, contract, spec = self.fixture()
        packet = store._maintenance_prepare(spec, contract)
        changed = dict(contract, note='different effective contract')
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(StaleAdmission):
                admission.check_review(store, db, changed, spec, packet)
        with self.assertRaises(StaleAdmission):
            store._maintenance_prepare(spec, changed)

    def test_frozen_audit_json_is_not_admission(self):
        case, store, contract, spec = self.fixture()
        packet = store._maintenance_prepare(spec, contract)
        with self.assertRaises(FrozenInstanceError):
            packet.rejection = None
        schema = json.loads((ROOT / 'schemas/rds-maintenance-admission.schema.json').read_text())
        self.assertEqual(set(schema['required']), set(asdict(packet)))
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'user JSON is not admission'):
                admission.check_review(store, db, contract, spec, asdict(packet))

    def test_refusal_remains_in_original_queued_settlement_boundary(self):
        case, store, contract, spec = self.fixture()
        store.register(spec)
        with store._db() as db:
            run = store._run(db, spec['id'])
            run['attempt_id'] = 'queued-maintenance'
            store._save(db, run)
        with patch.object(store, '_maintenance_review', side_effect=ValueError('current ready blocker missing')), \
                patch('rds_project.subprocess.Popen') as launch:
            receipt = store._execute_claim(spec['id'], 'queued-maintenance')
        launch.assert_not_called()
        self.assertEqual(receipt['run_status'], 'FAILED')
        self.assertFalse(receipt['process_started'])
        self.assertIn('current ready blocker missing', receipt['errors'])
        self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(store.recover(spec['id']), receipt)

    def test_unprepared_start_fails_closed(self):
        case, store, contract, spec = self.fixture()
        store.register(spec)
        before = store.snapshot()
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'prevalidation is missing'):
                store._check_prepared_start(db, store._run(db, spec['id']), None)
        self.assertEqual(store.snapshot(), before)

    def test_stale_queued_and_prelaunch_attempts_never_settle_as_failure(self):
        for changed_at in (1, 2):
            with self.subTest(changed_at=changed_at):
                case, store, contract, spec = self.fixture()
                store.register(spec)
                with store._db() as db:
                    run = store._run(db, spec['id'])
                    run['attempt_id'] = 'queued-stale'
                    store._save(db, run)
                before = store.snapshot()['budget']
                original, calls = store._maintenance_prepare_run, 0

                def change_after_prepare(*args, **kwargs):
                    nonlocal calls
                    packet = original(*args, **kwargs)
                    calls += 1
                    if calls == changed_at:
                        with store._db() as db:
                            db.execute("UPDATE budget SET charged=charged+0.25 WHERE resource='wall_seconds'")
                    return packet

                with patch.object(store, '_maintenance_prepare_run', side_effect=change_after_prepare), \
                        patch('rds_project._WindowsPopen._execute_child') as win_launch, \
                        patch('rds_project.subprocess.Popen') as launch:
                    with self.assertRaises(StaleAdmission):
                        store._execute_claim(spec['id'], 'queued-stale')
                win_launch.assert_not_called()
                launch.assert_not_called()
                after = store.snapshot()
                self.assertEqual(after['receipts'], [])
                self.assertEqual(after['budget']['wall_seconds']['reserved'], before['wall_seconds']['reserved'])
                self.assertEqual(after['budget']['wall_seconds']['charged_estimate'],
                                 before['wall_seconds']['charged_estimate'] + 0.25)
                self.assertEqual(after['runs'][0]['attempt_id'], 'queued-stale')

    def test_unrelated_request_does_not_open_storage(self):
        self.assertIsNone(ProjectStore._maintenance_prepare(object(), {}, {}))


if __name__ == '__main__':
    unittest.main()
