"""Synthetic real-worker regressions for elapsed-only Advisor interleavings."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_owned_advisor as owned
import rds_tms_store as tms
from rds_project import ProjectStore

# Reuse fixture construction without inheriting/discovering its full test suite.
_spec = importlib.util.spec_from_file_location(
    '_owned_telemetry_fixture', ROOT / 'tests/test_rds_owned_advisor.py')
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


class OwnedTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.harness = _fixture.OwnedAdvisorCLITests(methodName='runTest')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.root = self.harness.root
        # Freeze a real synthetic worker that waits while the parent reviews it.
        gated = _fixture.SCRIPT.replace('    time.sleep(1)', '''    deadline = time.monotonic() + 20
    while not pathlib.Path("release-worker").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("synthetic worker gate timeout")
        time.sleep(.01)''')
        with patch.object(_fixture, 'SCRIPT', gated):
            self.harness.initialize('slownegative', timeout=18)
        self.worker = subprocess.Popen(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
             'project', 'advance'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=self.harness.env)
        self.addCleanup(self.stop_worker)
        self.store = ProjectStore(self.root)
        self.wait_progress(0)

    def stop_worker(self):
        (self.root / 'release-worker').touch()
        if self.worker.poll() is None:
            try:
                self.worker.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.communicate(timeout=5)

    def wait_progress(self, after):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.assertIsNone(self.worker.poll(), 'worker ended before the deterministic interleaving')
            snapshot = self.store.snapshot()
            if snapshot['runs']:
                run = snapshot['runs'][0]
                if run['status'] == 'RUNNING' and run['pid'] is not None and run['observed_wall_seconds'] > after:
                    return run['observed_wall_seconds']
            time.sleep(.01)
        self.fail('real worker did not publish its next elapsed sample')

    def finish_worker(self):
        (self.root / 'release-worker').touch()
        out, err = self.worker.communicate(timeout=10)
        self.assertEqual(self.worker.returncode, 0, out + err)
        result = json.loads(out)
        self.harness.assert_owned_receipt(result['receipt'], 'baseline', 'SUCCEEDED')
        self.assertEqual(self.harness.starts(), ['baseline'])
        return result

    def test_real_elapsed_update_between_collection_publication_and_admission(self):
        original_save = tms.save
        boundaries = []

        def save_after_heartbeat(root, spec, **kwargs):
            elapsed = self.store.snapshot()['runs'][0]['observed_wall_seconds']
            boundaries.append(self.wait_progress(elapsed))
            return original_save(root, spec, **kwargs)

        before = self.store.snapshot()
        with patch.object(tms, 'save', side_effect=save_after_heartbeat):
            token = owned.prepare_admission(self.store, self.harness.manifests['baseline'])
        self.assertEqual(len(boundaries), 1)
        window = token['telemetry']['running'][0]
        self.assertGreater(window['latest_seconds'], window['lower_bound_seconds'])
        self.assertGreaterEqual(token['telemetry']['checked_at'], token['telemetry']['snapshot_at'])
        last = window['latest_seconds']
        self.wait_progress(last)
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertTrue(owned.check_admission(self.store, db, self.harness.manifests['baseline'], token))
        self.assertGreater(token['telemetry']['running'][0]['latest_seconds'], last)
        after = self.store.snapshot()
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(after['receipts'], [])
        self.assertEqual(after['runs'][0]['attempt_id'], before['runs'][0]['attempt_id'])
        self.finish_worker()
        final = self.store.snapshot()
        self.assertEqual(len(final['runs']), 1)
        self.assertEqual(len(final['receipts']), 1)
        self.assertEqual(final['budget']['cpu_seconds']['charged_estimate'], 1)
        self.assertEqual(final['budget']['wall_seconds']['reserved'], 0)

    def test_elapsed_regression_rejects_publication_and_admission(self):
        original_save = tms.save

        def save_with_regressed_sample(root, spec, **kwargs):
            real_validate = kwargs['validate_current']

            def regress_under_publication_lock(db):
                run = self.store._run(db, 'baseline')
                run['observed_wall_seconds'] = 0
                self.store._save(db, run)
                real_validate(db)

            kwargs['validate_current'] = regress_under_publication_lock
            return original_save(root, spec, **kwargs)

        with patch.object(tms, 'save', side_effect=save_with_regressed_sample):
            with self.assertRaisesRegex(ValueError, 'telemetry regressed'):
                owned.review(self.store)
        token = owned.prepare_admission(self.store, self.harness.manifests['baseline'])
        with self.assertRaisesRegex(ValueError, 'telemetry regressed'):
            with self.store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                run = self.store._run(db, 'baseline')
                run['observed_wall_seconds'] = 0
                self.store._save(db, run)
                owned.check_admission(self.store, db, self.harness.manifests['baseline'], token)
        self.assertGreater(self.store.snapshot()['runs'][0]['observed_wall_seconds'], 0)
        self.finish_worker()

    def test_real_completion_and_budget_settlement_invalidates_old_admission(self):
        token = owned.prepare_admission(self.store, self.harness.manifests['baseline'])
        before = self.store.snapshot()
        self.finish_worker()
        completed = self.store.snapshot()
        self.assertNotEqual(completed['budget'], before['budget'])
        self.assertEqual(completed['runs'][0]['status'], 'COMPLETED')
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'Owned state changed after Advisor selection'):
                owned.check_admission(self.store, db, self.harness.manifests['baseline'], token)
        self.assertEqual(self.store.snapshot(), completed)
        self.assertEqual(self.harness.starts(), ['baseline'])


if __name__ == '__main__':
    unittest.main()
