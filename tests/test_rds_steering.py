"""Public CLI steering trajectories with real launches and retained accounting.

Fixtures are controlled software workloads. No model or scientific gain is
measured. The start-boundary test injects a real committed user instruction.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_rds_owned_advisor as fixture
import test_rds_autonomy as autonomy_fixture
import test_rds_quick as quick_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_project import ProjectStore, canonical, digest
from rds_steering import submit


class SteeringCLITests(unittest.TestCase):
    setUp = fixture.OwnedAdvisorCLITests.setUp
    call = fixture.OwnedAdvisorCLITests.call
    output = fixture.OwnedAdvisorCLITests.output
    write_json = fixture.OwnedAdvisorCLITests.write_json
    initialize = fixture.OwnedAdvisorCLITests.initialize
    create = fixture.OwnedAdvisorCLITests.create
    execute = fixture.OwnedAdvisorCLITests.execute
    starts = fixture.OwnedAdvisorCLITests.starts
    snapshot = fixture.OwnedAdvisorCLITests.snapshot

    def request(self, kind, ident='instruction', **values):
        snap = self.snapshot()
        return {'id': ident, 'kind': kind, 'message': 'Current user direction: ' + kind,
                'contract_sha256': snap['contract_sha256'],
                'expected_revision': snap.get('steering', {}).get('revision'), **values}

    def steer(self, request, *, ok=True, directed=True):
        args = ['project', 'steer', '--request', self.write_json('steering-request.json', request),
                '--source', 'current-user-message:synthetic-case']
        if directed:
            args.append('--user-directed')
        raw = self.call(*args, ok=ok)
        return json.loads(raw.stdout) if raw.returncode == 0 else raw

    @staticmethod
    def independent_routes(policy):
        policy['graph']['nodes'][1]['executable']['preconditions'] = []

    def test_new_plan_groups_missing_information_without_creating_contract(self):
        intent = {'goal': '检查固定有限输入的误差', 'fixed_task': True}
        result = self.output('project', 'plan', '--intent', self.write_json('intent.json', intent))
        self.assertEqual(result['goal'], intent['goal'])
        self.assertEqual(result['missing'], ['evaluation', 'budget'])
        self.assertFalse(result['execution_started'])
        self.assertFalse(result['execution_authorized'])
        self.assertFalse((self.root / '.rds/project.sqlite3').exists())
        self.assertNotIn('competing_explanations', result)

    def test_complete_new_plan_is_still_a_proposal_and_output_is_no_clobber(self):
        intent = {'goal': 'Check fixed input', 'scope': 'finite software case', 'evaluation': 'Exact independent oracle',
                  'budget': {'wall_seconds': 10}, 'next_action': 'Check the original input with the oracle', 'fixed_task': True}
        path = self.write_json('intent.json', intent)
        result = self.output('project', 'plan', '--intent', path, '--output', 'plan.json')
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['next_action'], intent['next_action'])
        self.assertFalse(result['execution_authorized'])
        original = (self.root / 'plan.json').read_bytes()
        self.assertNotEqual(self.call('project', 'plan', '--intent', path, '--output', 'plan.json', ok=False).returncode, 0)
        self.assertEqual((self.root / 'plan.json').read_bytes(), original)

    def test_plan_rejects_invalid_budget_and_self_signed_fields(self):
        for intent in ({'budget': {'wall_seconds': -1}}, {'budget': {'wall_seconds': True}},
                       {'scientific_support': 'PASS'}, {'goal': 'x' * 40000}):
            with self.subTest(intent=str(intent)[:80]):
                result = self.call('project', 'plan', '--intent', self.write_json('intent.json', intent), ok=False)
                self.assertNotEqual(result.returncode, 0)

    def test_existing_plan_retains_original_goal_evidence_and_checkpoint(self):
        self.initialize()
        self.create()
        self.execute()
        before = self.snapshot()
        result = self.output('project', 'plan', '--save-as', 'inspect-original')
        self.assertEqual(result['goal'], self.policy['context']['decision'])
        self.assertEqual(result['scope'], self.policy['context']['decision']['scope'])
        self.assertEqual(result['evidence'][0]['receipt_sha256'], before['receipts'][0]['sha256'])
        self.assertEqual(result['checkpoint']['status'], 'SAVED')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.starts(), ['baseline'])
        self.assertTrue(Path(result['artifact']['path']).is_file())
        changed = self.output('project', 'plan', '--intent', self.write_json('intent.json', {'goal': 'a different goal', 'scope': 'a different scope'}))
        self.assertEqual(changed['goal'], result['goal'])
        self.assertEqual(changed['scope'], result['scope'])
        self.assertEqual(changed['proposed_changes']['scope'], 'a different scope')
        self.assertEqual(changed['proposed_changes']['goal'], 'a different goal')

    def test_pause_blocks_registration_without_charging_or_launching(self):
        self.initialize()
        before = self.snapshot()
        receipt = self.steer(self.request('pause'))
        self.assertEqual(receipt['status'], 'RECEIVED')
        self.assertEqual(receipt['budget']['wall_seconds']['reserved'], 0)
        self.assertNotEqual(self.create(ok=False).returncode, 0)
        next_step = self.output('project', 'next')
        self.assertIsNone(next_step['selected_run'])
        self.assertTrue(next_step['steering_handoff'])
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.snapshot()['runs'], [])

    def test_reserved_pause_preserves_reservation_then_explicit_resume_runs_once(self):
        self.initialize()
        self.create()
        before = self.snapshot()
        accepted = self.steer(self.request('pause'))
        self.assertEqual(accepted['active_work'][0]['disposition'], 'UNSTARTED_RESERVATION_HELD')
        self.assertNotEqual(self.call('project', 'execute', '--id', 'baseline', ok=False).returncode, 0)
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertIsNone(self.snapshot()['runs'][0]['attempt_id'])
        self.steer(self.request('resume', 'resume'))
        self.execute()
        self.assertEqual(self.starts(), ['baseline'])

    def test_redirect_changes_actual_selected_and_launched_route(self):
        self.initialize(mutate_policy=self.independent_routes)
        self.assertEqual(self.output('project', 'next')['selected_run'], 'baseline')
        before = self.snapshot()['budget']
        self.steer(self.request('redirect', withdraw=['baseline'], prefer=['repair']))
        next_step = self.output('project', 'next')
        self.assertEqual(next_step['selected_run'], 'repair')
        self.assertEqual(next_step['steering_blocked_runs'], ['baseline'])
        self.assertEqual(self.snapshot()['budget'], before)
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['repair'])
        self.assertEqual(len(self.snapshot()['receipts']), 1)

    def test_preference_cannot_bypass_missing_prerequisite(self):
        self.initialize()
        self.steer(self.request('redirect', prefer=['repair']))
        self.assertEqual(self.output('project', 'next')['selected_run'], 'baseline')
        self.assertNotEqual(self.create('repair', ok=False).returncode, 0)
        self.assertEqual(self.starts(), [])

    def test_priority_supersedes_pause_and_brief_keeps_current_instruction(self):
        self.initialize(mutate_policy=self.independent_routes)
        self.steer(self.request('pause'))
        self.steer(self.request('redirect', 'priority', prefer=['repair']))
        report = self.output('project', 'next')
        self.assertEqual(report['selection_basis'], 'CURRENT_USER_PRIORITY_WITHIN_ADMITTED_FROZEN_ROUTES')
        brief = self.output('project', 'next', '--brief')
        self.assertFalse(brief['steering']['paused'])
        self.assertEqual(brief['steering']['instruction_id'], 'priority')
        self.assertEqual(brief['selected_run'], 'repair')
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['repair'])

    def test_concurrent_conflicting_instructions_only_one_commits(self):
        self.initialize()
        requests = [self.request(kind, kind) for kind in ('pause', 'hypothesis')]
        def attempt(request):
            try:
                return submit(ProjectStore(self.root), request, user_directed=True, source='current-user:concurrent')['status']
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, requests))
        self.assertEqual(results.count('RECEIVED'), 1)
        self.assertEqual(sum('Steering revision is stale' in r for r in results), 1)
        self.assertEqual(self.starts(), [])

    def test_stale_conflicting_and_imported_instructions_have_no_effect(self):
        self.initialize()
        request = self.request('pause')
        rejected = self.steer(request, ok=False, directed=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn('steering', self.snapshot())
        accepted = self.steer(request)
        again = self.steer(request)
        self.assertEqual(again['status'], 'ALREADY_RECEIVED')
        self.assertEqual(again['received_revision'], accepted['received_revision'])
        self.assertNotEqual(self.steer({**request, 'message': 'different'}, ok=False).returncode, 0)
        self.assertNotEqual(self.steer({**request, 'id': 'outdated', 'kind': 'resume'}, ok=False).returncode, 0)
        self.assertNotEqual(self.steer({**self.request('resume', 'bad-contract'), 'contract_sha256': '0' * 64}, ok=False).returncode, 0)
        self.steer(self.request('resume', 'resume'))
        replay = self.steer(request)
        self.assertTrue(replay['superseded'])
        self.assertFalse(replay['steering']['paused'])

    def test_unverified_hypothesis_changes_no_fact_or_execution_authority(self):
        self.initialize()
        initial = self.output('project', 'next')
        before = self.snapshot()
        result = self.steer(self.request('hypothesis', message='The result will be positive; this is unverified'))
        after = self.output('project', 'next')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertEqual(after['selected_run'], initial['selected_run'])
        self.assertEqual(after['context']['facts'], initial['context']['facts'])
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.starts(), [])
        self.assertNotEqual(self.steer({**self.request('hypothesis', 'forged'), 'scientific_support': 'PASS'}, ok=False).returncode, 0)

    def test_material_change_pauses_without_replacing_contract_or_budget(self):
        self.initialize()
        before = self.snapshot()
        self.steer(self.request('change_request', message='Use a different evaluator and add a larger budget'))
        after = self.snapshot()
        self.assertEqual(after['contract'], before['contract'])
        self.assertEqual(after['budget'], before['budget'])
        self.assertTrue(after['steering']['paused'])
        self.assertIsNone(self.output('project', 'advance')['selected_run'])

    def test_running_work_finishes_under_original_receipt_and_no_next_launch(self):
        self.assert_running_work_finishes_under_original_receipt()

    def test_running_work_finishes_after_delayed_pause_cli(self):
        # Cross the original one-second completion window before the real CLI.
        self.assert_running_work_finishes_under_original_receipt(steering_delay=1.25)

    def assert_running_work_finishes_under_original_receipt(self, *, steering_delay=0):
        release = self.root / 'outputs/steering-release'
        script = fixture.SCRIPT.replace(
            'if mode == "slownegative":\n    time.sleep(1)',
            'if mode == "slownegative":\n    time.sleep(1)\n'
            '    while not pathlib.Path("outputs/steering-release").exists():\n'
            '        time.sleep(.02)')
        # Only this fixture waits for pause receipt; the worker's timeout is unchanged.
        with patch.object(fixture, 'SCRIPT', script):
            self.initialize(mode='slownegative', timeout=6)
        self.create()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.call, 'project', 'execute', '--id', 'baseline')
            try:
                deadline = time.monotonic() + 10
                while not self.starts() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(self.starts(), ['baseline'])
                if steering_delay:
                    time.sleep(steering_delay)
                accepted = self.steer(self.request('pause'))
                self.assertIn(accepted['active_work'][0]['disposition'],
                              {'FINISH_OR_RECOVER_ORIGINAL_ATTEMPT', 'DISPATCH_UNCERTAIN_RECONCILE_ONLY'})
            finally:
                # Release even if a marker or pause assertion fails.
                release.parent.mkdir(parents=True, exist_ok=True)
                release.touch()
            running.result(timeout=15)
        snap = self.snapshot()
        original = snap['receipts'][0]['sha256']
        self.assertEqual(snap['receipts'][0]['run_status'], 'SUCCEEDED')
        self.assertGreater(snap['budget']['wall_seconds']['spent_measured'], 0)
        self.assertIsNone(self.output('project', 'advance')['selected_run'])
        self.call('project', 'recover', '--id', 'baseline')
        self.assertEqual(self.snapshot()['receipts'][0]['sha256'], original)
        self.assertEqual(self.starts(), ['baseline'])

    def test_uncertain_dispatch_keeps_original_reservation_and_attempt(self):
        self.initialize()
        self.create()
        store = ProjectStore(self.root)
        with store._db() as db:
            run = store._run(db, 'baseline')
            run.update(attempt_id='uncertain-original', worker_pid=os.getpid())
            store._save(db, run)
        before = self.snapshot()
        accepted = self.steer(self.request('pause'))
        self.assertEqual(accepted['active_work'][0]['disposition'], 'DISPATCH_UNCERTAIN_RECONCILE_ONLY')
        self.call('project', 'recover', '--id', 'baseline')
        after = self.snapshot()
        self.assertEqual(after['runs'][0]['attempt_id'], 'uncertain-original')
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(after['receipts'], [])
        self.assertEqual(self.starts(), [])

    def test_steering_can_be_received_with_exhausted_budget_but_cannot_replenish_it(self):
        self.initialize()
        store = ProjectStore(self.root)
        with store._db() as db:
            db.execute('UPDATE budget SET spent=cap')
        before = self.snapshot()['budget']
        self.steer(self.request('pause'))
        self.steer(self.request('resume', 'resume'))
        self.assertNotEqual(self.create(ok=False).returncode, 0)
        self.assertEqual(self.snapshot()['budget'], before)
        self.assertEqual(self.starts(), [])

    def test_unknown_or_contradictory_redirect_does_not_change_instruction(self):
        self.initialize()
        for fields in ({'prefer': ['unknown']}, {'prefer': ['baseline'], 'withdraw': ['baseline']},
                       {'prefer': ['baseline', 'baseline']}, {}):
            with self.subTest(fields=fields):
                self.assertNotEqual(self.steer(self.request('redirect', **fields), ok=False).returncode, 0)
                self.assertNotIn('steering', self.snapshot())

    def test_launch_transaction_rechecks_a_steering_commit_after_selection(self):
        self.initialize()
        self.create()
        store = ProjectStore(self.root)
        real_prepare = store._advisor_prepare_run
        calls = []
        def prepare(*args, **kwargs):
            token = real_prepare(*args, **kwargs)
            calls.append(token)
            if len(calls) == 3:  # Last token is prepared immediately before Popen's transaction.
                submit(store, self.request('pause'), user_directed=True, source='current-user-message:launch-race')
            return token
        with patch.object(store, '_advisor_prepare_run', side_effect=prepare):
            result = store.execute('baseline')
        self.assertEqual(len(calls), 3)
        self.assertFalse(result['process_started'])
        self.assertEqual(result['run_status'], 'FAILED')
        self.assertEqual(self.starts(), [])
        self.assertEqual(len(self.snapshot()['receipts']), 1)

    def test_legacy_project_pause_also_blocks_actual_kernel_entry(self):
        self.initialize(include_policy=False)
        self.create()
        before = self.snapshot()['budget']
        self.steer(self.request('pause'))
        self.assertNotEqual(self.call('project', 'execute', '--id', 'baseline', ok=False).returncode, 0)
        self.assertEqual(self.snapshot()['budget'], before)
        self.assertEqual(self.starts(), [])
        self.assertIn('paused', self.output('project', 'next')['next_move'])

    def test_legacy_pause_blocks_new_quick_and_theory_allowances(self):
        self.initialize(include_policy=False)
        self.steer(self.request('pause'))
        before = self.snapshot()['budget']
        result = self.call('exec', '--name', 'bypass', '--timeout', '3', '--',
                           sys.executable, '-B', 'code.py', 'bypass', 'positive', 'outputs/bypass.json', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Human steering', result.stderr)
        store = ProjectStore(self.root)
        with self.assertRaisesRegex(ValueError, 'paused'):
            with store.theory_allowance(self.manifests['baseline'], {}, {'wall_seconds': 2}):
                self.fail('Paused theory work must not start')
        self.assertEqual(self.snapshot()['budget'], before)
        self.assertEqual(self.starts(), [])


class SteeringExternalAllowanceTests(unittest.TestCase):
    setUp = quick_fixture.QuickTests.setUp
    call = quick_fixture.QuickTests.call
    script = quick_fixture.QuickTests.script
    job = quick_fixture.QuickTests.job
    initialize_ledger = quick_fixture.QuickTests.initialize_ledger

    def test_existing_completed_child_is_retained_and_no_new_allowance_is_charged(self):
        self.initialize_ledger()
        store = ProjectStore(self.ledger)
        before = store.snapshot()
        submit(store, {'id': 'pause', 'kind': 'pause', 'message': 'Stop new work',
                      'contract_sha256': before['contract_sha256'], 'expected_revision': None},
               user_directed=True, source='current-user:external-fixture')
        from rds_quick import _charge_ledger
        with self.assertRaisesRegex(ValueError, 'Human steering'):
            _charge_ledger(self.ledger, self.root / 'child', {}, 2, dispatch=False)
        after = store.snapshot()
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertFalse((self.root / 'child').exists())


class SteeringDriveTests(unittest.TestCase):
    setUp = autonomy_fixture.AutonomyTests.setUp
    write = autonomy_fixture.AutonomyTests.write
    build = autonomy_fixture.AutonomyTests.build
    cli = autonomy_fixture.AutonomyTests.cli
    calls = autonomy_fixture.AutonomyTests.calls
    events = autonomy_fixture.AutonomyTests.events
    request = autonomy_fixture.AutonomyTests.request
    run_repair = autonomy_fixture.AutonomyTests.run_repair

    def steer(self, kind):
        snap = self.store.snapshot()
        self.write('instruction.json', {'id': kind, 'kind': kind, 'message': 'Current user ' + kind,
                   'contract_sha256': snap['contract_sha256'],
                   'expected_revision': snap.get('steering', {}).get('revision')})
        return self.cli('project', 'steer', '--request', str(self.root / 'instruction.json'),
                        '--user-directed', '--source', 'current-user:drive-fixture')

    def test_paused_drive_has_no_provider_call_and_resume_reaches_original_quality_endpoint(self):
        self.build()
        before = self.store.snapshot()['budget']
        self.steer('pause')
        result = self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(result['status'], 'HUMAN_STEERING_REQUIRED')
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.store.snapshot()['budget'], before)
        self.assertEqual(self.events('AUTONOMY_DRIVE_CLAIMED'), [])
        self.steer('resume')
        self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(json.loads((self.root / 'outputs/solve.json').read_text())['score'], 6)
        self.assertEqual(len(self.store.snapshot()['receipts']), 2)

    def test_exhausted_drive_accepts_pause_without_controller_allowance(self):
        self.build()
        with self.store._db() as db:
            db.execute('UPDATE budget SET spent=cap')
        before = self.store.snapshot()['budget']
        self.steer('pause')
        result = self.cli('project', 'drive', '--max-steps', '1')
        self.assertEqual(result['status'], 'HUMAN_STEERING_REQUIRED')
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.store.snapshot()['budget'], before)

    def pause_directly(self):
        snap = self.store.snapshot()
        return submit(self.store, {'id': 'race-pause', 'kind': 'pause', 'message': 'Pause committed during controller transition',
                      'contract_sha256': snap['contract_sha256'], 'expected_revision': snap.get('steering', {}).get('revision')},
                      user_directed=True, source='current-user:controller-race')

    def test_pause_after_initial_read_prevents_controller_reservation(self):
        self.build()
        import rds_autonomy as autonomy
        import rds_steering as steering
        real_status = steering.status
        before = self.store.snapshot()['budget']
        def read_then_pause(store):
            result = real_status(store)
            if not result['steering']['paused']:
                self.pause_directly()
            return result
        with patch.object(steering, 'status', side_effect=read_then_pause):
            result = autonomy.drive(self.store, max_steps=1)
        self.assertEqual(result['status'], 'HUMAN_STEERING_REQUIRED')
        self.assertEqual(self.events('AUTONOMY_DRIVE_CLAIMED'), [])
        self.assertEqual(self.store.snapshot()['budget'], before)
        self.assertEqual(self.calls(), [])

    def paid_adoption_race(self, between_transactions):
        self.build()
        self.run_repair()
        import rds_autonomy as autonomy
        import rds_method_revision as revision
        original = self.store.snapshot()
        source = (self.root / 'code.py').read_bytes()
        real_apply, real_db = revision.apply, self.store._db
        writes = 0
        @contextmanager
        def database(readonly=False):
            nonlocal writes
            if not readonly:
                writes += 1
                if writes == 2:
                    # PREPARED committed; ADOPTED transaction has not begun.
                    self.pause_directly()
            with real_db(readonly) as db:
                yield db
        def apply_after_pause(*args, **kwargs):
            if between_transactions:
                with patch.object(self.store, '_db', side_effect=database):
                    return real_apply(*args, **kwargs)
            self.pause_directly()
            return real_apply(*args, **kwargs)
        with patch.object(revision, 'apply', side_effect=apply_after_pause):
            result = autonomy.drive(self.store, max_steps=4)
        self.assertEqual(result['status'], 'HUMAN_STEERING_REQUIRED')
        self.assertEqual((self.root / 'code.py').read_bytes(), source)
        self.assertEqual(self.events('METHOD_REVISION_ADOPTED'), [])
        self.assertEqual(self.events('AUTONOMY_MODEL_PROCESSED'), [])
        self.assertEqual(self.store.snapshot()['receipts'], original['receipts'])
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(bool(self.events('METHOD_REVISION_PREPARED')), between_transactions)
        self.steer('resume')
        self.cli('project', 'drive', '--max-steps', '4')
        final = self.store.snapshot()
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(next(r for r in final['receipts'] if r['run_id'] == 'repair1'), original['receipts'][0])
        self.assertEqual(json.loads((self.root / 'outputs/solve.json').read_text())['score'], 6)

    def test_pause_before_adoption_keeps_paid_proposal_for_resume(self):
        self.paid_adoption_race(False)

    def test_pause_between_preparation_and_adoption_keeps_recoverable_proposal(self):
        self.paid_adoption_race(True)


if __name__ == '__main__':
    unittest.main()
