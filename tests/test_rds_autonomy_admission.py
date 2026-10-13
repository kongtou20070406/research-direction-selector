"""Original autonomy bindings cross the writer boundary as immutable data."""
import ast
from dataclasses import FrozenInstanceError, asdict
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import rds_autonomy as controller
import rds_autonomy_admission as admission
from rds_admission_read import StaleAdmission
from rds_project_values import digest
import test_rds_autonomy as fixtures


class AutonomyAdmissionTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.AutonomyTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.build()
        event = case.request()
        spec = next(r['manifest'] for r in case.contract['advisor_policy']['routes']
                    if r['manifest']['id'] == event['run_id'])
        run = {'id': spec['id'], 'manifest': spec,
               'effective_contract_sha256': digest(case.contract)}
        return case, run

    def test_import_has_no_store_controller_or_decision_dependency(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'import rds_autonomy_admission; '
                'assert not {"rds_project", "rds_autonomy", "rds_owned_advisor", '
                '"rds_quick", "rds_cli", "rds_math"}.intersection(sys.modules)')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                                 str(ROOT / 'scripts')], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_register_and_launch_do_not_call_controller(self):
        case, run = self.fixture()
        observed = []
        original = admission.records

        def readonly_records(store, db, contract):
            observed.append(db.execute('PRAGMA query_only').fetchone()[0])
            self.assertEqual(observed[-1], 1)
            return original(store, db, contract)

        with patch.object(admission, 'records', side_effect=readonly_records), \
                patch.object(controller, 'bind_run', side_effect=AssertionError('upper bind')), \
                patch.object(controller, 'check_run', side_effect=AssertionError('upper launch')):
            registered = case.store.register(run['manifest'])
            self.assertEqual(registered['autonomy_request'], case.events(controller.REQUESTED)[0]['request'])
            result = case.store.execute(run['id'])
        self.assertEqual(observed, [1])
        self.assertEqual(result['run_status'], 'SUCCEEDED')
        self.assertEqual(case.calls(), [run['id']])
        before = case.store.snapshot()
        case.store.execute(run['id'])
        self.assertEqual(case.store.snapshot(), before)
        self.assertEqual(case.calls(), [run['id']])

    def test_frozen_packet_audit_json_cannot_be_admitted(self):
        case, run = self.fixture()
        packet = admission.prepare_binding(case.store, case.contract, run)
        with self.assertRaises(FrozenInstanceError):
            packet.run_id = 'other'
        schema = json.loads((ROOT / 'schemas/rds-autonomy-binding.schema.json').read_text())
        self.assertEqual(set(schema['required']), set(asdict(packet)))
        with case.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'user JSON is not admission'):
                admission.bind_locked(case.store, db, case.contract, run, asdict(packet))

    def test_locked_binding_does_not_recompute_controller_history(self):
        case, run = self.fixture()
        packet = admission.prepare_binding(case.store, case.contract, run)
        before = case.store.snapshot()
        with patch.object(admission, 'records', side_effect=AssertionError('history under write lock')):
            with case.store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                admission.bind_locked(case.store, db, case.contract, run, packet)
        self.assertEqual(case.store.snapshot(), before)
        self.assertEqual(run['autonomy_request'], case.events(controller.REQUESTED)[0]['request'])

    def test_budget_change_after_preparation_is_stale_without_reset(self):
        case, run = self.fixture()
        packet = admission.prepare_binding(case.store, case.contract, run)
        with case.store._db() as db:
            db.execute("UPDATE budget SET charged=charged+0.25 WHERE resource='wall_seconds'")
        before = case.store.snapshot()
        with case.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(StaleAdmission) as caught:
                admission.bind_locked(case.store, db, case.contract, run, packet)
        self.assertEqual(caught.exception.status, 'STALE')
        self.assertEqual(case.store.snapshot(), before)
        self.assertNotIn('autonomy_request', run)
        self.assertEqual(case.calls(), [])

    def test_tampered_original_request_is_rejected_after_preparation(self):
        case, run = self.fixture()
        packet = admission.prepare_binding(case.store, case.contract, run)
        ref = json.loads(packet.request_json)
        path = Path(ref['path'])
        raw = path.read_bytes()
        path.write_bytes(b'!' + raw[1:])
        with case.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
                admission.bind_locked(case.store, db, case.contract, run, packet)
        self.assertNotIn('autonomy_request', run)
        self.assertEqual(case.calls(), [])

    def test_worker_is_rechecked_after_preparation(self):
        case, run = self.fixture()
        packet = admission.prepare_binding(case.store, case.contract, run)
        case.write('worker.py', '# replaced after preparation\n')
        with case.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'Unsupported adapter'):
                admission.bind_locked(case.store, db, case.contract, run, packet)
        self.assertNotIn('autonomy_request', run)

    def test_packet_cannot_bind_another_run_or_manifest(self):
        case, run = self.fixture()
        packet = admission.prepare_binding(case.store, case.contract, run)
        for changed in ({**run, 'id': 'solve'},
                        {**run, 'manifest': {**run['manifest'], 'description': 'changed'}}):
            with self.subTest(changed=changed):
                with case.store._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                        admission.bind_locked(case.store, db, case.contract, changed, packet)

    def test_no_autonomy_is_a_noop_without_opening_storage(self):
        self.assertIsNone(admission.prepare_binding(None, {}, {}))

    def test_legacy_launch_entry_keeps_hash_compatibility_seam(self):
        case, run = self.fixture()
        with case.store._db(True) as db:
            controller.bind_run(case.store, db, case.contract, run)
        original = controller.file_sha
        with patch.object(controller, 'file_sha', wraps=original) as hashed:
            controller.check_run(case.store, None, case.contract, run)
        self.assertEqual(hashed.call_count, 3)
        self.assertEqual(hashed.call_args.args, (case.contract['advisor_policy']['autonomy']['provider']['argv'][0],))

    def test_kernel_sources_have_no_controller_import(self):
        sources = [ROOT / 'scripts/rds_project.py', ROOT / 'scripts/rds_autonomy_admission.py']
        # The extracted facade is checked when present, alongside the monolith.
        sources.extend((ROOT / 'scripts').glob('rds_project_queries.py'))
        for source in sources:
            modules = [n.module for n in ast.walk(ast.parse(source.read_bytes()))
                       if isinstance(n, ast.ImportFrom)]
            self.assertNotIn('rds_autonomy', modules)
        modules = [n.module for n in ast.walk(ast.parse((ROOT / 'scripts/rds_autonomy_read.py').read_bytes()))
                   if isinstance(n, ast.ImportFrom)]
        self.assertNotIn('rds_math', modules)


if __name__ == '__main__':
    unittest.main()
