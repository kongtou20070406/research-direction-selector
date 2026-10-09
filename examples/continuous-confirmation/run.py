"""Owned finite-float confirmation; deterministic fixture, never an LLM trial."""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, canonical, file_sha, require
from rds_domain_confirmation import inspect_confirmation

CANDIDATE = '''import hashlib,json,pathlib
p=pathlib.Path
inputs=json.loads(p('features.json').read_text())
config=json.loads(p('candidate-config.json').read_text())
payload={'schema':1,'status':config['status'],'inputs_sha256':hashlib.sha256(p('features.json').read_bytes()).hexdigest(),'row_ids':[r['id'] for r in inputs['rows']]}
if config['status']=='candidate': payload['expression']=config['expression']
p('out/candidate.json').write_text(json.dumps(payload))
'''
EVALUATOR = '''import hashlib,json,pathlib,sqlite3
from continuous_checker import check_output
p=pathlib.Path
sha=lambda name:hashlib.sha256(p(name).read_bytes()).hexdigest()
read=lambda name:json.loads(p(name).read_text())
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt=json.loads(db.execute("SELECT body FROM receipts WHERE run_id='candidate'").fetchone()[0])
try:
    replay=check_output(read('claim.json'),read('features.json'),read('labels.json'),read('out/candidate.json'),sha('features.json'))
except ValueError as exc:
    replay={'status':'UNKNOWN','reason':str(exc)}
answer={'candidate_receipt_sha256':receipt['sha256'],'claim_sha256':sha('claim.json'),'evaluator_sha256':sha('evaluator.py'),'inputs_sha256':sha('features.json'),'labels_sha256':sha('labels.json'),'verdict':replay['status']}
p('out/confirmation.json').write_text(json.dumps(answer,allow_nan=False))
'''


def write(root, name, value):
    (root / name).write_text(value if isinstance(value, str) else canonical(value), encoding='utf-8')


