"""Public continuation trajectories; original launches, receipts and costs matter.

Local deterministic fixtures exercise software behavior, not research efficacy.
"""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_rds_owned_advisor as owned_fixture
import test_rds_autonomy as model_fixture
import test_rds_project_assembly as assembly_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, digest
import rds_autonomy as autonomy
import rds_continuation as continuation
import rds_owned_advisor as owned_advisor


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.f = owned_fixture.OwnedAdvisorCLITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def drive(self, *args, status_codes=(0,)):
        return self.f.output('project', 'drive', '--until-judgment', '--controller-wall-seconds', '8', *args,
                             status_codes=status_codes)

    def test_real_cli_continues_ordinary_routes_and_retains_false_goal(self):
        self.f.initialize()
        out = self.drive()
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertEqual(out['status'], 'JUDGMENT_REQUIRED')
        handoff = out['handoff']
        self.assertEqual(handoff['goal_status'], 'FALSE')
        self.assertEqual(handoff['scientific_support'], 'UNKNOWN')
        self.assertEqual(handoff['response_class'], 'RESEARCH_JUDGMENT')
        self.assertEqual(handoff['evidence_status'], 'VERIFIED_STOP_SNAPSHOT')
        state = self.f.snapshot()
        self.assertEqual(len(state['receipts']), 2)
        self.assertEqual(handoff['resources'], state['budget'])
        self.assertAlmostEqual(handoff['resources']['wall_seconds']['reserved'], 0)
        self.assertGreater(out['controller_wall_seconds'], 0)
        receipts = [r['sha256'] for r in state['receipts']]
        again = self.drive()
        self.assertEqual(again['status'], 'JUDGMENT_REQUIRED')
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertEqual([r['sha256'] for r in self.f.snapshot()['receipts']], receipts)

    def test_explicit_allowance_validation_and_default_drive_compatibility(self):
        self.f.initialize()
        before = self.f.snapshot()
        for args in [(), ('--until-judgment',), ('--until-judgment', '--controller-wall-seconds', 'nan'),
                     ('--until-judgment', '--controller-wall-seconds', '0'),
                     ('--until-judgment', '--controller-wall-seconds', '121')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.f.call('project', 'drive', *args, ok=False).returncode, 0)
                self.f.assert_uncharged(before)

    def test_pass_cap_and_existing_reservation_do_not_duplicate_attempts(self):
        self.f.initialize()
        self.f.create()
        self.assertEqual(self.f.snapshot()['runs'][0]['status'], 'RESERVED')
        first = self.drive('--max-steps', '1')
        self.assertEqual(first['status'], 'STEP_LIMIT')
        self.assertEqual(first['handoff']['response_class'], 'BOUNDED_CONTINUATION')
        self.assertEqual(self.f.starts(), ['baseline'])
        original = self.f.snapshot()['receipts'][0]
        self.drive()
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertIn(original, self.f.snapshot()['receipts'])

    def test_missing_or_conflicting_original_repaired_without_repeating_paid_work(self):
        self.f.initialize()
        self.f.output('project', 'advance', '--brief')
        receipt = self.f.snapshot()['receipts'][0]
        original = self.f.root / 'outputs/baseline.json'
        data = original.read_bytes()
        for mutation in ('missing', 'conflicting'):
            with self.subTest(mutation=mutation):
                if mutation == 'missing':
                    original.unlink()
                else:
                    original.write_text('{"score":1}', encoding='utf-8')
                blocked = self.drive(status_codes=(2,))
                self.assertEqual(blocked['status'], 'EVIDENCE_REPAIR_REQUIRED')
                self.assertEqual(blocked['handoff']['goal_status'], 'UNKNOWN')
                self.assertEqual(self.f.starts(), ['baseline'])
                self.assertIn(receipt, self.f.snapshot()['receipts'])
        original.write_bytes(data)
        self.drive()
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertIn(receipt, self.f.snapshot()['receipts'])

    def test_process_failure_does_not_become_refutation_or_success(self):
        self.f.initialize('nonzero')
        out = self.drive()
        self.assertEqual(out['handoff']['goal_status'], 'UNKNOWN')
        self.assertEqual(self.f.starts(), ['baseline'])
        self.assertEqual(self.f.snapshot()['receipts'][0]['exit_code'], 7)
        self.assertEqual(out['handoff']['scientific_support'], 'UNKNOWN')

    def test_true_predicate_still_needs_independent_confirmation(self):
        self.f.initialize('positive')
        out = self.drive('--max-steps', '1')
        self.assertEqual(self.f.starts(), ['baseline'])
        self.assertEqual(out['status'], 'GOAL_PREDICATES_MET_CONFIRMATION_UNDECLARED')
        self.assertEqual(out['handoff']['response_class'], 'INDEPENDENT_CONFIRMATION')
        self.assertEqual(out['handoff']['goal_status'], 'TRUE')

    def test_large_goal_is_exact_in_original_cas_not_truncated(self):
        value = 'bounded-original-goal-' * 400
        self.f.initialize(mutate_policy=lambda p: p['context']['decision']['goal_conditions'][0].update(op='eq', value=value))
        store = ProjectStore(self.f.root)
        before = self.f.snapshot()
        report = owned_advisor.review(store)
        self.assertEqual(report['status'], 'REVIEWED', report)
        # Test the real projection independently of controller throughput. This
        # supplied stop status is an observer input, not an executed drive result.
        out = {'status': 'STEP_LIMIT', 'advisor': report}
        continuation.attach(store, before['contract'], out, project=True)
        self.assertEqual(out['handoff']['evidence_status'], 'VERIFIED_STOP_SNAPSHOT', out['handoff'])
        goal = out['handoff']['evidence']['goals']['items'][0]
        self.assertTrue(goal['details_omitted'])
        exact = json.loads((self.f.root / goal['original']['path']).read_text(encoding='utf-8'))
        self.assertEqual(digest(exact), goal['original']['sha256'])
        self.assertEqual(exact['condition']['value'], value)
        self.f.assert_uncharged(before)
        self.assertEqual(self.f.snapshot()['receipts'], [])

    def test_missing_report_projection_retains_unknown(self):
        self.f.initialize()
        before = self.f.snapshot()
        out = {'status': 'HANDOFF_REQUIRED'}
        with patch('rds_advisor_workset.build') as build:
            continuation.attach(ProjectStore(self.f.root), before['contract'], out, project=True)
        build.assert_not_called()
        self.assertEqual(out['status'], 'HANDOFF_REQUIRED')
        self.assertEqual(out['handoff']['evidence_status'], 'UNAVAILABLE')
        self.assertEqual(out['handoff']['goal_status'], 'UNKNOWN')
        self.assertEqual(out['handoff']['scientific_support'], 'UNKNOWN')
        self.assertNotIn('evidence', out['handoff'])
        self.assertFalse(out['handoff']['admission_token'])
        self.f.assert_uncharged(before)
        self.assertEqual(self.f.snapshot()['receipts'], [])

    def test_expiry_after_review_skips_projection_and_settles_original_allowance(self):
        self.f.initialize()
        before = self.f.snapshot()
        ticks = [0.]
        # Only the controller sees this clock; real review/SQLite and worker
        # clocks remain untouched. Advance at a phase boundary, not a call count.
        clock = SimpleNamespace(monotonic=lambda: ticks[0], time=autonomy.time.time)
        real_review = owned_advisor.review

        def expire_after_review(store):
            report = real_review(store)
            self.assertEqual(report['status'], 'REVIEWED', report)
            self.assertEqual(report['selected_manifest']['id'], 'baseline')
            ticks[0] = 9.
            return report

        with patch.object(autonomy, 'time', clock), \
                patch.object(owned_advisor, 'review', side_effect=expire_after_review) as review, \
                patch('rds_advisor_workset.build') as build:
            out = autonomy.drive(ProjectStore(self.f.root), until_judgment=True, controller_wall_seconds=8)
        review.assert_called_once()
        build.assert_not_called()
        self.assertEqual(out['status'], 'HANDOFF_REQUIRED', out)
        self.assertEqual(out['reason'], 'CONTROLLER_WALL_ALLOWANCE_EXHAUSTED')
        self.assertEqual(out['executed'], [])
        self.assertEqual(out['handoff']['evidence_status'], 'UNAVAILABLE')
        self.assertEqual(out['handoff']['goal_status'], 'UNKNOWN')
        self.assertEqual(out['handoff']['scientific_support'], 'UNKNOWN')
        self.assertNotIn('evidence', out['handoff'])
        self.assertFalse(out['handoff']['admission_token'])
        self.assertEqual(out['handoff']['resource_cut'], 'CONTROLLER_RELEASE_TRANSACTION')
        self.assertEqual(out['controller_wall_seconds'], 9.)
        after = self.f.snapshot()
        self.assertEqual(self.f.starts(), [])
        self.assertEqual(after['runs'], before['runs'])
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertEqual(out['handoff']['resources'], after['budget'])
        for resource, original in before['budget'].items():
            expected = dict(original)
            if resource == 'wall_seconds':
                expected['spent_measured'] += 8.
                expected['charged_estimate'] += 1.
                expected['remaining'] -= 9.
            self.assertEqual(after['budget'][resource], expected, resource)

    def test_post_commit_collection_failure_recovers_original_receipt(self):
        self.f.initialize()
        store = ProjectStore(self.f.root)
        store.register(self.f.manifests['baseline'])
        with patch('rds_owned_advisor.after_finish', side_effect=RuntimeError('collector unavailable after commit')):
            receipt = store.execute('baseline')
        self.assertEqual(store.last_advisor_review['status'], 'COLLECTION_FAILED')
        self.drive()
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertIn(receipt, self.f.snapshot()['receipts'])

    def test_projection_failure_never_masks_execution_or_holds_budget(self):
        self.f.initialize()
        with patch('rds_advisor_workset.build', side_effect=RuntimeError('projection observer failed')):
            out = autonomy.drive(ProjectStore(self.f.root), until_judgment=True, controller_wall_seconds=8)
        self.assertEqual(out['status'], 'JUDGMENT_REQUIRED')
        self.assertEqual(out['handoff']['evidence_status'], 'UNAVAILABLE')
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertAlmostEqual(self.f.snapshot()['budget']['wall_seconds']['reserved'], 0)

    def test_exhausted_budget_cannot_launch_or_reset_charge(self):
        self.f.initialize()
        store = ProjectStore(self.f.root)
        # A fully consumed original wall ledger, independent of process timing.
        with store._db() as db:
            db.execute("UPDATE budget SET spent=cap WHERE resource='wall_seconds'")
        before = self.f.snapshot()
        out = self.drive()
        self.assertEqual(out['status'], 'CONTROLLER_ADMISSION_BLOCKED')
        self.assertEqual(out['handoff']['resources'], before['budget'])
        self.f.assert_uncharged(before)

    def test_uncertain_original_dispatch_keeps_reservation_and_attempt(self):
        self.f.initialize()
        self.f.create()
        store = ProjectStore(self.f.root)
        with store._db() as db:
            run = store._run(db, 'baseline')
            run.update(attempt_id='uncertain-original', worker_pid=os.getpid())
            store._save(db, run)
        before = self.f.snapshot()
        out = self.drive()
        after = self.f.snapshot()
        self.assertEqual(out['status'], 'WAITING_FOR_ORIGINAL_ATTEMPT')
        self.assertEqual(after['runs'], before['runs'])
        self.assertEqual(after['receipts'], [])
        self.assertEqual(self.f.starts(), [])
        self.assertAlmostEqual(after['budget']['wall_seconds']['reserved'], before['budget']['wall_seconds']['reserved'])
        self.assertGreater(after['budget']['wall_seconds']['spent_measured'], before['budget']['wall_seconds']['spent_measured'])

    def test_pause_keeps_original_reservation_and_resume_continues(self):
        self.f.initialize()
        self.f.create()
        for kind in ('pause', 'resume'):
            snap = self.f.snapshot()
            request = {'id': kind, 'kind': kind, 'message': 'Explicit fixture user direction',
                       'contract_sha256': snap['contract_sha256'],
                       'expected_revision': snap.get('steering', {}).get('revision')}
            self.f.call('project', 'steer', '--request', self.f.write_json('steer.json', request),
                        '--user-directed', '--source', 'current-user-message:continuation-test')
            out = self.drive()
            if kind == 'pause':
                self.assertEqual(out['status'], 'HUMAN_STEERING_REQUIRED')
                self.assertEqual(self.f.starts(), [])
                self.assertEqual(self.f.snapshot()['budget'], snap['budget'])
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])

    def test_control_exhaustion_stops_before_worker_and_releases_hold(self):
        self.f.initialize()
        out = self.f.output('project', 'drive', '--until-judgment', '--controller-wall-seconds', '.000001')
        self.assertEqual(out['status'], 'HANDOFF_REQUIRED')
        self.assertIn('CONTROLLER_WALL_ALLOWANCE_EXHAUSTED', out['reason'])
        self.assertEqual(self.f.starts(), [])
        self.assertEqual(self.f.snapshot()['receipts'], [])
        budget = self.f.snapshot()['budget']['wall_seconds']
        self.assertAlmostEqual(budget['reserved'], 0)
        self.assertGreater(budget['spent_measured'] + budget['charged_estimate'], 0)

    def test_dead_ordinary_controller_charge_survives_rejected_new_claim(self):
        self.f.initialize()
        store = ProjectStore(self.f.root)
        autonomy._claim(store, 30)
        with patch.object(autonomy, '_alive', return_value=False):
            first = autonomy.drive(store, until_judgment=True, controller_wall_seconds=8)
            budget = self.f.snapshot()['budget']
            second = autonomy.drive(store, until_judgment=True, controller_wall_seconds=8)
        self.assertEqual(first['status'], 'CONTROLLER_ADMISSION_BLOCKED')
        self.assertEqual(second['status'], first['status'])
        self.assertEqual(self.f.snapshot()['budget'], budget)
        self.assertEqual(budget['wall_seconds']['reserved'], 0)
        self.assertEqual(budget['wall_seconds']['charged_estimate'], 30)
        self.assertEqual(self.f.starts(), [])


