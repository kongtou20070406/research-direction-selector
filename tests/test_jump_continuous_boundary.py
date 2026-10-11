"""Explicit floating-point oracle controls, not model or discovery scores.

These small synthetic rows exercise admission and feedback boundaries without
external benchmark data. The scorer reads candidate output, never its code.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_autonomy import drive
from rds_math import blob
from rds_project import ProjectStore, canonical, file_sha
import rds_jump as jump
import rds_structure as structure

spec = importlib.util.spec_from_file_location('continuous_boundary_example', REPO / 'examples/jump-loop/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)

ORACLE_EVALUATE = '''def evaluate(tree, env):
    if 'var' in tree:
        return env[tree['var']]
    if 'const' in tree:
        return tree['const']
    if tree['op'] != 'mul':
        raise ValueError('Only the explicitly supplied product control is supported')
    return evaluate(tree['args'][0], env) * evaluate(tree['args'][1], env)
'''
ORACLE_MAIN = '''def main():
    kind, output = sys.argv[1:]
    req, inputs, docs = context(kind)
    domain = read('domain.json')
    if kind == 'probe':
        result = {'observations': domain['observations'], 'scope': 'SYNTHETIC_FLOAT_CONTROL'}
    elif kind == 'refresh':
        result = {'sources': read('corpus.json'), 'scope': 'EXPLICIT_ORACLE_CONTROL'}
    elif kind == 'synthesize':
        search = {'candidate': read('corpus.json')[0]['oracle'], 'rival': {'const': 0},
                  'probe': domain['observations'][0]['inputs'], 'status': 'EXPLICIT_ORACLE_CONTROL'}
        result = {'status': 'PROPOSED', 'search': search, 'reason': 'Supplied control, not discovered',
                  'proposals': [proposal(req, search, read('template.json'))]}
    else:
        raise ValueError('Unexpected control stage')
    Path(output).write_text(canonical({'schema': 1, 'kind': kind,
        'request_sha256': hashlib.sha256(canonical(req).encode()).hexdigest(),
        'inputs': inputs, 'result': result}), encoding='utf-8')

if __name__ == '__main__':
    main()
'''
NUMERIC_SCORER = '''import json
import math
from pathlib import Path
import sqlite3
import sys

rid, source, output = sys.argv[1:]
read = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt = json.loads(db.execute('SELECT body FROM receipts WHERE run_id=?', (rid,)).fetchone()[0])
points = read('points.json')['points']
expected = [float(p['coordinates'][0]) * float(p['coordinates'][1]) for p in points]
actual = [float(row[0]) for row in read(source)['values']]
passed = len(actual) == len(expected) and all(math.isclose(a, b, rel_tol=0, abs_tol=1e-12)
                                            for a, b in zip(actual, expected))
probe = read('out/synthesize.json')['result']['search']['probe']
verdict = 'PASS' if passed else 'FAIL'
Path(output).write_text(json.dumps({'candidate_receipt_sha256': receipt['sha256'], 'verdict': verdict,
    'replay': {'status': verdict, 'expected': expected, 'actual': actual},
    'probe_value': probe['x'] * probe['y'], 'scope': 'FINITE_SYNTHETIC_NUMERIC_CONTROL'}), encoding='utf-8')
'''


class JumpContinuousBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-continuous-boundary-')
        self.addCleanup(self.tmp.cleanup)

    def build(self):
        root, contract = example.build(Path(self.tmp.name) / 'project')
        write = lambda name, value: (root / name).write_text(
            value if isinstance(value, str) else canonical(value), encoding='utf-8')
        candidate = (root / 'candidate.py').read_text(encoding='utf-8')
        self.assertIn('int(v)', candidate)
        write('candidate.py', candidate.replace('int(v)', 'float(v)'))
        write('evaluator.py', NUMERIC_SCORER)
        generator = (root / 'generator.py').read_text(encoding='utf-8').split('def main():')[0]
        generator = generator.replace('from rds_jump_search import diagnose, synthesize, evaluate', ORACLE_EVALUATE)
        generator = generator.replace('from instrument import measure', '')
        generator = generator.replace('Exact finite integer relation: ', 'Explicit floating-point oracle control: ')
        generator = generator.replace('Bounded grammar search fits the acquired observations', 'Supplied oracle tests integration only')
        write('generator.py', generator + ORACLE_MAIN)
        tree = {'op': 'mul', 'args': [{'var': 'x'}, {'var': 'y'}]}
        write('corpus.json', [{'oracle': tree, 'kind': 'explicit_oracle_control'}])
        write('domain.json', {'observations': [{'inputs': {'x': 0.25, 'y': 1.5}, 'value': 0.375}]})
        points = json.loads((root / 'points.json').read_text(encoding='utf-8'))
        points['points'] = [{'id': 'float' + str(i), 'coordinates': pair} for i, pair in enumerate(
            [('0.125', '2.5'), ('-1.25', '0.4'), ('3.75', '-0.2')])]
        write('points.json', points)
        plan = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
        plan['generator_code_paths'] = ['generator.py']
        write('jump-generation.json', plan)
        contract['description'] = 'Explicit synthetic numeric control; no scientific-discovery claim'
        contract['advisor_policy'].pop('confirmation')
        contract['advisor_policy']['context']['decision']['scope'] = {'domain': 'synthetic numerical fit only'}
        for binding in contract['bindings']:
            binding['sha256'] = file_sha(root / binding['path'])
        protocol = json.loads((root / 'protocol.json').read_text(encoding='utf-8'))
        protocol.update({role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')})
        protocol['numeric_protocol'] = 'Synthetic floating-point oracle control; absolute tolerance 1e-12'
        write('protocol.json', protocol)
        ref = {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')}
        next(b for b in contract['bindings'] if b['path'] == 'protocol.json').update(ref)
        for route in contract['advisor_policy']['routes']:
            route['manifest']['protocol'] = ref
        write('contract.json', contract)
        store = ProjectStore(root)
        store.initialize(contract)
        return root, store

    def test_explicit_generation_rebinds_admission_after_baseline_and_three_receipts(self):
        root, store = self.build()
        self.assertEqual(drive(store, max_steps=1)['status'], 'STEP_LIMIT')
        self.assertEqual([r['run_id'] for r in store.snapshot()['receipts']], ['baseline'])
        generated = jump.generate(root, 3)
        self.assertEqual(generated['status'], 'JUMP_PROPOSED')
        started = structure._find(store, 'JUMP', generated['id'])
        original = structure._find(store, 'REQUEST', started['request_id'])
        proposal = structure._find(store, 'PROPOSAL', generated['proposal_ids'][0])
        admission = structure._find(store, 'REQUEST', proposal['request_id'])
        self.assertNotIn('search_allocation', original)
        self.assertNotEqual(admission['id'], original['id'])
        self.assertEqual(len(original['receipts']), 1)
        self.assertEqual(admission['receipts'], [r['sha256'] for r in store.snapshot()['receipts']])
        self.assertEqual(len(admission['receipts']), 4)
        self.assertEqual(generated['agent_context']['scientific_support'], 'UNKNOWN')
        before = store.snapshot()
        self.assertEqual(jump.generate(root, 3)['id'], generated['id'])
        self.assertEqual(store.snapshot(), before)

    def test_owned_float_feedback_stays_historical_and_does_not_claim_confirmation(self):
        root, store = self.build()
        prepared = drive(store, max_steps=10, prepare_only=True)
        self.assertEqual(prepared['status'], 'MODEL_REQUEST_READY', prepared.get('reason'))
        first = json.loads(blob(root, prepared['request']))
        result = drive(store, max_steps=10)
        self.assertEqual(result['status'], 'GOAL_PREDICATES_MET_CONFIRMATION_UNDECLARED', result.get('reason'))
        with store._db(True) as db:
            events = [json.loads(row['body']) for row in db.execute('SELECT body FROM events ORDER BY id')]
        requests = [event for event in events if event.get('kind') == 'AUTONOMY_MODEL_REQUESTED']
        self.assertEqual([event['run_id'] for event in requests], ['repair1', 'repair2'])
        second = json.loads(blob(root, requests[1]['request']))
        check1 = json.loads((root / 'out/check1.json').read_text(encoding='utf-8'))
        feedback = next(item for item in second['evidence_excerpts']['original_outputs'] if item['path'] == 'out/check1.json')
        self.assertEqual(json.loads(feedback['text']), check1)
        self.assertEqual(check1['verdict'], 'FAIL')
        self.assertEqual(json.loads((root / 'out/check2.json').read_text(encoding='utf-8'))['verdict'], 'PASS')
        self.assertEqual(second['jump_packet']['id'], first['jump_packet']['id'])
        self.assertEqual(second['jump_packet']['evidence_scope'], 'HISTORICAL')
        self.assertFalse(second['jump_packet']['admission_authorized'])
        before = store.snapshot()
        self.assertEqual(len(before['receipts']), 10)
        again = drive(store, max_steps=10)
        self.assertEqual(again['status'], result['status'])
        self.assertEqual(again['executed'], [])
        self.assertEqual(store.snapshot()['runs'], before['runs'])
        self.assertEqual(store.snapshot()['receipts'], before['receipts'])
        self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)


if __name__ == '__main__':
    unittest.main()
