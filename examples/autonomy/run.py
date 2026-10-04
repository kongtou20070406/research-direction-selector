"""Three-domain CLI trajectories in one owning ledger; fixture or real Codex.

Use a new sibling workspace. Fixture output tests plumbing, never model ability.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, canonical, file_sha
from rds_autonomy_worker import output_paths


def write(root, name, value):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value if isinstance(value, str) else canonical(value), encoding='utf-8')


def sources(domain):
    if domain == 'mathematics':
        preamble = "import json,pathlib,sys\nfrom fractions import Fraction\nsys.path.insert(0,'engine')\nfrom rds_verify import verify\n"
        old = preamble + "claim=json.loads(pathlib.Path('claim.json').read_text())\nclaim['point']=['0']\nanswer=verify(claim)\n"
        new = preamble + "claim=json.loads(pathlib.Path('claim.json').read_text())\na=Fraction(claim['model']['matrix'][0][0]); b=Fraction(claim['model']['bias'][0])\nif abs(a)>=1: raise ValueError('Unsupported noncontractive model')\nclaim['point']=[str(b/(1-a))]\nanswer=verify(claim)\n"
        tail = "pathlib.Path(sys.argv[1]).write_text(json.dumps(answer),encoding='utf-8')\n"
        claim = {'schema': 1, 'kind': 'affine_dynamics', 'model': {'matrix': [['1/2']], 'bias': ['1/2']},
                 'threshold': '1', 'point': ['1']}
        data, kind = [1], 'exact_certificate'
    elif domain == 'algorithms':
        preamble = "import hashlib,json,pathlib,sys\nxs=json.loads(pathlib.Path('data.json').read_text())\n"
        old = preamble + "values=[sum(v*v for v in row)+1 for row in xs]\n"
        new = preamble + "values=[]\nfor row in xs:\n    accumulator=0\n    for v in row: accumulator+=v*v\n    values.append(accumulator)\n"
        tail = "answer={'values':values,'inputs_sha256':hashlib.sha256(pathlib.Path('data.json').read_bytes()).hexdigest()}\npathlib.Path(sys.argv[1]).write_text(json.dumps(answer),encoding='utf-8')\n"
        claim = {'schema': 1, 'kind': 'integer_sum_squares'}
        data, kind = [[], [1, 2, 3], [-7, 0, 11], [2 ** 40, -(2 ** 40)]], 'integer_sum_squares'
    else:
        old = "import json,pathlib,sys\nanswer={'weight':[0.],'bias':0.}\n"
        new = """import json,pathlib,sys,torch
torch.set_num_threads(1); torch.manual_seed(5)
d=json.loads(pathlib.Path('data.json').read_text())
x=torch.tensor(d['x'],dtype=torch.float32,device='cpu'); y=torch.tensor(d['y'],dtype=torch.float32,device='cpu').reshape(-1,1)
model=torch.nn.Linear(1,1,device='cpu'); optimizer=torch.optim.SGD(model.parameters(),lr=.1)
for step in range(150):
    optimizer.zero_grad(); loss=torch.mean((model(x)-y)**2); loss.backward(); optimizer.step()
answer={'weight':model.weight.detach().reshape(-1).tolist(),'bias':model.bias.detach().item()}
"""
        tail = "pathlib.Path(sys.argv[1]).write_text(json.dumps(answer),encoding='utf-8')\n"
        claim = {'schema': 1, 'kind': 'torch_linear_regression', 'input_dimension': 1, 'max_mse': .001}
        data, kind = {'x': [[-1.], [0.], [1.], [2.]], 'y': [-1., 1., 3., 5.]}, 'torch_linear_regression'
    return old + tail, new + tail, claim, data, kind


EVALUATOR = """import hashlib,json,pathlib,sqlite3,sys
sys.path.insert(0,'engine')
from rds_verify import verify
sha=lambda p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt=json.loads(db.execute("SELECT body FROM receipts WHERE run_id='candidate'").fetchone()[0])
claim=json.loads(pathlib.Path('claim.json').read_text()); output=json.loads(pathlib.Path('outputs/candidate.json').read_text())
if claim['kind']=='affine_dynamics':
    from rds_verify import checked_result
    verdict=checked_result(claim,output.get('certificate',output))['status']
