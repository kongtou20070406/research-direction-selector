"""Real CLI bridges: native qualification, owned application and consumers.

These are labelled finite software cases, not scientific effectiveness trials.
"""
from copy import deepcopy
import hashlib
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
from rds_project import ProjectStore, digest
from rds_method_revision import _code_sha
from rds_owned_advisor import prepare_admission, check_admission
from rds_math import get, put


class OwnedToolsCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-owned-tools-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = ProjectStore(self.root)
        self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}
        self.trace = []

    def write(self, name, value):
        raw = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
        (self.root / name).write_text(raw, encoding='utf-8')
        return name

    def call(self, *args, ok=True):
        if args and args[0] == 'rsi':
            args = (*args, '--json')
        p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), *args],
                           capture_output=True, text=True, encoding='utf-8', timeout=40, env=self.env)
        self.trace.append({'argv': list(args), 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        if ok:
            self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-4500:])
            return json.loads(p.stdout)
        return p

    def setup_campaign(self, *, consumed=True, wrong_inputs=False, qualification=True, negative_first=False,
                       disconnected=False, interface='full'):
        cases = [{'args': [[1, 2, 3]], 'expected': 14}, {'args': [[-3, 3]], 'expected': 18}]
        self.write('source.py', 'def sum_squares(values):\n    return sum(v * v for v in values)\n')
        self.write('cases.json', cases)
        inputs = [{'args': c['args'], 'kwargs': c.get('kwargs', {})} for c in cases]
        self.write('inputs.json', inputs)
        if negative_first:
            self.write('bad.py', 'def wrong(values):\n    return 0\n')
            self.call('rsi', 'extract', '--source', str(self.root / 'bad.py'), '--entry', 'wrong', '--name', 'wrong')
            failed_run = self.call('rsi', 'validate', '--name', 'wrong', '--cases', str(self.root / 'cases.json'), '--timeout', '5', ok=False)
            self.assertEqual(failed_run.returncode, 1)
            failed = json.loads(failed_run.stdout)
            self.assertEqual(failed['status'], 'FAILED')
        self.call('rsi', 'extract', '--source', str(self.root / 'source.py'), '--entry', 'sum_squares', '--name', 'squares')
        validation = self.call('rsi', 'validate', '--name', 'squares', '--cases', str(self.root / 'cases.json'), '--timeout', '5')
        self.call('rsi', 'register', '--name', 'squares', '--validation', validation['id'])
        decision = {'id': 'next', 'goal_revision': 'finite-software-v1', 'scope': {'domain': 'finite-software'},
                    'goal_conditions': [{'fact': 'task.status' if consumed else 'run.apply.succeeded',
                                         'op': 'eq', 'value': 'PASS' if consumed else True}]}
        action = {'id': 'apply', 'kind': 'PAIRED_TEST', 'target': 'task.status' if consumed else 'finite-task',
                  'operation': 'sum-squares-on-current-inputs', 'description': 'Apply a locally qualified finite function',
                  'competing_explanations': ['the finite inputs pass', 'the finite inputs fail'],
                  'required_observables': ['task.status'],
                  'outcomes': [{'observation': 'PASS', 'next_decision': 'read original finite results'},
                               {'observation': 'FAIL', 'next_decision': 'inspect preserved failure'}]}
        self.write('decision.json', decision)
        self.write('action.json', action)
        prepared = self.call('rsi', 'prepare-application', '--name', 'squares', '--inputs', 'inputs.json',
                             '--cases', 'cases.json', '--code-path', 'qualified.py', '--driver', 'apply.py',
                             '--request', 'request.json', '--output', 'outputs/application.json',
                             '--decision', str(self.root / 'decision.json'), '--action-file', str(self.root / 'action.json'),
                             '--candidate', 'apply', '--run-id', 'apply', '--obligation', action['target'],
                             '--observation-fact', 'task.status')
        self.assertFalse(prepared['execution_started'])
        self.assertEqual(prepared['authorization'], 'UNCHANGED')
        self.binding = prepared['binding']
        bindings = prepared['required_bindings']
        if wrong_inputs:
            self.write('inputs.json', [{'args': [[5]], 'kwargs': {}}])
            sha = hashlib.sha256((self.root / 'inputs.json').read_bytes()).hexdigest()
            next(b for b in bindings if b['path'] == 'inputs.json')['sha256'] = sha
            self.binding['qualification']['task_inputs']['sha256'] = sha
            # Request identity remains original: qualification rejects before that path can run.
        if not qualification:
            self.binding['qualification'] = self.binding['application'] = None
        protocol = {'code_sha256': _code_sha({'bindings': bindings}),
                    'config_sha256': next(b['sha256'] for b in bindings if b['role'] == 'config'),
                    'data_sha256': next(b['sha256'] for b in bindings if b['role'] == 'data'),
                    'data_split': 'finite-software-development', 'init': 'none', 'seed': 0, 'checkpoint': 'none',
                    'schedule': 'two bounded calls', 'sample_work': {'cases': 2}, 'numeric_protocol': 'Python integer'}
        self.write('protocol.json', protocol)
        protocol_ref = {'path': 'protocol.json', 'sha256': hashlib.sha256((self.root / 'protocol.json').read_bytes()).hexdigest()}
        bindings.append({'role': 'protocol', **protocol_ref})
        self.manifest = {'schema': 1, 'id': 'apply', 'arm': 'tool', 'control_id': None, 'protocol': protocol_ref,
                         'argv': prepared['argv'], 'outpaths': ['outputs/application.json'],
                         'resource_estimates': {'wall_seconds': 10}, 'timeout_seconds': 10}
        self.contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [prepared['argv']],
                         'output_roots': ['outputs'], 'budget': {'wall_seconds': 40},
                         'advisor_policy': {'schema': 1, 'context': {'decision': decision},
                                            'graph': {'nodes': [{'id': 'finite-task', 'sources': ['synthetic-case'],
                                                                'executable': {'decisions': ['next'], 'preconditions': [],
                                                                               'action': action}}], 'edges': []},
                                            'routes': [{'candidate': 'apply', 'manifest': self.manifest}],
                                            'observations': [{'fact': 'task.status', 'run_id': 'apply',
                                                              'path': 'outputs/application.json', 'selector': {'pointer': '/status'}}],
                                            'tool_bindings': [self.binding]}}
        if disconnected:
            self.contract['advisor_policy']['graph']['nodes'].append({
                'id': 'unvisited', 'sources': ['synthetic-unvisited'],
                'executable': {'decisions': ['another-decision'],
                               'preconditions': [{'fact': 'task.status', 'op': 'eq', 'value': 'PASS'}],
                               'satisfied_when': [{'fact': 'task.status', 'op': 'eq', 'value': 'PASS'}]}})
        if interface != 'full':
            self.contract['method_evolution'] = {'schema': 1, 'code_paths': ['qualified.py'], 'max_revisions': 1}
            # An actual authorized material replacement, rather than a policy-only rename.
            (self.root / 'replacement.py').write_bytes((self.root / 'qualified.py').read_bytes())
            self.write('qualified.py', 'def sum_squares(values):\n    return 0\n')
            next(b for b in bindings if b['path'] == 'qualified.py')['sha256'] = hashlib.sha256((self.root / 'qualified.py').read_bytes()).hexdigest()
            protocol['code_sha256'] = _code_sha({'bindings': bindings})
            self.write('protocol.json', protocol)
            protocol_ref['sha256'] = hashlib.sha256((self.root / 'protocol.json').read_bytes()).hexdigest()
            next(b for b in bindings if b['role'] == 'protocol')['sha256'] = protocol_ref['sha256']
            if interface == 'missing':
                del self.contract['advisor_policy']['tool_bindings']
            else:
                self.contract['advisor_policy']['tool_bindings'] = []
        self.write('contract.json', self.contract)
        return self.call('project', 'init', '--contract', str(self.root / 'contract.json'))

    def rows(self, table):
        with self.store._db(True) as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM ' + table + ' ORDER BY rowid')]

    def test_real_cli_qualification_application_history_and_consumption(self):
        self.setup_campaign(negative_first=True)
        before = self.call('project', 'next')
        use = before['tool_utilization']
        self.assertEqual(use['counts']['applicable'], 1)
        self.assertEqual(use['counts']['used'], 0)
        self.assertEqual(use['counts']['consumed'], 0)
        self.assertTrue(use['tools'][0]['qualification_cost']['parent_budget_accounted'])
        init_state = self.store.snapshot()
        charge = next(e for e in self.rows('events') if e['kind'] == 'TOOL_PREPARATION_COST')
        self.assertEqual(len(charge['validations']), 2)
        self.assertIn('FAILED', {v['status'] for v in charge['validations']})
        self.assertEqual(init_state['budget']['wall_seconds']['charged_estimate'], charge['wall_seconds'])
        complete = self.call('project', 'advance')
        self.assertEqual(complete['receipt']['run_status'], 'SUCCEEDED')
        use = complete['advisor']['tool_utilization']
        self.assertEqual(use['counts']['used'], 1)
        self.assertEqual(use['counts']['consumed'], 1)
        self.assertEqual(use['applicable_consumption_rate'], 1)
        self.assertTrue(use['tools'][0]['result_consumed'])
        self.assertIsNone(complete['advisor']['selected_run'])
        state = self.store.snapshot()
        checkpoints = self.rows('checkpoints')
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(checkpoints[0]['decision']['outcome'], 'plan_locked')
        self.assertEqual(checkpoints[1]['decision']['evidence'], checkpoints[0]['decision']['evidence'])
        self.assertEqual(checkpoints[1]['decision']['execution']['receipt_sha256'], complete['receipt']['sha256'])
        report = self.call('project', 'next')
        self.call('project', 'recover', '--id', 'apply')
        self.call('project', 'init', '--contract', str(self.root / 'contract.json'))
        self.assertEqual(self.store.snapshot()['budget'], state['budget'])
        self.assertEqual(len(self.rows('checkpoints')), 2)
        self.assertEqual(sum(e['kind'] == 'TOOL_RESULT_CONSUMED' for e in self.rows('events')), 1)
        # Opt-in preserve the real CLI transcript for an outer frozen evidence run.
        evidence_root = os.environ.get('RDS_TEST_EVIDENCE_ROOT')
        if evidence_root:
            path = Path(evidence_root) / self._testMethodName
            path.mkdir(parents=True, exist_ok=True)
            (path / 'cli-transcript.json').write_text(json.dumps(self.trace, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(report['tool_utilization']['tools'][0]['scientific_support'], 'UNKNOWN')

    def test_used_output_without_a_decision_reader_is_unconsumed(self):
        self.setup_campaign(consumed=False, disconnected=True)
        result = self.call('project', 'advance')['advisor']['tool_utilization']
        self.assertTrue(result['tools'][0]['used'])
        self.assertFalse(result['tools'][0]['consumed'])
        self.assertEqual(result['applicable_consumption_rate'], 0)
        self.assertEqual(sum(e['kind'] == 'TOOL_RESULT_CONSUMED' for e in self.rows('events')), 0)

    def test_policy_revision_cannot_admit_unaccounted_native_qualification(self):
        self.setup_campaign(interface='missing')
        before = self.store.snapshot()
        policy = deepcopy(self.contract['advisor_policy'])
        policy['tool_bindings'] = [self.binding]
        proposal = {'id': 'add-consumer', 'parent_sha256': digest(self.contract),
                    'reason': 'Adopt a material prequalified tool for the existing obligation', 'policy': policy,
                    'code_replacements': [{'path': 'qualified.py', 'source': 'replacement.py',
                                           'sha256': hashlib.sha256((self.root / 'replacement.py').read_bytes()).hexdigest()}]}
        self.write('proposal.json', proposal)
        self.call('project', 'revise', '--proposal', str(self.root / 'proposal.json'))
        result = self.call('project', 'advance')
        self.assertIsNone(result['selected_run'])
        row = result['tool_utilization']['tools'][0]
        self.assertEqual(row['status'], 'UNKNOWN')
        self.assertFalse(row['qualification_cost']['parent_budget_accounted'])
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])

    def test_empty_genesis_interface_can_consume_a_prepaid_candidate_after_revision(self):
        self.setup_campaign(interface='empty')
        before = self.store.snapshot()
        self.assertGreater(before['budget']['wall_seconds']['charged_estimate'], 0)
        policy = deepcopy(self.contract['advisor_policy'])
        policy['tool_bindings'] = [self.binding]
        proposal = {'id': 'add-prepaid', 'parent_sha256': digest(self.contract),
                    'reason': 'Adopt a prequalified candidate within the original envelope', 'policy': policy,
                    'code_replacements': [{'path': 'qualified.py', 'source': 'replacement.py',
                                           'sha256': hashlib.sha256((self.root / 'replacement.py').read_bytes()).hexdigest()}]}
        self.write('proposal.json', proposal)
        self.call('project', 'revise', '--proposal', str(self.root / 'proposal.json'))
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        result = self.call('project', 'advance')
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(result['advisor']['tool_utilization']['counts']['consumed'], 1)
        self.assertEqual(sum(e['kind'] == 'TOOL_PREPARATION_COST' for e in self.rows('events')), 1)

    def test_wrong_task_and_unknown_qualification_do_not_execute(self):
        self.setup_campaign(wrong_inputs=True)
        result = self.call('project', 'advance')
        self.assertIsNone(result['selected_run'])
        self.assertEqual(result['tool_utilization']['counts']['inapplicable'], 1)
        self.assertIsNone(result['tool_utilization']['applicable_use_rate'])
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertFalse((self.root / 'outputs/application.json').exists())

    def test_catalogue_match_is_unknown_and_is_not_a_free_execution(self):
        self.setup_campaign(qualification=False)
        result = self.call('project', 'advance')
        self.assertIsNone(result['selected_run'])
        self.assertEqual(result['tool_utilization']['counts']['unknown'], 1)
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_refutation_after_review_blocks_admission(self):
        self.setup_campaign()
        token = prepare_admission(self.store, self.manifest)
        put(self.root, 'refute:late', 'refutation', b'late scope contradiction',
            dependencies=[self.binding['tool']['id']], data={'target': self.binding['tool']['id']})
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'applicability changed'):
                check_admission(self.store, db, self.manifest, token)
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_changed_application_bytes_do_not_turn_old_receipt_into_use(self):
        self.setup_campaign()
        finished = self.call('project', 'advance')
        self.assertTrue(finished['advisor']['tool_utilization']['tools'][0]['consumed'])
        self.write('outputs/application.json', {'status': 'PASS', 'case_count': 2})
        rejected = self.call('project', 'next', ok=False)
        # Collection reports a recoverable original evidence failure, rather than inventing new use.
        result = json.loads(rejected.stdout)
        self.assertEqual(result['status'], 'COLLECTION_FAILED')
        self.assertFalse(result['tool_utilization']['tools'][0]['used'])
        self.assertEqual(len(self.store.snapshot()['receipts']), 1)

    def test_live_native_validation_cannot_run_outside_the_frozen_budget(self):
        self.setup_campaign()
        before = self.store.snapshot()
        result = self.call('rsi', 'validate', '--name', 'squares', '--cases', 'cases.json', '--timeout', '5', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('frozen budget', result.stderr + result.stdout)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])


if __name__ == '__main__':
    unittest.main()