class RecipeContinuationTests(unittest.TestCase):
    def setUp(self):
        self.f = assembly_fixture.ProjectAssemblyTests()
        self.f._testMethodName = self._testMethodName
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_recipe_routes_continue_without_resetting_identity_receipts_or_budget(self):
        initialized = self.f.init()
        before = self.f.store.snapshot()
        original_contract = before['contract']
        original_goal = original_contract['advisor_policy']['context']['decision']
        rejected = self.f.call('project', 'drive', ok=False)
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        unchanged = self.f.store.snapshot()
        for key in ('contract', 'contract_sha256', 'budget', 'runs', 'receipts'):
            self.assertEqual(unchanged[key], before[key], key)
        self.assertEqual(initialized['assembly']['contract_sha256'], before['contract_sha256'])
        self.assertEqual(self.f.starts(), [])
        original_receipt = None
        controller_spent, controller_charged = 0., 0.
        for expected_starts, expected_status in [(['baseline'], 'STEP_LIMIT'),
                                                  (['baseline', 'repair'], 'JUDGMENT_REQUIRED')]:
            step_args = ('--max-steps', '1') if len(expected_starts) == 1 else ()
            out = self.f.call('project', 'drive', '--until-judgment',
                              '--controller-wall-seconds', '8', *step_args)
            diagnostic = {key: out.get(key) for key in ('status', 'reason', 'controller_wall_seconds')}
            diagnostic['handoff'] = {key: out.get('handoff', {}).get(key)
                                    for key in ('evidence_status', 'diagnostic', 'resource_cut')}
            self.assertEqual(out['status'], expected_status, diagnostic)
            self.assertEqual(self.f.starts(), expected_starts, diagnostic)
            state = self.f.store.snapshot()
            self.assertEqual(state['contract'], original_contract)
            self.assertEqual(state['contract_sha256'], before['contract_sha256'])
            self.assertEqual(state['contract']['advisor_policy']['context']['decision'], original_goal)
            self.assertEqual(out['handoff']['goal_status'], 'FALSE', diagnostic)
            self.assertEqual(out['handoff']['scientific_support'], 'UNKNOWN')
            self.assertEqual(out['handoff']['resources'], state['budget'])
            self.assertEqual(out['handoff']['resource_cut'], 'CONTROLLER_RELEASE_TRANSACTION')
            self.assertEqual(len(state['receipts']), len(expected_starts))
            baseline = next(receipt for receipt in state['receipts'] if receipt['run_id'] == 'baseline')
            if original_receipt is None:
                original_receipt = baseline
            self.assertEqual(baseline, original_receipt)
            for resource, budget in state['budget'].items():
                worker_reserved = sum(run['resource_estimates'].get(resource, 0)
                                      for run in state['runs'] if run['status'] in {'RESERVED', 'RUNNING'})
                self.assertAlmostEqual(budget['reserved'], worker_reserved, msg=resource)
            controller_spent += min(out['controller_wall_seconds'], 8)
            controller_charged += max(0., out['controller_wall_seconds'] - 8)
            worker_spent = sum(receipt['resources']['wall_seconds']['measured']
                               for receipt in state['receipts'])
            self.assertAlmostEqual(state['budget']['wall_seconds']['spent_measured'],
                                   before['budget']['wall_seconds']['spent_measured']
                                   + worker_spent + controller_spent)
            self.assertAlmostEqual(state['budget']['wall_seconds']['charged_estimate'],
                                   before['budget']['wall_seconds']['charged_estimate'] + controller_charged)
        self.assertTrue(all(receipt['run_status'] == 'SUCCEEDED' for receipt in state['receipts']))
        self.assertAlmostEqual(state['budget']['wall_seconds']['reserved'], 0)