elif claim['kind']=='integer_sum_squares':
    data=json.loads(pathlib.Path('data.json').read_text())
    oracle=[sum(v*v for v in row) for row in data]
    verdict='PASS' if output['values']==oracle and output['inputs_sha256']==sha('data.json') else 'FAIL'
else:
    import torch
    torch.set_num_threads(1)
    data=json.loads(pathlib.Path('data.json').read_text())
    x=torch.tensor(data['x'],dtype=torch.float32,device='cpu'); y=torch.tensor(data['y'],dtype=torch.float32,device='cpu')
    pred=x@torch.tensor(output['weight'],dtype=torch.float32,device='cpu')+output['bias']
    verdict='PASS' if torch.mean((pred-y)**2).item()<=claim['max_mse'] else 'FAIL'
answer={'candidate_receipt_sha256':receipt['sha256'],'claim_sha256':sha('claim.json'),
        'evaluator_sha256':sha('evaluator.py'),'verdict':verdict}
pathlib.Path('outputs/confirmation.json').write_text(json.dumps(answer),encoding='utf-8')
"""


def build(root, domain, provider='fixture', codex_path=None, python=sys.executable):
    root = Path(root).resolve()
    if root == REPO or root.is_relative_to(REPO) or (root.exists() and any(root.iterdir())):
        raise ValueError('Choose a new empty sibling workspace; never overwrite research state')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'outputs').mkdir()
    # Freeze all dynamically imported engine sources; no Git/user settings copied.
    for original in sorted((REPO / 'scripts').glob('*.py')):
        target = root / 'engine' / original.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(original, target)
    old, new, claim, data, kind = sources(domain)
    write(root, 'candidate.py', old)
    write(root, 'claim.json', claim)
    write(root, 'data.json', data)
    write(root, 'config.json', {'domain': domain, 'scope': 'finite public engineering trajectory'})
    write(root, 'evaluator.py', EVALUATOR)
    # This explicit fixture is not a model call; real Codex is an opt-in provider.
    write(root, 'provider.py', "import json,sys\nrequest=json.loads(sys.stdin.read().split('\\n',1)[1])\n" +
          'print(json.dumps({"status":"proposed","source":' + repr(new) +
          ',"policy_json":json.dumps(request["policy"]),"reason":"Replace the failing computation with a different bounded algorithm and retain independent confirmation"}))\n')
    if domain == 'algorithms':
        baseline = "import hashlib,json,pathlib\nxs=json.loads(pathlib.Path('data.json').read_text())\nvalues=[sum(map(lambda x:x*x,row)) for row in xs]\nanswer={'values':values,'inputs_sha256':hashlib.sha256(pathlib.Path('data.json').read_bytes()).hexdigest()}\npathlib.Path('outputs/baseline.json').write_text(json.dumps(answer),encoding='utf-8')\n"
        write(root, 'baseline.py', baseline)
    bindings = [{'path': p.relative_to(root).as_posix(), 'role': 'code', 'sha256': file_sha(p)}
                for p in sorted((root / 'engine').glob('*.py'))]
    files = [('candidate.py', 'code'), ('provider.py', 'code'), ('evaluator.py', 'evaluator'),
             ('claim.json', 'config'), ('config.json', 'config'), ('data.json', 'data')]
    if domain == 'algorithms':
        files.append(('baseline.py', 'code'))
    bindings += [{'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in files]
    protocol = {role + '_sha256': ProjectStore._role_sha({'bindings': bindings}, role) for role in ('code', 'config', 'data')}
    protocol.update(data_split='public-finite-regression', init='fresh', seed=5, checkpoint='same-ledger',
                    schedule='failed-pilot/model-repair/candidate/confirmation', sample_work={'domain': domain}, numeric_protocol='declared exact integers or CPU FP32')
    write(root, 'protocol.json', protocol)
    bindings.append({'path': 'protocol.json', 'role': 'protocol', 'sha256': file_sha(root / 'protocol.json')})
    specs = [('failed-pilot', [python, '-B', 'candidate.py', 'outputs/failed-pilot.json'], ['outputs/failed-pilot.json'], []),
             ('repair', [python, '-B', 'engine/rds_autonomy_worker.py', '--run', 'repair'], output_paths('outputs/repair.json'),
              [{'fact': 'autonomy.repair.ready', 'op': 'eq', 'value': True}]),
             ('candidate', [python, '-B', 'candidate.py', 'outputs/candidate.json'], ['outputs/candidate.json'],
              [{'fact': 'autonomy.repair.adopted', 'op': 'eq', 'value': True}])]
    if domain == 'algorithms':
        specs.append(('baseline', [python, '-B', 'baseline.py'], ['outputs/baseline.json'],
                      [{'fact': 'run.candidate.succeeded', 'op': 'eq', 'value': True}]))
    dependency = 'baseline' if domain == 'algorithms' else 'candidate'
    specs.append(('confirmation', [python, '-B', 'evaluator.py'], ['outputs/confirmation.json'],
                  [{'fact': 'run.' + dependency + '.succeeded', 'op': 'eq', 'value': True}]))
    routes, nodes = [], []
    for rid, argv, outputs, preconditions in specs:
        duration = 180 if rid == 'repair' and provider == 'codex_exec' else 45 if domain == 'deep_learning' else 5
        manifest = {'schema': 1, 'id': rid, 'arm': 'tool', 'control_id': None, 'argv': argv, 'outpaths': outputs,
                    'protocol': {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')},
                    'resource_estimates': {'wall_seconds': duration + 2}, 'timeout_seconds': duration}
        routes.append({'candidate': rid, 'manifest': manifest})
        nodes.append({'id': rid, 'sources': ['declared-public-domain-trajectory'], 'executable': {
            'decisions': ['domain-research'], 'preconditions': preconditions, 'action': {
                'id': rid, 'kind': 'PAIRED_TEST', 'target': domain, 'operation': 'measure-' + rid,
                'description': 'Execute bounded ' + rid + ' and retain original observations',
                'competing_explanations': ['candidate meets bounded evaluator', 'candidate fails or evidence unavailable'],
                'required_observables': ['run.' + rid + '.succeeded'],
                'outcomes': [{'observation': 'positive', 'next_decision': 'inspect independent confirmation'},
                             {'observation': 'negative', 'next_decision': 'change method preserving goal'}]}}})
    model = {'kind': 'fixture', 'argv': [python, '-B', 'provider.py'], 'executable_sha256': file_sha(Path(python)),
             'model': None, 'effort': None, 'service_tier': None}
    if provider == 'codex_exec':
        executable = Path(codex_path).resolve()
        model = {'kind': 'codex_exec', 'argv': [str(executable)], 'executable_sha256': file_sha(executable),
                 'model': 'gpt-6.1-sol', 'effort': 'high', 'service_tier': 'default'}
    rules = {'kind': kind, 'candidate_output': 'outputs/candidate.json', 'confirmation_output': 'outputs/confirmation.json'}
    if domain == 'algorithms':
        rules.update(baseline_run='baseline', baseline_output='outputs/baseline.json')
    policy = {'schema': 1, 'context': {'decision': {'id': 'domain-research', 'goal_revision': 'finite-v1',
              'scope': {'domain': domain}, 'goal_conditions': [{'fact': 'final.verdict', 'op': 'eq', 'value': 'PASS'}]}},
              'graph': {'nodes': nodes, 'edges': []}, 'routes': routes,
              'observations': [{'fact': 'final.verdict', 'run_id': 'confirmation', 'path': 'outputs/confirmation.json', 'selector': {'pointer': '/verdict'}}],
              'autonomy': {'schema': 1, 'max_steps': 8, 'repair_slots': [{'run_id': 'repair', 'code_path': 'candidate.py',
                           'worker_path': 'engine/rds_autonomy_worker.py', 'response_path': 'outputs/repair.json'}], 'provider': model},
              'confirmation': {'schema': 1, 'domain': domain, 'candidate_runs': ['candidate'], 'confirmation_runs': ['confirmation'],
                  'scope': 'Public finite example; no scientific generalization',
                  'claim': {'path': 'claim.json', 'sha256': file_sha(root / 'claim.json')},
                  'evaluator': {'path': 'evaluator.py', 'sha256': file_sha(root / 'evaluator.py')},
                  'data': [{'path': 'data.json', 'sha256': file_sha(root / 'data.json')}], 'rules': rules}}
    contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [r['manifest']['argv'] for r in routes],
                'output_roots': ['outputs'], 'budget': {'wall_seconds': 480}, 'advisor_policy': policy,
                'stop_policy': {'schema': 1, 'wall_seconds': 600, 'progress': {'window_seconds': 60, 'min_bytes': 0}},
                'method_evolution': {'schema': 1, 'max_revisions': 2, 'code_paths': ['candidate.py']}}
    write(root, 'contract.json', contract)
    return contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--domain', choices=['mathematics', 'algorithms', 'deep_learning'], required=True)
    parser.add_argument('--provider', choices=['fixture', 'codex_exec'], default='fixture')
    parser.add_argument('--codex-path', help='Native installed codex executable; credentials/settings stay in existing host stores')
    args = parser.parse_args()
    if args.provider == 'codex_exec' and not args.codex_path:
        parser.error('codex_exec requires --codex-path')
    root = Path(args.workspace).resolve()
    build(root, args.domain, args.provider, args.codex_path)
    for name, argv in [('init', ['project', 'init', '--contract', str(root / 'contract.json')]),
                       ('drive', ['project', 'drive', '--max-steps', '8']),
                       ('recover-drive', ['project', 'drive', '--max-steps', '8'])]:
        command = [sys.executable, '-B', str(root / 'engine/rds_cli.py'), '--root', str(root), *argv]
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=550)
        write(root, 'entry-' + name + '.json', {'argv': command, 'exit_code': completed.returncode,
                                              'stdout': completed.stdout, 'stderr': completed.stderr})
        if completed.returncode:
            raise SystemExit('Entry failed; inspect ' + str(root / ('entry-' + name + '.json')))
    with ProjectStore(root)._db(True) as db:
        runs = ProjectStore._runs(db)
        receipts = [ProjectStore._receipt(r) for r in db.execute('SELECT run_id,sha256,body FROM receipts')]
    summary = {'domain': args.domain, 'provider': args.provider, 'model_ability_measured': False,
               'drive': json.loads((root / 'entry-drive.json').read_text(encoding='utf-8'))['stdout'],
               'recovered_drive': json.loads((root / 'entry-recover-drive.json').read_text(encoding='utf-8'))['stdout'],
               'run_ids': [r['id'] for r in runs], 'attempt_ids': [r['attempt_id'] for r in runs],
               'receipts': [r['sha256'] for r in receipts], 'scientific_support': 'UNKNOWN'}
    write(root, 'trajectory-summary.json', summary)
    actual = json.loads(summary['recovered_drive'])
    expected = 'DOMAIN_CONFIRMATION_UNKNOWN' if args.domain == 'deep_learning' else 'GOAL_CONFIRMED'
    if actual['status'] != expected or len(runs) != (5 if args.domain == 'algorithms' else 4) or len(receipts) != len(runs):
        raise SystemExit('Trajectory has not met its bounded acceptance; inspect ' + str(root / 'trajectory-summary.json'))
    first = json.loads(summary['drive'])
    for completed in first['executed']:
        original = next(r for r in receipts if r['run_id'] == completed['run_id'])
        if original['attempt_id'] != completed['attempt_id'] or original['sha256'] != completed['receipt_sha256']:
            raise SystemExit('Recovery changed an original completed attempt')
    print(canonical({'workspace': str(root), 'domain': args.domain, 'provider': args.provider,
                     'runs': len(runs), 'receipts': len(receipts), 'evidence': 'trajectory-summary.json'}))


if __name__ == '__main__':
    main()
