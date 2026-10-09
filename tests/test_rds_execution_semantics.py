"""Issue #254: synthetic execution blockers retain their distinct meaning."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import test_rds_project as project_fixture
import test_rds_owned_advisor as owned_fixture
import test_rds_domain_confirmation as confirmation_fixture
from rds_project import ProjectStore
from rds_owned_advisor import review


class ExecutionSemanticsTests(unittest.TestCase):
    def cli(self, root, *arguments):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                 '--root', str(root), *arguments], capture_output=True,
                                text=True, encoding='utf-8', timeout=45,
                                env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def retain(self, name, value):
        directory = Path.cwd() / 'out' / 'execution-semantics-observations'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (name + '.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')

    def legacy(self):
        helper = project_fixture.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        return helper

    def owned(self, resource=None):
        helper = owned_fixture.OwnedAdvisorCLITests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        def change(policy):
            if resource is not None:
                cap = {'wall_seconds': 30, 'cpu_seconds': 10, 'gpu_seconds': 0}[resource]
                for route in policy['routes']:
                    route['manifest']['resource_estimates'][resource] = cap + 1
        helper.initialize(mutate_policy=change)
        return helper

    def confirmation(self, **options):
        helper = confirmation_fixture.DomainConfirmationTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        initialize = ProjectStore.initialize
        def lifecycle_goal(store, contract, **kwargs):
            contract = deepcopy(contract)
            contract['advisor_policy']['context']['decision']['goal_conditions'] = [
                {'fact': 'run.candidate.succeeded', 'op': 'eq', 'value': True}]
            return initialize(store, contract, **kwargs)
        with patch.object(ProjectStore, 'initialize', new=lifecycle_goal):
            helper.build(**options)
        return helper

    def goal(self, report):
        return next(row['search']['selection_review']['goal'] for row in report['recommendations']
                    if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')

    def test_retained_failure_requires_review_without_recovery_loop_or_rerun(self):
        helper = self.legacy()
        receipt = helper.run_spec(helper.spec(mode='nonzero'))
        before = helper.store.snapshot()
        result = self.cli(helper.root, 'project', 'next')
        recovered = helper.store.recover('r1')
        again = self.cli(helper.root, 'project', 'next')
        after = helper.store.snapshot()
        self.retain('settled-failure', {'receipt': receipt, 'before': before, 'next': result,
                                      'recovered': recovered, 'next_after': again, 'after': after})
        self.assertEqual(receipt['run_status'], 'FAILED')
        self.assertEqual(result['disposition'], 'REVIEW_EXECUTION_FAILURE')
        self.assertEqual(result['receipt_sha256'], receipt['sha256'])
        self.assertNotIn('project recover', result['command'])
        self.assertEqual(recovered['sha256'], receipt['sha256'])
        self.assertEqual(again, result)
        self.assertEqual(after, before)

    def test_unsettled_attempt_still_requires_recovery(self):
        helper = self.legacy()
        snapshot = helper.store.snapshot()
        snapshot['runs'] = [{'id': 'unsettled', 'status': 'INTERRUPTED'}]
        with patch.object(helper.store, 'snapshot', return_value=snapshot):
            result = helper.store.next_move()
        self.assertIn('project recover', result['command'])

    def test_settled_failure_cannot_hide_later_unsettled_attempt(self):
        helper = self.legacy()
        receipt = helper.run_spec(helper.spec(mode='nonzero'))
        snapshot = helper.store.snapshot()
        snapshot['runs'].append({'id': 'unsettled', 'status': 'INTERRUPTED'})
        with patch.object(helper.store, 'snapshot', return_value=snapshot):
            result = helper.store.next_move()
        self.assertIn('project recover --id=unsettled', result['command'])
        self.assertEqual(snapshot['receipts'][0]['sha256'], receipt['sha256'])

    def test_settled_failure_cannot_hide_running_attempt(self):
        helper = self.legacy()
        helper.run_spec(helper.spec(mode='nonzero'))
        snapshot = helper.store.snapshot()
        snapshot['runs'].append({'id': 'live', 'status': 'RUNNING'})
        with patch.object(helper.store, 'snapshot', return_value=snapshot):
            result = helper.store.next_move()
        self.assertEqual(result['next_move'], 'wait for the running attempt, then re-check status')

    def test_retained_interruption_is_reviewed_without_new_attempt(self):
        helper = self.legacy()
        helper.test_interrupted_recovery_preserves_unknown_cost_and_no_rerun()
        before = helper.store.snapshot()
        result = self.cli(helper.root, 'project', 'next')
        self.assertEqual(result['disposition'], 'REVIEW_EXECUTION_FAILURE')
        self.assertEqual(result['run_status'], 'INTERRUPTED')
        self.assertEqual(helper.store.snapshot(), before)

    def test_resource_shortfalls_are_reported_by_real_advance_for_every_dimension(self):
        for resource in ('wall_seconds', 'cpu_seconds', 'gpu_seconds'):
            with self.subTest(resource=resource):
                helper = self.owned(resource)
                before = helper.snapshot()
                result = helper.output('project', 'advance')
                self.retain('resource-' + resource, {'before': before, 'report': result,
                                                    'after': helper.snapshot()})
                self.assertIsNone(result['selected_run'])
                self.assertEqual(result['next_move']['kind'], 'RESOURCE_BLOCKED')
                blocked = next(row for row in result['resource_blockers'] if row['run_id'] == 'baseline')
                self.assertIn(resource, blocked['shortfalls'])
                self.assertGreater(blocked['shortfalls'][resource]['required'],
                                   blocked['shortfalls'][resource]['remaining'])
                self.assertEqual(helper.starts(), [])
                self.assertEqual(helper.snapshot()['budget'], before['budget'])
                self.assertEqual(helper.snapshot()['runs'], before['runs'])

    def test_available_resources_preserve_normal_dispatch(self):
        helper = self.owned()
        result = helper.output('project', 'advance')
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(helper.starts(), ['baseline'])

    def test_resource_shortfall_cannot_mask_method_gates_in_real_next(self):
        from test_rds_methods import method, policy
        for gate, status in (('conflict', 'BLOCKED_METHOD'),
                             ('description', 'NEEDS_METHOD_DESCRIPTION'),
                             ('clarification', 'NEEDS_METHOD_CLARIFICATION')):
            for over in (False, True):
                with self.subTest(gate=gate, over_budget=over):
                    helper = owned_fixture.OwnedAdvisorCLITests()
                    helper.setUp()
                    self.addCleanup(helper.doCleanups)
                    def change(p):
                        p['context'].update(policy())
                        if gate == 'clarification':
                            p['context']['method_constraints'].append({'id': 'unclear',
                                'quote': 'Use exact methods', 'source': 'user:fixture',
                                'status': 'UNRESOLVED', 'question': 'Which steps?'})
                        for node in p['graph']['nodes']:
                            if gate != 'description':
                                node['executable']['action']['methods'] = method(
                                    device='gpu' if gate == 'conflict' else 'cpu')
                        if over:
                            for route in p['routes']:
                                route['manifest']['resource_estimates']['wall_seconds'] = 31
                    helper.initialize(mutate_policy=change)
                    before = helper.snapshot()
                    result = helper.output('project', 'next')
                    search = next(row['search'] for row in result['recommendations']
                                  if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                    candidate = next(c for c in search['candidates'] + search['blocked_candidates']
                                     if c.get('action', {}).get('id') == 'baseline')
                    self.assertEqual(candidate['status'], status)
                    self.assertEqual(candidate['method_review']['status'],
                                     'CONFLICT' if gate == 'conflict' else 'UNKNOWN')
                    self.assertEqual(candidate['budget_status'],
                                     'OVER_REPORTED_BUDGET' if over else 'WITHIN_REPORTED_BUDGET')
                    self.assertNotEqual(result.get('next_move', {}).get('kind'), 'RESOURCE_BLOCKED')
                    self.assertEqual(result.get('resource_blockers', []), [])
                    self.assertIsNone(result['selected_run'])
                    self.assertEqual(helper.starts(), [])
                    self.assertEqual(helper.snapshot(), before)
                    self.retain('method-resource-' + gate + '-' + str(over), result)

    def test_pause_has_priority_over_resource_block(self):
        helper = self.owned('cpu_seconds')
        from rds_steering import submit
        store = ProjectStore(helper.root)
        submit(store, {'id': 'pause', 'contract_sha256': helper.snapshot()['contract_sha256'],
                       'expected_revision': None, 'kind': 'pause', 'message': 'Synthetic current-user pause'},
               user_directed=True, source='explicit synthetic test request')
        report = helper.output('project', 'advance')
        self.assertEqual(report['next_move']['kind'], 'HUMAN_STEERING')
        self.assertIsNone(report['selected_run'])
        self.assertEqual(helper.starts(), [])

    def test_history_integrity_repair_has_priority_over_resource_block(self):
        helper = self.owned('cpu_seconds')
        from rds_advisor import RDSAdvisor
        def failed_read(advisor, state, context, search):
            result = {'status': 'REVIEW_REQUIRED', 'limitations': [],
                      'flags': [{'kind': 'LOOP_HISTORY_REVIEW_ERROR', 'reason': 'Synthetic history read error'}]}
            search['loop_review'] = result
            return result
        with patch.object(RDSAdvisor, '_review_loop_history', new=failed_read):
            report = review(ProjectStore(helper.root), persist=False)
        self.assertEqual(report['next_move']['kind'], 'RESOLVE_PREMISE')
        self.assertIn('history integrity', report['next_move']['reason'])
        self.assertIsNone(report['selected_run'])
        self.assertTrue(report['resource_blockers'])

    def test_pending_confirmation_keeps_goal_unknown_and_selects_confirmation(self):
        helper = self.confirmation()
        helper.store.register(helper.manifests['candidate'])
        helper.store.execute('candidate')
        report = self.cli(helper.root, 'project', 'next')
        self.retain('confirmation-pending', {'confirmation': report['confirmation'], 'goal': self.goal(report),
                                            'fact': report['context']['facts']['confirmation.task_status'],
                                            'next_move': report['next_move']})
        self.assertEqual(report['confirmation']['task_confirmation'], 'PENDING')
        self.assertEqual(self.goal(report)['status'], 'UNKNOWN')
        self.assertEqual(report['selected_run'], 'confirmation')
        fact = report['context']['facts']['confirmation.task_status']
        self.assertEqual(fact['value'], 'PENDING')
        self.assertIn('locator', fact['source'])

    def test_unknown_confirmation_is_not_goal_refutation(self):
        helper = self.confirmation(unsigned=True)
        helper.run_all()
        report = self.cli(helper.root, 'project', 'next')
        self.retain('confirmation-unknown', {'confirmation': report['confirmation'], 'goal': self.goal(report),
                                            'fact': report['context']['facts']['confirmation.task_status'],
                                            'next_move': report['next_move']})
        self.assertEqual(report['confirmation']['task_confirmation'], 'UNKNOWN')
        self.assertEqual(self.goal(report)['status'], 'UNKNOWN')
        self.assertEqual(report['scientific_support'], 'UNKNOWN')

    def test_completed_confirmation_fail_still_refutes_goal(self):
        helper = self.confirmation(wrong=True)
        helper.run_all()
        report = review(helper.store)
        self.assertEqual(report['confirmation']['task_confirmation'], 'FAIL')
        self.assertEqual(self.goal(report)['status'], 'FALSE')

    def test_completed_confirmation_pass_still_meets_scoped_goal(self):
        helper = self.confirmation()
        helper.run_all()
        report = review(helper.store)
        self.assertEqual(report['confirmation']['task_confirmation'], 'PASS')
        self.assertEqual(self.goal(report)['status'], 'TRUE')
        self.assertIsNone(report['selected_run'])
        self.assertEqual(report['scientific_support'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