def build(root, mode='correct'):
    root = Path(root).resolve()
    require(not root.exists() and not root.is_relative_to(REPO), 'Use a new absolute sibling workspace')
    root.mkdir(parents=True)
    (root / 'out').mkdir()
    for name, source in [('candidate.py', CANDIDATE), ('evaluator.py', EVALUATOR),
                         ('unused_provider.py', 'raise RuntimeError("No provider call is part of this fixture")\n')]:
        write(root, name, source)
    shutil.copyfile(REPO / 'scripts/rds_continuous_confirmation.py', root / 'continuous_checker.py')
    features = {'schema': 1, 'variables': ['x0'], 'rows': [
        {'id': 'r' + str(i), 'values': [x]} for i, x in enumerate([.1, .4, .8, 1.2])]}
    labels = {'schema': 1, 'rows': [{'id': r['id'], 'target': math.sin(r['values'][0])} for r in features['rows']]}
    write(root, 'features.json', features)
    write(root, 'labels.json', labels)
    write(root, 'claim.json', {'schema': 1, 'kind': 'numerical_expression_evaluation', 'max_nrmse': 1e-12, 'min_r2': .999999999999})
    expressions = {'correct': 'sin(x0)', 'wrong': '0', 'undefined': '1/(x0-x0)', 'abstain': None}
    expression = expressions[mode]
    write(root, 'candidate-config.json', {'status': 'abstain' if expression is None else 'candidate', 'expression': expression})
    files = [('candidate.py', 'code'), ('unused_provider.py', 'code'), ('candidate-config.json', 'config'),
             ('claim.json', 'config'), ('features.json', 'data'), ('labels.json', 'data'),
             ('evaluator.py', 'evaluator'), ('continuous_checker.py', 'evaluator')]
    bindings = [{'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in files]
    protocol = {role + '_sha256': ProjectStore._role_sha({'bindings': bindings}, role) for role in ('code', 'config', 'data')}
    protocol.update(data_split='public-synthetic-finite-fit', init='fresh', seed=0, checkpoint='none',
                    schedule='one candidate then one final scorer', sample_work={'rows': 4}, numeric_protocol='bounded Python float replay')
    write(root, 'protocol.json', protocol)
    ref = {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')}
    bindings.append({'role': 'protocol', **ref})
    nodes, routes = [], []
    for rid, script in [('candidate', 'candidate.py'), ('confirmation', 'evaluator.py')]:
        argv = [sys.executable, '-B', script]
        routes.append({'candidate': rid, 'manifest': {'schema': 1, 'id': rid, 'arm': 'tool', 'control_id': None,
            'argv': argv, 'protocol': ref, 'outpaths': ['out/' + rid + '.json'],
            'timeout_seconds': 10, 'resource_estimates': {'wall_seconds': 10}}})
        action = {'id': rid, 'kind': 'OBLIGATION_CHECK', 'target': 'finite-fit', 'description': 'Execute finite numeric control',
            'claim': 'Public synthetic finite fit only', 'required_observables': ['original output and independent replay'],
            'outcomes': [{'observation': 'verified', 'next_decision': 'retain finite fit'},
                         {'observation': 'counterexample', 'next_decision': 'retain failed final check'},
                         {'observation': 'unresolved', 'next_decision': 'retain unknown'}]}
        conditions = [] if rid == 'candidate' else [{'fact': 'run.candidate.succeeded', 'op': 'eq', 'value': True}]
        nodes.append({'id': rid, 'sources': ['explicit public control'],
                      'executable': {'decisions': ['next'], 'preconditions': conditions, 'action': action}})
    bound = lambda path: {'path': path, 'sha256': file_sha(root / path)}
    policy = {'schema': 1, 'context': {'decision': {'id': 'next', 'goal_revision': 'finite-continuous-v1',
        'scope': {'domain': 'Four public synthetic rows; no held-out or discovery claim'},
        'goal_conditions': [{'fact': 'numeric.verdict', 'op': 'eq', 'value': 'PASS'}]}},
        'graph': {'nodes': nodes, 'edges': []}, 'routes': routes,
        'observations': [{'fact': 'numeric.verdict', 'run_id': 'confirmation', 'path': 'out/confirmation.json', 'selector': {'pointer': '/verdict'}}],
        'autonomy': {'schema': 1, 'max_steps': 2, 'repair_slots': [], 'controller_wall_seconds': 30,
            'provider': {'kind': 'fixture', 'argv': [sys.executable, '-B', 'unused_provider.py'],
                'executable_sha256': file_sha(sys.executable), 'model': None, 'effort': None, 'service_tier': None}},
        'confirmation': {'schema': 1, 'domain': 'continuous', 'candidate_runs': ['candidate'], 'confirmation_runs': ['confirmation'],
            'scope': 'Finite numeric fit only; public labels, no isolation or generalization claim',
            'claim': bound('claim.json'), 'evaluator': bound('evaluator.py'), 'data': [bound('features.json'), bound('labels.json')],
            'rules': {'kind': 'numerical_expression_evaluation', 'candidate_output': 'out/candidate.json', 'confirmation_output': 'out/confirmation.json'}}}
    contract = {'schema': 1, 'description': 'Public deterministic finite numeric confirmation control', 'bindings': bindings,
        'allowed_commands': [r['manifest']['argv'] for r in routes], 'output_roots': ['out'],
        'budget': {'wall_seconds': 90}, 'advisor_policy': policy}
    write(root, 'contract.json', contract)
    return root, contract


def run(root, mode='correct'):
    root, contract = build(root, mode)
    trace = []
    for label, args in [('init', ['project', 'init', '--contract', str(root / 'contract.json')]),
                        ('drive', ['project', 'drive', '--max-steps', '3']),
                        ('repeat', ['project', 'drive', '--max-steps', '3'])]:
        p = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root), *args],
                           cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=60)
        trace.append({'step': label, 'argv': args, 'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        write(root, 'out/trajectory.json', trace)
        require(p.returncode == 0, 'Inspect original trajectory')
    state = ProjectStore(root).snapshot()
    result = {'mode': mode, 'controller_status': json.loads(trace[1]['stdout'])['status'],
              'repeat_executed': json.loads(trace[2]['stdout'])['executed'], 'receipts': len(state['receipts']),
              'confirmation': inspect_confirmation(ProjectStore(root), contract), 'model_calls': 0,
              'scientific_gain': 'UNMEASURED', 'isolation': 'NOT_CLAIMED'}
    write(root, 'out/summary.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--mode', choices=['correct', 'wrong', 'undefined', 'abstain'], default='correct')
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, args.mode), indent=2))
