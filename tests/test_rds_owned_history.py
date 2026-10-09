"""Owned history writer boundaries with actual lightweight worker receipts."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, canonical, digest
from rds_checkpoints import MAX_BYTES, append_checkpoint, read_checkpoint, restore_checkpoint
from rds_advisor import _loop_route
from rds_owned_advisor import review, prepare_admission
import rds_owned_history as history

spec = importlib.util.spec_from_file_location('owned_history_fixture', ROOT / 'tests/test_rds_owned_advisor.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class OwnedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.OwnedAdvisorCLITests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initialize('positive' if self._testMethodName in {
            'test_large_retained_metadata_settles_and_recovers_original_worker',
            'test_checkpoint_slots_are_reserved_before_registration_and_completion'} else 'nonzero')
        self.root = self.fixture.root.resolve()
        self.store = ProjectStore(self.root)
        self.contract = self.store.snapshot()['contract']

    def decision(self):
        report = review(self.store)
        # Owned review already binds history and calculates the complete graph.
        # Consume that native advice instead of resubmitting a transformed graph
        # through the public API, where caller graph overrides are forbidden.
        return history.prepare_decision(self.store, report, self.contract)

    def reserve(self):
        decision = self.decision()
        run = self.store.register(self.contract['advisor_policy']['routes'][0]['manifest'])
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = read_checkpoint(db, history.checkpoint_id('before', run), root=self.root)
            if existing:
                decision = existing['record']['decision']
            saved = history.capture_choice(self.store, db, run, decision)
        return run, decision, saved

    def test_transaction_rollback_preserves_caller_mutations_and_checkpoint_atomicity(self):
        decision = self.decision()
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE budget SET reserved=1 WHERE resource="wall_seconds"')
            snap = history.snapshot(self.store, db)
            append_checkpoint(db, self.root, 'rollback', snap, kind='project')
            self.assertTrue(db.in_transaction)
            db.rollback()
        live = self.store.snapshot()
        self.assertEqual(live['budget']['wall_seconds']['reserved'], 0)
        with self.store._db(True) as db:
            self.assertIsNone(read_checkpoint(db, 'rollback', root=self.root))

    def test_choice_idempotence_and_conflicting_decision_are_checked(self):
        run, decision, saved = self.reserve()
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            duplicate = history.capture_choice(self.store, db, run, decision)
            self.assertEqual(duplicate['status'], 'ALREADY_SAVED')
            self.assertEqual(duplicate['sha256'], saved['sha256'])
            changed = deepcopy(decision)
            changed['evidence']['invented'] = {'value': 1}
            with self.assertRaisesRegex(ValueError, 'conflicting contents'):
                history.capture_choice(self.store, db, run, changed)
            self.assertEqual(len(history.history_cut(self.store, db)), 1)

    def test_actual_failure_completion_retains_decision_time_evidence_and_live_cost(self):
        run, decision, before = self.reserve()
        receipt = self.store.execute(run['id'])
        self.assertEqual(receipt['run_status'], 'FAILED')
        live = self.store.snapshot()
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.store._run(db, run['id'])
            first = history.capture_completion(self.store, db, current, receipt)
            second = history.capture_completion(self.store, db, current, receipt)
            self.assertEqual(first['sha256'], second['sha256'])
            record = read_checkpoint(db, first['id'], root=self.root)['record']
            self.assertEqual(record['decision']['outcome'], 'plan_locked')
            self.assertEqual(record['decision']['scientific_support'], 'UNKNOWN')
            self.assertEqual(record['decision']['evidence'], decision['evidence'])
            self.assertEqual(record['decision']['execution']['receipt_sha256'], receipt['sha256'])
            self.assertEqual(len(history.history_cut(self.store, db)), 2)
        restored = restore_checkpoint(self.root, before['id'], self.store.snapshot(), kind='project')
        self.assertEqual(restored['live_budget'], live['budget'])
        self.assertEqual(self.store.snapshot()['runs'], live['runs'])
        self.assertEqual((self.root / 'outputs/launches.txt').read_text().splitlines(), ['baseline'])

    def test_unretained_or_forged_completion_receipt_is_rejected(self):
        run, _, _ = self.reserve()
        receipt = self.store.execute(run['id'])
        forged = deepcopy(receipt)
        forged['run_status'] = 'SUCCEEDED'
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'not retained'):
                history.capture_completion(self.store, db, self.store._run(db, run['id']), forged)

    def test_history_cut_detects_new_records_and_corruption(self):
        with self.store._db(True) as db:
            initial = history.history_cut(self.store, db)
        _, _, _ = self.reserve()
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertNotEqual(history.history_cut(self.store, db), initial)
            db.execute('DROP TRIGGER checkpoint_no_update')
            db.execute("UPDATE checkpoints SET body='{}'")
            with self.assertRaisesRegex(ValueError, 'integrity'):
                history.history_cut(self.store, db)

    def test_foreign_hash_consistent_contract_is_not_a_verified_ancestor(self):
        self.reserve()
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,body FROM checkpoints').fetchone()
            body = json.loads(row['body'])
            body['snapshot']['contract']['budget']['wall_seconds'] = 300
            body['contract_sha256'] = digest(body['snapshot']['contract'])
            db.execute('DROP TRIGGER checkpoint_no_update')
            db.execute('UPDATE checkpoints SET body=?,sha=? WHERE id=?', (canonical(body), digest(body), row['id']))
            with self.assertRaisesRegex(ValueError, 'outside verified lineage'):
                history.history_cut(self.store, db)

    def test_method_identity_tracks_source_but_not_declared_output_name(self):
        manifest = self.contract['advisor_policy']['routes'][0]['manifest']
        original = history.method_identity(self.store, self.contract, manifest)
        renamed = deepcopy(manifest)
        renamed['id'] = 'renamed'
        renamed['argv'][-1] = 'outputs/renamed.json'
        renamed['outpaths'] = ['outputs/renamed.json']
        self.assertEqual(original, history.method_identity(self.store, self.contract, renamed))
        changed = deepcopy(self.contract)
        next(b for b in changed['bindings'] if b['role'] == 'code')['sha256'] = 'f' * 64
        self.assertNotEqual(original, history.method_identity(self.store, changed, manifest))
        with patch('rds_owned_history.file_sha', wraps=history.file_sha) as read_executor:
            graph = history.bind_graph(self.store, self.contract, self.contract['advisor_policy']['graph'])
            self.assertEqual(read_executor.call_count, 1)
            history.bind_graph(self.store, self.contract, self.contract['advisor_policy']['graph'])
            self.assertEqual(read_executor.call_count, 2)  # No cache survives this operation.
        candidate = {'action': graph['nodes'][0]['executable']['action']}
        altered = deepcopy(candidate)
        altered['action']['owned_execution_identity'] = history.method_identity(self.store, changed, manifest)
        self.assertNotEqual(_loop_route(candidate), _loop_route(altered))
        self.assertNotIn('owned_execution_identity', self.contract['advisor_policy']['graph']['nodes'][0]['executable']['action'])

    def test_writer_requires_owning_transaction_and_original_goal(self):
        decision = self.decision()
        with self.store._db() as db:
            with self.assertRaisesRegex(ValueError, 'owning transaction'):
                append_checkpoint(db, self.root, 'uncommitted', self.store.snapshot(), kind='project', decision=decision)
        run, decision, _ = self.reserve()
        decision['goal_revision'] = 'forged'
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'frozen goal'):
                history.capture_choice(self.store, db, run, decision)

    def test_changed_bound_action_and_forged_live_budget_are_rejected(self):
        run, decision, _ = self.reserve()
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            altered = deepcopy(decision)
            altered['candidate']['action']['operation'] = 'invented-operation'
            with self.assertRaisesRegex(ValueError, 'bound policy action'):
                history.capture_choice(self.store, db, run, altered)
            altered_snapshot = history.snapshot(self.store, db)
            altered_snapshot['budget']['wall_seconds']['cap'] = 300
            with self.assertRaisesRegex(ValueError, 'owning transaction'):
                history.capture_choice(self.store, db, run, decision, altered_snapshot)

    def test_automatic_cli_pair_is_consumed_without_scientific_rejection(self):
        completed = self.fixture.output('project', 'advance', status_codes=(0, 1))
        self.assertEqual(completed['receipt']['run_status'], 'FAILED')
        advice = self.fixture.output('advise')
        search = next(row['search'] for row in advice['recommendations']
                      if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertEqual(search['loop_review']['status'], 'RECORDED_HISTORY_REVIEWED')
        self.assertEqual(search['loop_review']['flags'], [])
        with self.store._db(True) as db:
            run = self.store._runs(db)[0]
            self.assertIs(run['owned_history_recorded'], True)
            self.assertEqual(len(history.history_cut(self.store, db)), 2)
            for stage in ('before', 'after'):
                decision = read_checkpoint(db, history.checkpoint_id(stage, run), root=self.root)['record']['decision']
                self.assertEqual(decision['outcome'], 'plan_locked')
                self.assertEqual(decision['scientific_support'], 'UNKNOWN')
        self.assertEqual(self.fixture.starts(), ['baseline'])

    def test_checkpoint_added_after_selection_invalidates_actual_registration(self):
        manifest = self.contract['advisor_policy']['routes'][0]['manifest']
        token = prepare_admission(self.store, manifest)
        decision = deepcopy(token['decision'])
        decision['outcome'] = 'deferred'
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'Program-owned Advisor'):
                append_checkpoint(db, self.root, 'forged-late-choice', history.snapshot(self.store, db),
                                  kind='project', decision=decision)
            append_checkpoint(db, self.root, 'late-choice', history.snapshot(self.store, db),
                              kind='project')
        before = self.store.snapshot()
        with patch('rds_owned_advisor.prepare_admission', return_value=token):
            with self.assertRaisesRegex(ValueError, 'state changed after Advisor selection'):
                self.store.register(manifest)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.fixture.starts(), [])

    def test_marked_missing_pair_blocks_but_legacy_history_is_not_invented(self):
        run, _, _ = self.reserve()
        self.store.execute(run['id'])
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DROP TRIGGER checkpoint_no_delete')
            db.execute('DELETE FROM checkpoints WHERE id=?', (history.checkpoint_id('after', run),))
            with self.assertRaisesRegex(ValueError, 'missing a committed after'):
                history.history_cut(self.store, db)
            retained = self.store._run(db, run['id'])
            retained.pop('owned_history_recorded')
            self.store._save(db, retained)
            self.assertEqual(len(history.history_cut(self.store, db)), 1)
            retained['owned_history_recorded'] = True
            self.store._save(db, retained)
            db.execute('DELETE FROM checkpoints WHERE id=?', (history.checkpoint_id('before', run),))
            with self.assertRaisesRegex(ValueError, 'missing a committed before'):
                history.history_cut(self.store, db)

    def test_method_identity_binds_resolved_executor(self):
        manifest = self.contract['advisor_policy']['routes'][0]['manifest']
        original = history.method_identity(self.store, self.contract, manifest)
        with patch('rds_owned_history.file_sha', return_value='f' * 64):
            self.assertNotEqual(history.method_identity(self.store, self.contract, manifest), original)

    def test_large_retained_metadata_settles_and_recovers_original_worker(self):
        run, _, saved = self.reserve()
        diagnostic = 'retained diagnostic ' + 'x' * (MAX_BYTES + 1024)
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            retained = self.store._run(db, run['id'])
            retained['diagnostic_metadata'] = diagnostic
            self.store._save(db, retained)
        self.assertGreater(len(canonical(self.store.snapshot()).encode('utf-8')), MAX_BYTES)
        receipt = self.store.execute(run['id'])
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        live = self.store.snapshot()
        self.assertEqual(live['runs'][0]['status'], 'COMPLETED')
        self.assertEqual(live['runs'][0]['diagnostic_metadata'], diagnostic)
        self.assertEqual(live['receipts'][0]['sha256'], receipt['sha256'])
        self.assertEqual(live['budget']['wall_seconds']['reserved'], 0)
        self.assertGreater(live['budget']['wall_seconds']['spent_measured'], 0)
        with self.store._db(True) as db:
            compact = read_checkpoint(db, history.checkpoint_id('after', live['runs'][0]), root=self.root)['record']['snapshot']
            self.assertLess(len(canonical(compact).encode('utf-8')), MAX_BYTES - history.COMPLETION_HEADROOM)
            self.assertNotIn('diagnostic_metadata', compact['runs'][0])
            self.assertEqual(compact['runs'][0]['attempt_id'], receipt['attempt_id'])
            self.assertEqual(compact['receipts'], [{'run_id': run['id'], 'sha256': receipt['sha256']}])
            self.assertEqual(compact['exposures'], live['exposures'])
            self.assertEqual(compact['exposures'][0]['data'],
                             [b for b in self.contract['bindings'] if b['role'] == 'data'])
        recovered = self.store.recover(run['id'])
        self.assertEqual(recovered['sha256'], receipt['sha256'])
        self.assertEqual(recovered['attempt_id'], receipt['attempt_id'])
        self.assertEqual(self.store.snapshot()['budget'], live['budget'])
        self.assertEqual(self.fixture.starts(), ['baseline'])
        restored = restore_checkpoint(self.root, saved['id'], self.store.snapshot(), kind='project')
        self.assertEqual(restored['status'], 'RESUMABLE_HANDOFF')
        self.assertEqual(restored['live_budget'], live['budget'])

    def test_near_capacity_choice_is_refused_before_reservation_or_launch(self):
        manifest = self.contract['advisor_policy']['routes'][0]['manifest']
        token = prepare_admission(self.store, manifest)
        token['decision']['pending_evidence'] = ['x' * (MAX_BYTES - history.COMPLETION_HEADROOM)]
        before = self.store.snapshot()
        with patch('rds_owned_advisor.prepare_admission', return_value=token):
            with self.assertRaisesRegex(ValueError, 'completion headroom'):
                self.store.register(manifest)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.store.snapshot()['exposures'], [])
        self.assertEqual(self.fixture.starts(), [])

    def test_checkpoint_slots_are_reserved_before_registration_and_completion(self):
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            snap = history.snapshot(self.store, db)
            for i in range(history.MAX_CHECKPOINTS - 1):
                append_checkpoint(db, self.root, 'manual-' + str(i), snap, kind='project')
        before = self.store.snapshot()
        manifest = self.contract['advisor_policy']['routes'][0]['manifest']
        with self.assertRaisesRegex(ValueError, 'checkpoint slots for outstanding completions'):
            self.store.register(manifest)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.fixture.starts(), [])
        # Remove one synthetic note solely to exercise the exact accepted
        # boundary: the normal writer remains append-only in production.
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DROP TRIGGER checkpoint_no_delete')
            db.execute('DELETE FROM checkpoints WHERE id=?', ('manual-0',))
        run = self.store.register(manifest)
        with self.store._db(True) as db:
            self.assertEqual(len(history.history_cut(self.store, db)), history.MAX_CHECKPOINTS - 1)
        for name, reason in (('late-manual', 'checkpoint slots for outstanding completions'),
                             (history.checkpoint_id('after', run), 'reserved for the owned lifecycle')):
            rejected = self.fixture.call('checkpoint', 'save', '--id', name, ok=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(reason, rejected.stdout + rejected.stderr)
        self.assertEqual(self.fixture.starts(), [])
        with self.store._db(True) as db:
            self.assertEqual(len(history.history_cut(self.store, db)), history.MAX_CHECKPOINTS - 1)
        receipt = self.store.execute(run['id'])
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        with self.store._db(True) as db:
            self.assertEqual(len(history.history_cut(self.store, db)), history.MAX_CHECKPOINTS)
            after = read_checkpoint(db, history.checkpoint_id('after', self.store._run(db, run['id'])), root=self.root)
            self.assertEqual(after['record']['decision']['execution']['receipt_sha256'], receipt['sha256'])
        self.assertEqual(self.store.recover(run['id'])['sha256'], receipt['sha256'])
        self.assertEqual(self.fixture.starts(), ['baseline'])


if __name__ == '__main__':
    unittest.main()