class ModelContinuationTests(unittest.TestCase):
    def setUp(self):
        self.f = model_fixture.AutonomyTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_new_judgment_never_dispatches_or_even_requests_a_provider(self):
        self.f.build()
        out = self.f.cli('project', 'drive', '--until-judgment')
        self.assertEqual(out['status'], 'JUDGMENT_REQUIRED')
        self.assertEqual(self.f.calls(), [])
        self.assertEqual(self.f.events(autonomy.REQUESTED), [])
        self.assertEqual(self.f.store.snapshot()['receipts'], [])

    def test_existing_request_pauses_before_its_original_model_worker(self):
        self.f.build()
        request = self.f.request()
        out = self.f.cli('project', 'drive', '--until-judgment')
        self.assertEqual(out['status'], 'MODEL_REQUEST_READY')
        self.assertEqual(out['request'], request['request'])
        self.assertEqual(self.f.calls(), [])
        self.assertEqual(self.f.store.snapshot()['receipts'], [])

    def test_paid_model_success_reconciles_and_continues_without_new_call(self):
        self.f.build()
        _, receipt = self.f.run_repair()
        out = self.f.cli('project', 'drive', '--until-judgment')
        self.assertEqual(out['status'], 'GOAL_PREDICATES_MET_CONFIRMATION_UNDECLARED')
        self.assertEqual(self.f.calls(), ['repair1'])
        self.assertIn(receipt, self.f.store.snapshot()['receipts'])
        self.assertEqual(len(self.f.store.snapshot()['receipts']), 2)

    def test_uncertain_paid_delivery_never_buys_a_replacement(self):
        self.f.build(modes={'repair1': 'exit'}, slots=2)
        event = self.f.request()
        manifest = next(r['manifest'] for r in self.f.contract['advisor_policy']['routes'] if r['manifest']['id'] == event['run_id'])
        self.f.store.register(manifest)
        self.f.store.execute(event['run_id'])
        before = self.f.store.snapshot()
        out = self.f.cli('project', 'drive', '--until-judgment')
        self.assertEqual(out['status'], 'RECONCILE_MODEL_DELIVERY_REQUIRED')
        self.assertEqual(out['handoff']['response_class'], 'RECONCILE_ORIGINAL_DELIVERY')
        self.f.assert_single_model_cost(before)
        self.assertEqual(self.f.calls(), ['repair1'])
        self.assertEqual(len(self.f.events(autonomy.REQUESTED)), 1)

    def test_frozen_autonomy_allowance_is_not_overridden(self):
        self.f.build()
        before = self.f.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'cannot be overridden'):
            autonomy.drive(self.f.store, until_judgment=True, controller_wall_seconds=1)
        self.assertEqual(self.f.store.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
