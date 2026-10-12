"""Canonical campaign accounting through native writers, not only CLI routing."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from threading import Event
import time
import unittest
from unittest.mock import patch

import test_rds_project as fixtures
import test_rds_project_lifecycle as owned_fixture
from rds_campaign import bind
from rds_checkpoints import read_checkpoint, save_checkpoint
from rds_cli import RDSState
from rds_project import ProjectStore, canonical, file_sha
from rds_quick import cas_bytes
import rds_owned_history as owned_history
import rds_project
from rds_tms_store import save as save_dependencies


class CampaignNativeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProjectTests('test_real_success_receipt_and_distinct_costs')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.store = self.fixture.root, self.fixture.store
        self.child = self.root / 'detached'
        self.child.mkdir()
        for item in self.fixture.contract['bindings']:
            shutil.copyfile(self.root / item['path'], self.child / item['path'])

    def test_failed_costs_checkpoint_and_canonical_continuation_survive_binding(self):
        failed = self.fixture.run_spec(self.fixture.spec(mode='nonzero'))
        self.assertEqual(failed['run_status'], 'FAILED')
        before = self.store.snapshot()
        checkpoint = save_checkpoint(self.root, 'failed-observation', before, kind='project',
                                     decision={'observed_failure': failed['sha256']})
        bind(self.store, self.root)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        rejected = ProjectStore(self.child)
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            rejected.initialize(self.fixture.contract)
        self.assertFalse(rejected.state_dir.exists())
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            with RDSState(self.root).transaction(create=True):
                pass
        self.assertFalse((self.store.state_dir / 'state.sqlite3').exists())
        success = self.fixture.run_spec(self.fixture.spec(rid='r2'))
        self.assertEqual(success['run_status'], 'SUCCEEDED')
        after = self.store.snapshot()
        self.assertIn(failed['sha256'], [row['sha256'] for row in after['receipts']])
        self.assertEqual(len(after['runs']), 2)
        with self.store._db(True) as db:
            self.assertEqual(read_checkpoint(db, 'failed-observation', root=self.root)['sha256'], checkpoint['sha256'])
        spent = lambda state: state['budget']['wall_seconds']['spent_measured']
        self.assertGreaterEqual(spent(after), spent(before))

    def test_direct_auxiliary_writers_reject_before_child_state_exists(self):
        snapshot = self.store.snapshot()
        bind(self.store, self.root)
        graph = {'schema': 1, 'nodes': [{'id': 'g', 'status': 'UNKNOWN', 'source': 'synthetic'}],
                 'hyperedges': [], 'goals': ['g']}
        def create_reference():
            with RDSState(self.child).transaction(create=True):
                pass
        for write in (lambda: cas_bytes(self.child, b'original input'),
                      lambda: save_dependencies(self.child, graph, expected=None),
                      lambda: save_checkpoint(self.child, 'new', snapshot, kind='project'),
                      create_reference):
            with self.subTest(writer=write), self.assertRaisesRegex(ValueError, 'canonical project ledger'):
                write()
            self.assertFalse((self.child / '.rds').exists())

    def test_retained_child_is_readable_but_cannot_reserve_or_mint_new_budget(self):
        child = ProjectStore(self.child)
        child.initialize(self.fixture.contract)
        before = child.snapshot()
        bind(self.store, self.root)
        self.assertEqual(child.snapshot(), before)
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            child.register(self.fixture.spec())
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            save_checkpoint(self.child, 'alternative-ledger', before, kind='project')
        self.assertEqual(child.snapshot(), before)

    def sibling(self, *, wait_for_release=False):
        contract = deepcopy(self.fixture.contract)
        if wait_for_release:
            prefix = ('import pathlib, time\n'
                      'pathlib.Path("process-started").write_text("started")\n'
                      'deadline = time.monotonic() + 8\n'
                      'while not pathlib.Path("release-process").exists():\n'
                      '    if time.monotonic() >= deadline: raise SystemExit(9)\n'
                      '    time.sleep(0.01)\n')
            code = self.child / 'code.py'
            code.write_text(prefix + code.read_text(encoding='utf-8'), encoding='utf-8')
            protocol = json.loads((self.child / 'protocol.json').read_text())
            protocol['code_sha256'] = file_sha(code)
            (self.child / 'protocol.json').write_text(canonical(protocol), encoding='utf-8')
            for item in contract['bindings']:
                item['sha256'] = file_sha(self.child / item['path'])
        child = ProjectStore(self.child)
        child.initialize(contract)
        return child

    def sibling_spec(self, rid='r1', *, timeout=2):
        spec = self.fixture.spec(rid=rid, timeout=timeout)
        spec['protocol']['sha256'] = file_sha(self.child / 'protocol.json')
        return spec

    def publish_legacy_binding(self):
        """Rebuild the 750-era marker, retaining all original ledger checks."""
        with self.assertRaisesRegex(ValueError, 'no active runs or reserved resources'):
            bind(self.store, self.root)
        self.assertFalse((self.root / '.rds-campaign.json').exists())
        # Only the newly introduced workspace inventory is absent in this
        # legacy fixture. Canonical and known-job reconciliation remain real.
        with patch('rds_campaign._workspace_roots', return_value=iter(())):
            return bind(self.store, self.root)

    def test_legacy_binding_running_sibling_settles_without_new_admission(self):
        child = self.sibling(wait_for_release=True)
        child.register(self.sibling_spec(timeout=10))
        parent_before = self.store.snapshot()['budget']
        release = self.child / 'release-process'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(child.execute, 'r1')
            try:
                deadline = time.monotonic() + 10
                while True:
                    with child._db(True) as db:
                        running = child._run(db, 'r1')
                    handshake = (self.child / 'process-started').exists()
                    diagnostic = (f'handshake={handshake}, status={running["status"]}, '
                                  f'pid={running["pid"]}, attempt_id={running["attempt_id"]}')
                    self.assertFalse(future.done(), f'Process exited before binding: {diagnostic}')
                    # Child startup does not synchronize the parent's PID commit.
                    if handshake and running['pid'] is not None:
                        break
                    self.assertLess(time.monotonic(), deadline,
                                    f'Process handshake/PID publication timed out: {diagnostic}')
                    time.sleep(0.01)
                self.assertFalse(future.done(), 'Process exited before the binding boundary')
                self.assertEqual(running['status'], 'RUNNING')
                self.assertIsNotNone(running['pid'])
                self.assertIsNotNone(running['attempt_id'])
                self.publish_legacy_binding()
                with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
                    child.register(self.sibling_spec('r2'))
            finally:
                release.write_text('finish original process', encoding='utf-8')
            receipt = future.result(timeout=15)
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertTrue(receipt['process_started'])
        self.assertEqual(receipt['attempt_id'], running['attempt_id'])
        after = child.snapshot()
        self.assertEqual(len(after['runs']), 1)
        self.assertEqual(len(after['receipts']), 1)
        for resource in after['budget'].values():
            self.assertEqual(resource['reserved'], 0)
        self.assertGreater(after['budget']['wall_seconds']['spent_measured'], 0)
        self.assertEqual(self.store.snapshot()['budget'], parent_before)

    def test_legacy_binding_running_sibling_waits_for_pid_publication(self):
        import rds_project
        parent_paused = Event()
        publication_observed = Event()
        job_factory = rds_project._Job
        snapshot = ProjectStore.snapshot
        make_sibling = self.sibling
        observed = []

        def delay_pid_publication(process):
            job = job_factory(process)
            # Hold the real parent before PID persistence, without locking readers.
            parent_paused.set()
            if not publication_observed.wait(timeout=5):
                job.stop()
                job.close()
                raise AssertionError('Pre-PID RUNNING state was not observed')
            return job

        def monitored_sibling(*args, **kwargs):
            child = make_sibling(*args, **kwargs)
            read_run = child._run

            def observe_run(db, run_id):
                run = read_run(db, run_id)
                if (parent_paused.is_set() and not publication_observed.is_set()
                        and run['status'] == 'RUNNING' and run['pid'] is None):
                    observed.append(run)
                    publication_observed.set()
                return run

            reader = patch.object(child, '_run', side_effect=observe_run)
            reader.start()
            self.addCleanup(reader.stop)
            return child

        with patch('rds_project._Job', side_effect=delay_pid_publication), \
                patch.object(self, 'sibling', side_effect=monitored_sibling):
            try:
                self.test_legacy_binding_running_sibling_settles_without_new_admission()
            finally:
                publication_observed.set()
        self.assertEqual(len(observed), 1)
        self.assertIsNotNone(observed[0]['attempt_id'])
        after = snapshot(ProjectStore(self.child))
        self.assertEqual(after['runs'][0]['attempt_id'], observed[0]['attempt_id'])
        self.assertEqual(after['receipts'][0]['attempt_id'], observed[0]['attempt_id'])
        self.assertEqual(after['receipts'][0]['pid'], after['runs'][0]['pid'])

    def test_legacy_binding_after_admission_before_claim_settles_failed_without_popen(self):
        child = self.sibling()
        child.register(self.sibling_spec())
        reserved = child.snapshot()['budget']
        claim = child._execute_claim
        def bind_before_claim(run_id, attempt_id):
            admitted = child.snapshot()['runs'][0]
            self.assertEqual(admitted['status'], 'RESERVED')
            self.assertEqual(admitted['attempt_id'], attempt_id)
            self.publish_legacy_binding()
            return claim(run_id, attempt_id)
        with patch.object(child, '_execute_claim', side_effect=bind_before_claim), \
                patch('rds_project.subprocess.Popen') as launch:
            receipt = child.execute('r1')
        launch.assert_not_called()
        self.assertEqual(receipt['run_status'], 'FAILED')
        self.assertIsNone(receipt['process_started'])
        self.assertTrue(any('Campaign changed before launch' in error for error in receipt['errors']))
        after = child.snapshot()
        self.assertEqual(after['runs'][0]['attempt_id'], receipt['attempt_id'])
        self.assertEqual(len(after['receipts']), 1)
        for name, budget in after['budget'].items():
            self.assertEqual(budget['cap'], reserved[name]['cap'])
            self.assertEqual(budget['reserved'], 0)
            self.assertEqual(budget['spent_measured'], reserved[name]['spent_measured'])
            self.assertEqual(budget['charged_estimate'], reserved[name]['charged_estimate'] + reserved[name]['reserved'])

    def test_legacy_binding_owned_running_sibling_settles_original_receipt_and_checkpoint(self):
        helper = owned_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper._testMethodName = self._testMethodName
        for name in ('code.py', 'config.json', 'data.json', 'evaluator.json'):
            shutil.copyfile(helper.root / name, self.child / name)
        helper.root = helper.fixture.root = self.child
        helper.store = helper.fixture.store = child = ProjectStore(self.child)
        prefix = ('import pathlib, time\n'
                  'pathlib.Path("owned-process-started").write_text("started")\n'
                  'deadline = time.monotonic() + 4\n'
                  'while not pathlib.Path("release-owned-process").exists():\n'
                  '    if time.monotonic() >= deadline: raise SystemExit(9)\n'
                  '    time.sleep(0.01)\n')
        helper.prepare_contract(owned=True, source=prefix + owned_fixture.fixture_module.SCRIPT)
        child.initialize(helper.contract)
        child.register(helper.manifests['baseline'])
        with child._db(True) as db:
            registered = child._run(db, 'baseline')
            before_id = owned_history.checkpoint_id('before', registered)
            before = read_checkpoint(db, before_id, root=child.root)
        self.assertTrue(registered['owned_history_recorded'])
        self.assertEqual(before['record']['decision']['owned_history']['run_id'], 'baseline')
        self.assertEqual(before['record']['decision']['owned_history']['manifest_sha256'], registered['manifest_sha256'])
        self.assertIsNone(before['record']['snapshot']['runs'][0]['attempt_id'])
        release = self.child / 'release-owned-process'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(child.execute, 'baseline')
            try:
                deadline = time.monotonic() + 10
                while True:
                    with child._db(True) as db:
                        running = child._run(db, 'baseline')
                    handshake = (self.child / 'owned-process-started').exists()
                    diagnostic = (f'handshake={handshake}, status={running["status"]}, '
                                  f'pid={running["pid"]}, attempt_id={running["attempt_id"]}')
                    self.assertFalse(future.done(), f'Owned process exited before binding: {diagnostic}')
                    # Child startup does not synchronize the parent's PID commit.
                    if handshake and running['pid'] is not None:
                        break
                    self.assertLess(time.monotonic(), deadline,
                                    f'Owned process handshake/PID publication timed out: {diagnostic}')
                    time.sleep(0.01)
                self.assertFalse(future.done(), 'Owned process exited before binding')
                self.assertEqual(running['status'], 'RUNNING')
                self.assertIsNotNone(running['pid'])
                self.publish_legacy_binding()
            finally:
                release.write_text('finish owned original attempt', encoding='utf-8')
            receipt = future.result(timeout=15)
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(receipt['attempt_id'], running['attempt_id'])
        state = child.snapshot()
        self.assertEqual(state['receipts'], [receipt])
        self.assertEqual(state['runs'][0]['attempt_id'], running['attempt_id'])
        with child._db(True) as db:
            completed = child._run(db, 'baseline')
            after_id = owned_history.checkpoint_id('after', completed)
            after = read_checkpoint(db, after_id, root=child.root)
            self.assertEqual(read_checkpoint(db, before_id, root=child.root), before)
            self.assertEqual({item['id'] for item in owned_history.history_cut(child, db)}, {before_id, after_id})
            self.assertEqual(after['record']['snapshot'], owned_history.snapshot(child, db))
        self.assertEqual(after['record']['decision']['previous_checkpoint'], before_id)
        self.assertEqual(after['record']['decision']['execution'], {
            'run_id': 'baseline', 'attempt_id': running['attempt_id'],
            'receipt_sha256': receipt['sha256'], 'run_status': 'SUCCEEDED'})
        self.assertEqual(after['record']['snapshot']['receipts'], [{'run_id': 'baseline', 'sha256': receipt['sha256']}])
        self.assertEqual(after['record']['snapshot']['runs'][0]['attempt_id'], running['attempt_id'])
        for budget in state['budget'].values():
            self.assertEqual(budget['reserved'], 0)
        self.assertEqual((self.child / 'outputs/launches.txt').read_text().splitlines(), ['baseline'])
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            save_checkpoint(child.root, 'ordinary-after-binding', state, kind='project')
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            child.register(helper.manifests['repair'])
        self.assertEqual(child.snapshot(), state)

    def test_owned_running_binding_waits_for_delayed_pid_publication(self):
        gap_seen = Event()
        real_job = rds_project._Job

        def delayed_job(process):
            job = real_job(process)
            try:
                # Read the committed row without snapshot's mutation gate: this
                # launch already owns that gate until it publishes the PID.
                child = ProjectStore(self.child)
                with child._db(True) as db:
                    running = child._run(db, 'baseline')
                self.assertEqual(running['status'], 'RUNNING')
                self.assertIsNone(running['pid'])
                self.assertIsNone(process.poll(), 'Owned child exited during PID barrier')
                if os.name == 'nt':
                    self.assertFalse((self.child / 'owned-process-started').exists())
                    self.assertEqual((self.child / '.rds/project-artifacts/baseline/stdout.bin').stat().st_size, 0)
                gap_seen.set()
                return job
            except BaseException:
                job.stop()
                job.close()
                raise

        with patch('rds_project._Job', side_effect=delayed_job):
            self.test_legacy_binding_owned_running_sibling_settles_original_receipt_and_checkpoint()
        self.assertTrue(gap_seen.is_set(), 'Did not observe the committed pre-PID state')

    def test_legacy_binding_orphan_real_cli_recovery_preserves_charge_and_refuses_writers(self):
        child = self.sibling()
        child.register(self.sibling_spec())
        with patch.object(child, '_execute_claim', return_value={'status': 'PAUSED_BEFORE_CLAIM'}):
            child.execute('r1')
        with child._db() as db:
            run = child._run(db, 'r1')
            attempt = run['attempt_id']
            self.assertIsNotNone(attempt)
            run.update(status='RUNNING', worker_pid=None, pid=None, started_at=time.time() - 2,
                       observed_wall_seconds=0.3)
            child._save(db, run)
        before = child.snapshot()
        self.publish_legacy_binding()
        env = {**os.environ, 'RDS_USAGE_LOG': '0', 'PYTHONIOENCODING': 'utf-8'}
        env.pop('RDS_CAMPAIGN_BINDING', None)
        completed = subprocess.run([sys.executable, '-B',
            str(Path(__file__).resolve().parents[1] / 'scripts' / 'rds_cli.py'),
            '--root', str(self.child), 'project', 'recover', '--id', 'r1'],
            capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        value = json.loads(completed.stdout)
        receipt = value.get('receipt', value)
        self.assertEqual(receipt['run_status'], 'INTERRUPTED')
        self.assertEqual(receipt['attempt_id'], attempt)
        wall = receipt['resources']['wall_seconds']
        self.assertIsNone(wall['measured'])
        self.assertEqual(wall['observed_lower_bound'], 0.3)
        self.assertEqual(wall['charged_estimate'], before['budget']['wall_seconds']['reserved'])
        after = child.snapshot()
        self.assertEqual(len(after['runs']), 1)
        self.assertEqual(len(after['receipts']), 1)
        self.assertEqual(after['runs'][0]['attempt_id'], attempt)
        self.assertFalse((self.child / 'outputs/r1.json').exists())
        self.assertFalse((self.child / '.rds/cas').exists(), 'Foreign recovery must not write a CAS brief')
        for name, budget in after['budget'].items():
            self.assertEqual(budget['cap'], before['budget'][name]['cap'])
            self.assertEqual(budget['reserved'], 0)
            self.assertEqual(budget['charged_estimate'], before['budget'][name]['charged_estimate'] + before['budget'][name]['reserved'])
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            child.register(self.sibling_spec('r2'))
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            cas_bytes(self.child, b'new unrelated research state')
        self.assertEqual(child.snapshot(), after)

    def test_legacy_binding_wrong_or_missing_attempt_settlement_refuses_without_database(self):
        child = self.sibling()
        child.register(self.sibling_spec())
        with patch.object(child, '_execute_claim', return_value={'status': 'PAUSED_BEFORE_CLAIM'}):
            child.execute('r1')
        self.publish_legacy_binding()
        before = child.snapshot()
        for identity in (('r1', 'wrong-attempt'), ('r1', None), ('missing', 'wrong-attempt')):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                with child._db(settlement=identity):
                    self.fail('Wrong attempt opened a settlement writer')
        self.assertEqual(child.snapshot(), before)
        missing_root = self.root / 'missing-ledger'
        missing_root.mkdir()
        missing = ProjectStore(missing_root)
        with self.assertRaises((ValueError, sqlite3.Error)):
            with missing._db(settlement=('r1', 'missing-attempt')):
                self.fail('Missing ledger opened a settlement writer')
        self.assertEqual(list(missing.root.iterdir()), [])
        self.assertFalse(missing.path.exists())


if __name__ == '__main__':
    unittest.main()
