"""One original campaign identity; markers carry no budget or history copies."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_campaign as campaign
from rds_checkpoints import save_checkpoint
from rds_project import ProjectStore, canonical, digest
import test_rds_project as fixture


class CampaignBindingTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(campaign.ENVIRONMENT, None)
        helper = fixture.ProjectTests('runTest')
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.helper = helper
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-campaign-')
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name).resolve()
        self.root = self.workspace / 'project'
        shutil.copytree(helper.root, self.root)
        self.store = ProjectStore(self.root)
        self.marker = self.workspace / campaign.MARKER

    def events(self):
        with campaign._database(self.root) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')=?", (campaign.KIND,))]

    def write_marker(self, value, path=None):
        (path or self.marker).write_text(canonical(value), encoding='utf-8')

    def native_update(self, statement, arguments=()):
        db = sqlite3.connect(self.store.path)
        try:
            db.execute('DROP TRIGGER IF EXISTS events_no_update')
            db.execute('DROP TRIGGER IF EXISTS events_no_delete')
            db.execute(statement, arguments)
            db.commit()
        finally:
            db.close()

    def test_unbound_does_not_create_state_or_marker(self):
        sibling = self.workspace / 'sibling'
        sibling.mkdir()
        self.assertIsNone(campaign.binding(sibling))
        self.assertIsNone(campaign.enforce(sibling, kind='reference'))
        self.assertEqual(list(sibling.iterdir()), [])
        self.assertFalse(self.marker.exists())

    def test_current_sibling_and_reference_kind_share_one_original_identity(self):
        result = campaign.bind(self.store, self.workspace)
        self.assertEqual(set(result), campaign.FIELDS | {'binding_path'})
        self.assertEqual(campaign.enforce(self.root), result)
        sibling = self.workspace / 'sibling'
        sibling.mkdir()
        self.assertEqual(campaign.binding(sibling)['binding_id'], result['binding_id'])
        for root, kind in ((sibling, 'project'), (self.root, 'reference')):
            with self.subTest(root=root, kind=kind), self.assertRaisesRegex(ValueError, 'canonical project ledger'):
                campaign.enforce(root, kind=kind)
        self.assertEqual(json.loads(self.marker.read_text()), self.events()[0])
        self.assertNotIn('budget', result)

    def test_binding_and_retry_preserve_failure_costs_checkpoint_and_genesis(self):
        self.store.register(self.helper.spec(mode='nonzero'))
        self.assertEqual(self.store.execute('r1')['run_status'], 'FAILED')
        save_checkpoint(self.root, 'original-failure', self.store.snapshot(), kind='project')
        before = self.store.snapshot()
        first = campaign.bind(self.store, self.workspace)
        self.assertEqual(campaign.bind(self.store, self.workspace), first)
        after = self.store.snapshot()
        for key in ('contract', 'contract_sha256', 'budget', 'runs', 'receipts'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(len(self.events()), 1)
        with campaign._database(self.root) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 1)

    def test_missing_original_ledger_is_failclosed(self):
        campaign.bind(self.store, self.workspace)
        self.store.path.unlink()
        with self.assertRaisesRegex(ValueError, 'ledger is missing'):
            campaign.binding(self.root)

    def test_recreated_same_contract_without_original_nonce_is_refused(self):
        campaign.bind(self.store, self.workspace)
        replacement = self.workspace / 'replacement.sqlite3'
        shutil.copyfile(self.helper.store.path, replacement)
        os.replace(replacement, self.store.path)
        with self.assertRaisesRegex(ValueError, 'nonce event is missing'):
            campaign.enforce(self.root)

    def test_missing_native_event_is_failclosed(self):
        campaign.bind(self.store, self.workspace)
        self.native_update("DELETE FROM events WHERE json_extract(body,'$.kind')=?", (campaign.KIND,))
        with self.assertRaisesRegex(ValueError, 'nonce event is missing'):
            campaign.binding(self.root)

    def test_changed_genesis_digest_is_refused_even_if_new_contract_hash_is_valid(self):
        campaign.bind(self.store, self.workspace)
        value = deepcopy(self.helper.contract)
        value['description'] = 'different genesis'
        db = sqlite3.connect(self.store.path)
        try:
            db.execute('DROP TRIGGER contract_no_update')
            db.execute('UPDATE contract SET sha256=?,body=? WHERE id=1', (digest(value), canonical(value)))
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(ValueError, 'genesis contract changed'):
            campaign.binding(self.root)

    def test_required_host_binding_covers_another_working_directory(self):
        original = campaign.bind(self.store, self.workspace)
        os.environ[campaign.ENVIRONMENT] = str(self.marker)
        outside = Path(self.helper.root).resolve()
        self.assertEqual(campaign.binding(outside), original)
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            campaign.enforce(outside)

    def test_missing_empty_and_relative_required_host_pointer_never_fall_back(self):
        for required in (str(self.workspace / 'missing.json'), '', 'relative.json'):
            with self.subTest(required=required):
                os.environ[campaign.ENVIRONMENT] = required
                with self.assertRaises(ValueError):
                    campaign.binding(self.root)

    def test_same_exact_marker_at_multiple_ancestors_is_consistent(self):
        value = campaign.bind(self.store, self.workspace)
        shutil.copyfile(self.marker, self.root / campaign.MARKER)
        self.assertEqual(campaign.binding(self.root)['binding_id'], value['binding_id'])

    def test_conflicting_required_and_ancestor_bindings_are_refused(self):
        campaign.bind(self.store, self.workspace)
        other = campaign.bind(self.helper.store, self.helper.root)
        os.environ[campaign.ENVIRONMENT] = other['binding_path']
        with self.assertRaisesRegex(ValueError, 'Conflicting campaign bindings'):
            campaign.binding(self.root)

    def test_every_ancestor_is_checked_instead_of_nearest_marker_winning(self):
        campaign.bind(self.store, self.workspace)
        other = campaign.bind(self.helper.store, self.helper.root)
        shutil.copyfile(other['binding_path'], self.root / campaign.MARKER)
        with self.assertRaisesRegex(ValueError, 'Conflicting campaign bindings'):
            campaign.binding(self.root)

    def test_malformed_marker_and_finite_json_boundaries_are_refused(self):
        value = campaign.bind(self.store, self.workspace)
        samples = ['{"schema":1,"schema":1}', '{"schema":NaN}', '[' * 34 + '0' + ']' * 34,
                   'x' * (campaign.MAX_BYTES + 1), canonical({**value, 'budget': 100})]
        for raw in samples:
            with self.subTest(raw=raw[:35]):
                self.marker.write_text(raw, encoding='utf-8')
                with self.assertRaises(ValueError):
                    campaign.binding(self.root)

    def test_event_first_interrupted_publication_recovers_same_identity(self):
        with patch.object(campaign, '_publish', side_effect=OSError('simulated interrupted publication')):
            with self.assertRaisesRegex(OSError, 'interrupted publication'):
                campaign.bind(self.store, self.workspace)
        original = self.events()[0]
        self.assertFalse(self.marker.exists())
        recovered = campaign.bind(self.store, self.workspace)
        self.assertEqual(recovered['binding_id'], original['binding_id'])
        self.assertEqual(len(self.events()), 1)

    def test_required_missing_marker_refuses_even_a_committed_recovery(self):
        os.environ[campaign.ENVIRONMENT] = str(self.marker)
        with self.assertRaisesRegex(ValueError, 'missing or unreadable'):
            campaign.bind(self.store, self.workspace)
        self.assertEqual(self.events(), [])
        os.environ.pop(campaign.ENVIRONMENT)
        with patch.object(campaign, '_publish', side_effect=OSError('publication stopped')):
            with self.assertRaises(OSError):
                campaign.bind(self.store, self.workspace)
        os.environ[campaign.ENVIRONMENT] = str(self.marker)
        with self.assertRaises(ValueError):
            campaign.enforce(self.root)
        with self.assertRaisesRegex(ValueError, 'missing or unreadable'):
            campaign.bind(self.store, self.workspace)
        os.environ.pop(campaign.ENVIRONMENT)
        self.assertEqual(campaign.bind(self.store, self.workspace)['binding_id'], self.events()[0]['binding_id'])

    def test_concurrent_same_identity_commits_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: campaign.bind(self.store, self.workspace), range(4)))
        self.assertEqual(len({row['binding_id'] for row in results}), 1)
        self.assertEqual(len(self.events()), 1)

    def test_concurrent_different_projects_cannot_overwrite_workspace_owner(self):
        other_root = self.workspace / 'other'
        shutil.copytree(self.helper.root, other_root)
        other = ProjectStore(other_root)
        synchronize, publish = Barrier(2), campaign._publish
        def simultaneous(path, value):
            synchronize.wait(timeout=10)
            publish(path, value)
        def attempt(store):
            try:
                return campaign.bind(store, self.workspace)
            except ValueError as exc:
                return exc
        with patch.object(campaign, '_publish', side_effect=simultaneous), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (self.store, other)))
        successes = [row for row in results if isinstance(row, dict)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(sum(isinstance(row, ValueError) for row in results), 1)
        self.assertEqual(json.loads(self.marker.read_text())['binding_id'], successes[0]['binding_id'])

    def test_conflicting_publication_never_overwrites_marker(self):
        with patch.object(campaign, '_publish', side_effect=OSError('publication stopped')):
            with self.assertRaises(OSError):
                campaign.bind(self.store, self.workspace)
        self.marker.write_bytes(b'preserved external marker')
        before = self.marker.read_bytes()
        with self.assertRaises(ValueError):
            campaign.bind(self.store, self.workspace)
        self.assertEqual(self.marker.read_bytes(), before)
        self.assertEqual(len(self.events()), 1)

    def test_workspace_cannot_change_after_event_first_commit(self):
        with patch.object(campaign, '_publish', side_effect=OSError('publication stopped')):
            with self.assertRaises(OSError):
                campaign.bind(self.store, self.workspace)
        with self.assertRaisesRegex(ValueError, 'another identity'):
            campaign.bind(self.store, self.root)
        self.assertFalse((self.root / campaign.MARKER).exists())

    def test_workspace_must_be_existing_project_ancestor(self):
        sibling = self.workspace / 'sibling'
        sibling.mkdir()
        for workspace in (sibling, self.workspace / 'missing'):
            with self.subTest(workspace=workspace), self.assertRaisesRegex(ValueError, 'existing ancestor'):
                campaign.bind(self.store, workspace)
        self.assertEqual(self.events(), [])

    def test_active_canonical_reservation_blocks_binding_without_changes(self):
        self.store.register(self.helper.spec())
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'active runs or reserved'):
            campaign.bind(self.store, self.workspace)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events(), [])
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])

    def test_publication_recovery_does_not_strand_new_pending_work(self):
        with patch.object(campaign, '_publish', side_effect=OSError('publication stopped')):
            with self.assertRaises(OSError):
                campaign.bind(self.store, self.workspace)
        original = self.events()[0]
        self.store.register(self.helper.spec())
        with self.assertRaisesRegex(ValueError, 'active runs or reserved'):
            campaign.bind(self.store, self.workspace)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events(), [original])

    def test_missing_retained_external_job_blocks_binding(self):
        self.native_update('INSERT INTO events(body) VALUES (?)',
                           (canonical({'kind': 'EXTERNAL_RUN_ALLOWANCE', 'job_root': str(self.workspace / 'missing')}),))
        with self.assertRaisesRegex(ValueError, 'ledger is missing'):
            campaign.bind(self.store, self.workspace)
        self.assertEqual(self.events(), [])

    def test_active_retained_external_job_blocks_and_terminal_receipt_allows_binding(self):
        child_root = self.workspace / 'retained-job'
        shutil.copytree(self.helper.root, child_root)
        child = ProjectStore(child_root)
        child.register(self.helper.spec())
        self.native_update('INSERT INTO events(body) VALUES (?)',
                           (canonical({'kind': 'EXTERNAL_RUN_ALLOWANCE', 'job_root': str(child_root)}),))
        with self.assertRaisesRegex(ValueError, 'settled retained child'):
            campaign.bind(self.store, self.workspace)
        self.assertEqual(self.events(), [])
        receipt = child.execute('r1')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        before = child.snapshot()
        campaign.bind(self.store, self.workspace)
        after = child.snapshot()
        for key in ('budget', 'runs', 'receipts'):
            self.assertEqual(after[key], before[key])

    def quick_child(self):
        child_root = self.root / '.rds' / 'exec' / 'plain-job'
        shutil.copytree(self.helper.root, child_root)
        child = ProjectStore(child_root)
        child.register(self.helper.spec())
        return child

    def test_legacy_plain_quick_reservation_without_pointer_blocks_binding(self):
        child = self.quick_child()
        before = child.snapshot()
        with self.assertRaisesRegex(ValueError, 'settled retained child'):
            campaign.bind(self.store, self.workspace)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events(), [])
        self.assertEqual(child.snapshot()['budget'], before['budget'])
        self.assertEqual(child.execute('r1')['run_status'], 'SUCCEEDED')
        settled = child.snapshot()
        campaign.bind(self.store, self.workspace)
        for key in ('budget', 'runs', 'receipts'):
            self.assertEqual(child.snapshot()[key], settled[key])

    def test_committed_intent_blocks_detached_admission_before_marker_publication(self):
        publish = campaign._publish
        def inspect_boundary(path, value):
            self.assertFalse(path.exists())
            self.assertIsNone(campaign.binding(self.root))
            self.assertEqual(self.events(), [value])
            with campaign._database(self.root, write=True) as db:
                with self.assertRaisesRegex(ValueError, 'binding intent'):
                    campaign.detached_admission(self.root, db)
            publish(path, value)
        with patch.object(campaign, '_publish', side_effect=inspect_boundary):
            result = campaign.bind(self.store, self.workspace)
        self.assertEqual(campaign.binding(self.root), result)

    def test_interrupted_intent_blocks_admission_until_same_identity_recovery(self):
        with patch.object(campaign, '_publish', side_effect=OSError('publication interrupted')):
            with self.assertRaises(OSError):
                campaign.bind(self.store, self.workspace)
        original = self.events()[0]
        with campaign._database(self.root, write=True) as db:
            with self.assertRaisesRegex(ValueError, 'binding intent'):
                campaign.detached_admission(self.root, db)
        self.assertEqual(campaign.bind(self.store, self.workspace)['binding_id'], original['binding_id'])

    def test_real_quick_refused_during_publication_keeps_recovery_and_zero_reservation(self):
        from types import SimpleNamespace
        import rds_quick
        script = self.root / 'publication-probe.py'
        script.write_text("print('must never launch')", encoding='utf-8')
        args = SimpleNamespace(root=str(self.root), name='publication-probe', timeout=5,
                               argv=[sys.executable, '-B', script.name], bind=[], output=[],
                               background=False, ledger=None, choose=None)
        def interrupted(path, value):
            with self.assertRaisesRegex(ValueError, 'binding intent'):
                rds_quick.execute(args)
            raise OSError('publication interrupted')
        with patch.object(campaign, '_publish', side_effect=interrupted), \
                patch('rds_project.subprocess.Popen') as launch:
            with self.assertRaisesRegex(OSError, 'publication interrupted'):
                campaign.bind(self.store, self.workspace)
            launch.assert_not_called()
        original = self.events()[0]
        child = ProjectStore(self.root / '.rds/exec/publication-probe').snapshot()
        self.assertEqual(child['runs'], [])
        self.assertEqual(child['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(campaign.bind(self.store, self.workspace)['binding_id'], original['binding_id'])

    def test_admission_parent_mutex_makes_binding_see_committed_quick_job(self):
        child = self.quick_child()
        started = Event()
        def bind_after_start():
            started.set()
            return campaign.bind(self.store, self.workspace)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with campaign._database(self.root, write=True) as db:
                campaign.detached_admission(self.root, db)
                future = pool.submit(bind_after_start)
                self.assertTrue(started.wait(timeout=5))
                self.assertFalse(future.done())
                db.execute('INSERT INTO events(body) VALUES (?)', (canonical({
                    'kind': campaign.QUICK_JOB_KIND, 'job_root': str(child.root)}),))
            with self.assertRaisesRegex(ValueError, 'settled retained child'):
                future.result(timeout=10)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events(), [])

    def test_detached_admission_requires_parent_transaction(self):
        db = sqlite3.connect(self.store.path)
        try:
            with self.assertRaisesRegex(ValueError, 'parent transaction'):
                campaign.detached_admission(self.root, db)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
