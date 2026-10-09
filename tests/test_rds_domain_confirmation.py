"""Domain boundaries use actual ProjectStore workers and retained receipts."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, canonical, digest
from rds_domain_confirmation import inspect_confirmation, validate_policy
from rds_verify import verify


CONFIRM = '''import hashlib,json,pathlib,sqlite3
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt=json.loads(db.execute("SELECT body FROM receipts WHERE run_id='candidate'").fetchone()[0])
sha=lambda p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
answer={'candidate_receipt_sha256':receipt['sha256'],'claim_sha256':sha('claim.json'),
        'evaluator_sha256':sha('evaluator.py'),'verdict':VERDICT}
pathlib.Path('outputs/confirmation.json').write_text(json.dumps(answer),encoding='utf-8')
'''


class DomainConfirmationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='rds-domain-confirmation-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ProjectStore(self.root)

    def write(self, path, value):
        target = self.root / path
        target.write_text(value if isinstance(value, str) else json.dumps(value, allow_nan=False), encoding='utf-8')

    def build(self, domain='mathematics', *, wrong=False, self_verdict=None, train=False, unsigned=False):
        if domain == 'mathematics':
            claim = {'schema': 1, 'kind': 'affine_dynamics', 'model': {'matrix': [['1/2']], 'bias': ['1/2']},
                     'threshold': '1', 'point': ['0' if wrong else '1']}
            payload = verify(claim)
            verdict = payload['status']
            if unsigned:
                payload = {'status': 'PASS', 'scientific_support': 'CONFIRMED'}
            data = [1]
        elif domain == 'algorithms':
            claim = {'schema': 1, 'kind': 'integer_sum_squares'}
            data = [[], [-3, 3], [1, 2, 3]]
            payload = None
            verdict = 'FAIL' if wrong else 'PASS'
        else:
            claim = {'schema': 1, 'kind': 'torch_linear_regression', 'input_dimension': 1, 'max_mse': .001}
            data = {'x': [[-1.], [0.], [1.], [2.]], 'y': [-1., 1., 3., 5.]}
            payload = {'weight': [2.], 'bias': 1.}
            verdict = 'PASS'
        self.write('claim.json', claim)
        self.write('data.json', data)
        self.write('config.json', {})
        self.write('candidate-input.json', payload)
        generic = "import pathlib\npathlib.Path('outputs/candidate.json').write_text(pathlib.Path('candidate-input.json').read_text(encoding='utf-8'),encoding='utf-8')\n"
        algorithm = '''import hashlib,json,pathlib,sys
xs=json.loads(pathlib.Path('data.json').read_text())
values=[sum(v*v for v in row) for row in xs]
if WRONG and sys.argv[1]=='candidate': values[-1]+=1
out={'values':values,'inputs_sha256':hashlib.sha256(pathlib.Path('data.json').read_bytes()).hexdigest()}
pathlib.Path('outputs/'+sys.argv[1]+'.json').write_text(json.dumps(out),encoding='utf-8')
'''.replace('WRONG', repr(wrong))
        trainer = '''import json,pathlib,torch
torch.set_num_threads(1)
torch.manual_seed(5)
d=json.loads(pathlib.Path('data.json').read_text())
x=torch.tensor(d['x'],dtype=torch.float32,device='cpu'); y=torch.tensor(d['y'],dtype=torch.float32,device='cpu').reshape(-1,1)
model=torch.nn.Linear(1,1,device='cpu'); opt=torch.optim.SGD(model.parameters(),lr=.1)
for _ in range(150):
    opt.zero_grad(); loss=torch.mean((model(x)-y)**2); loss.backward(); opt.step()
out={'weight':model.weight.detach().reshape(-1).tolist(),'bias':model.bias.detach().item()}
pathlib.Path('outputs/candidate.json').write_text(json.dumps(out),encoding='utf-8')
'''
        self.write('candidate.py', algorithm if domain == 'algorithms' else trainer if train else generic)
        self.write('evaluator.py', CONFIRM.replace('VERDICT', repr(self_verdict or verdict)))
        sha = lambda p: hashlib.sha256((self.root / p).read_bytes()).hexdigest()
        files = [('candidate.py', 'code'), ('config.json', 'config'), ('candidate-input.json', 'config'),
                 ('claim.json', 'config'), ('data.json', 'data'), ('evaluator.py', 'evaluator')]
        bindings = [{'path': p, 'role': role, 'sha256': sha(p)} for p, role in files]
        protocol = {role + '_sha256': ProjectStore._role_sha({'bindings': bindings}, role) for role in ('code', 'config', 'data')}
        protocol.update(data_split='finite-domain-regression', init='fixed', seed=5, checkpoint='none',
                        schedule='one real CPU execution per route', sample_work={'cases': len(data)}, numeric_protocol='declared finite semantics')
        self.write('protocol.json', protocol)
        bindings.append({'path': 'protocol.json', 'role': 'protocol', 'sha256': sha('protocol.json')})
        routes = []
        nodes = []
        ids = ['candidate'] + (['baseline'] if domain == 'algorithms' else []) + ['confirmation']
        self.manifests = {}
        for rid in ids:
            script = 'evaluator.py' if rid == 'confirmation' else 'candidate.py'
            argv = [sys.executable, '-B', script] + ([rid] if domain == 'algorithms' and rid != 'confirmation' else [])
            manifest = {'schema': 1, 'id': rid, 'arm': 'tool', 'control_id': None,
                        'protocol': {'path': 'protocol.json', 'sha256': sha('protocol.json')},
                        'argv': argv, 'outpaths': ['outputs/' + rid + '.json'],
                        'timeout_seconds': 20, 'resource_estimates': {'wall_seconds': 20}}
            self.manifests[rid] = manifest
            routes.append({'candidate': rid, 'manifest': manifest})
            pre = [{'fact': 'run.' + p + '.succeeded', 'op': 'eq', 'value': True}
                   for p in ids[:ids.index(rid)]]
            action = {'id': rid, 'kind': 'OBLIGATION_CHECK', 'target': 'confirmed',
                      'description': 'Run bounded domain fixture', 'claim': 'Finite regression only',
                      'required_observables': ['confirmed'],
                      'outcomes': [{'observation': 'verified', 'next_decision': 'review'},
                                   {'observation': 'counterexample', 'next_decision': 'repair'},
                                   {'observation': 'unresolved', 'next_decision': 'retain unknown'}]}
            nodes.append({'id': rid, 'sources': ['finite independent check'],
                          'executable': {'decisions': ['next'], 'preconditions': pre, 'action': action}})
        rules = {'kind': {'mathematics': 'exact_certificate', 'algorithms': 'integer_sum_squares',
                          'deep_learning': 'torch_linear_regression'}[domain],
                 'candidate_output': 'outputs/candidate.json', 'confirmation_output': 'outputs/confirmation.json'}
        if domain == 'algorithms':
            rules.update(baseline_run='baseline', baseline_output='outputs/baseline.json')
        declaration = {'schema': 1, 'domain': domain, 'candidate_runs': ['candidate'],
                       'confirmation_runs': ['confirmation'], 'scope': 'FINITE_TEST_ONLY',
                       'claim': {'path': 'claim.json', 'sha256': sha('claim.json')},
                       'evaluator': {'path': 'evaluator.py', 'sha256': sha('evaluator.py')},
                       'data': [{'path': 'data.json', 'sha256': sha('data.json')}], 'rules': rules}
        policy = {'schema': 1, 'context': {'decision': {'id': 'next', 'goal_revision': 'finite-v1',
                  'scope': {'domain': domain}, 'goal_conditions': [{'fact': 'confirmed', 'op': 'eq', 'value': 'PASS'}]}},
                  'graph': {'nodes': nodes, 'edges': []}, 'routes': routes,
                  'observations': [{'fact': 'confirmed', 'run_id': 'confirmation', 'path': 'outputs/confirmation.json',
                                    'selector': {'pointer': '/verdict'}}], 'confirmation': declaration}
        contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [r['manifest']['argv'] for r in routes],
                    'budget': {'wall_seconds': 100}, 'output_roots': ['outputs'], 'advisor_policy': policy}
        self.store.initialize(contract)
        self.contract = self.store.snapshot()['contract']

    def run_all(self):
        for rid, spec in self.manifests.items():
            self.store.register(spec)
            self.assertEqual(self.store.execute(rid)['run_status'], 'SUCCEEDED')
        return inspect_confirmation(self.store, self.contract)

    def test_exact_proof_is_independently_replayed(self):
        self.build()
        result = self.run_all()
        self.assertEqual(result['task_confirmation'], 'PASS', result)
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertEqual(result['assurance'], 'CERTIFICATE_CHECKED')

    def test_checked_counterexample_is_task_fail_not_execution_failure(self):
        self.build(wrong=True)
        result = self.run_all()
        self.assertEqual(result['task_confirmation'], 'FAIL', result)
        self.assertEqual(result['execution']['confirmation'], 'SUCCEEDED')

    def test_confirmation_self_success_cannot_override_replay(self):
        self.build(wrong=True, self_verdict='PASS')
        result = self.run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('contradicts replay', ' '.join(result['reasons']))

    def test_candidate_self_signed_pass_has_no_certificate(self):
        self.build(unsigned=True)
        result = self.run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('independent replay', ' '.join(result['reasons']))

    def test_pending_has_no_promoted_verdict(self):
        self.build()
        self.assertEqual(inspect_confirmation(self.store, self.contract)['task_confirmation'], 'PENDING')

    def test_original_artifact_tampering_is_unknown(self):
        self.build()
        self.run_all()
        self.write('outputs/candidate.json', {'status': 'PASS'})
        self.assertEqual(inspect_confirmation(self.store, self.contract)['task_confirmation'], 'UNKNOWN')

    def test_finite_algorithm_and_same_input_cost_are_reported(self):
        self.build('algorithms')
        result = self.run_all()
        self.assertEqual(result['task_confirmation'], 'PASS', result)
        self.assertEqual(result['case_count'], 3)
        self.assertEqual(result['cost_comparison']['general_speedup'], 'UNKNOWN')
        self.assertGreater(result['cost_comparison']['baseline_wall_seconds'], 0)

    def test_algorithm_wrong_actual_value_is_a_counterexample(self):
        self.build('algorithms', wrong=True)
        self.assertEqual(self.run_all()['task_confirmation'], 'FAIL')

    def test_forged_supplied_receipt_is_not_retained_evidence(self):
        self.build()
        self.run_all()
        state = self.store.snapshot()
        altered = state['receipts'][0]
        altered['worker_pid'] += 1
        altered['sha256'] = digest({k: v for k, v in altered.items() if k != 'sha256'})
        result = inspect_confirmation(self.store, self.contract, state)
        self.assertEqual(result['task_confirmation'], 'UNKNOWN')
        self.assertIn('retained original', ' '.join(result['reasons']))

    def test_confirmation_cannot_be_candidate_or_unbound_evaluator(self):
        self.build()
        contract = deepcopy(self.contract)
        declaration = contract['advisor_policy']['confirmation']
        declaration['confirmation_runs'] = ['candidate']
        with self.assertRaises(ValueError):
            validate_policy(self.store, contract, contract['advisor_policy'])

    def test_torch_unavailable_preserves_unknown(self):
        self.build('deep_learning')
        with patch.dict(sys.modules, {'torch': None}):
            result = self.run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN')
        self.assertIn('torch CPU dependency unavailable', result['reasons'])

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'torch CPU dependency unavailable')
    def test_actual_torch_training_and_recomputed_metric_do_not_hide_exposure(self):
        self.build('deep_learning', train=True)
        result = self.run_all()
        self.assertEqual(result['finite_evaluation'], 'PASS', result)
        self.assertLessEqual(result['measured_mse'], .001)
        self.assertEqual(result['confirmation_independence'], 'DECLARED_EXPOSED')
        self.assertEqual(result['task_confirmation'], 'UNKNOWN')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
