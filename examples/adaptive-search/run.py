"""Public CLI wiring trajectory; scripted hypotheses, no model or scientific gain."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, file_sha, digest
from rds_tms_store import maintain
from rds_search_allocation import POLICY_PATH, STRATEGY

spec = importlib.util.spec_from_file_location('structure_example', REPO / 'examples/problem-structure/run.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def policy():
    return {'schema': 1, 'strategy': STRATEGY, 'baseline_proposal_id': 'baseline',
            'metric': {'name': 'finite-mse', 'pointer': '/mse', 'unit': 'squared-output-units',
                       'direction': 'min', 'min_improvement': '1'},
            'slots': {'explore': 2, 'evidence': 1, 'refine': 2}, 'min_repeats': 2}


def prepare(root, *, enabled=True, budget=240, search_policy=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    for name in ('candidate.py', 'evaluator.py'):
        shutil.copyfile(Path(__file__).parent / name, root / name)
    (root / 'data.json').write_text(json.dumps({'inputs': [0, 1, 2, 3]}), encoding='utf-8')
    (root / 'config.json').write_text('{}', encoding='utf-8')
    paths = [('candidate.py', 'code'), ('evaluator.py', 'evaluator'), ('data.json', 'data'), ('config.json', 'config')]
    if enabled:
        (root / POLICY_PATH).write_text(json.dumps(search_policy or policy()), encoding='utf-8')
        paths.append((POLICY_PATH, 'config'))
    commands = []
    for ident in ('baseline', 'p1', 'p2', 'p3', 'p4', 'alternative', 'alias', 'unknown'):
        for mode in ('zero', 'linear', 'quadratic'):
            commands.append([sys.executable, '-B', 'candidate.py', mode, f'out/{ident}.json'])
        for mode in ('ok', 'missing'):
            commands.append([sys.executable, '-B', 'evaluator.py', ident, f'out/{ident}.json', f'out/{ident}-verdict.json', mode])
    contract = {'schema': 1, 'description': 'Predict the frozen finite outputs; all must pass the independent evaluator',
                'bindings': [{'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in paths],
                'allowed_commands': commands, 'output_roots': ['out'], 'budget': {'wall_seconds': budget}}
    protocol = {'code_sha256': ProjectStore._role_sha(contract, 'code'),
                'config_sha256': ProjectStore._role_sha(contract, 'config'),
                'data_sha256': ProjectStore._role_sha(contract, 'data'), 'data_split': 'public-development',
                'init': 'none', 'seed': 0, 'checkpoint': 'none', 'schedule': 'finite-observation',
                'sample_work': {'points': 4}, 'numeric_protocol': 'integer prediction, exact finite squared errors'}
    (root / 'protocol.json').write_text(json.dumps(protocol), encoding='utf-8')
    contract['bindings'].append({'path': 'protocol.json', 'role': 'protocol', 'sha256': file_sha(root / 'protocol.json')})
    (root / 'contract.json').write_text(json.dumps(contract), encoding='utf-8')
    maintain(root, initial={'schema': 1, 'nodes': [
        {'id': n, 'source': 'synthetic finite task', 'status': 'UNKNOWN'} for n in ('observations', 'old-model', 'goal')],
        'hyperedges': [{'id': 'old-decomposition', 'premises': ['observations', 'old-model'],
                       'conclusion': 'goal', 'status': 'PROPOSED', 'source': 'initial finite task'}], 'goals': ['goal']})
    ProjectStore(root).initialize(contract)
    return root


def proposal(root, task, ident='baseline', mode='zero', kind='explore', verifier_mode='ok', timeout=8):
    result = base.proposal(root, task, ident, mode, timeout=timeout)
    result['discriminator'] = {'schema': 1, 'hypothesis_id': mode,
        'conditions': [{'path': b['path'], 'sha256': b['sha256']} for b in ProjectStore(root).snapshot()['contract']['bindings']
                       if b['role'] in ('config', 'data')],
        'measurement': {'name': 'finite-mse', 'path': f'out/{ident}-verdict.json', 'pointer': '/mse'},
        'proposal': {'op': 'lt', 'value': 20}, 'rival': {'op': 'gte', 'value': 20}}
    result['prediction'] = {'observable': ident + '-concept', 'if_proposal': 'finite MSE below 20', 'if_rival': 'finite MSE at least 20'}
    result['experiment']['runs'][1]['argv'] = [sys.executable, '-B', 'evaluator.py', ident, f'out/{ident}.json', f'out/{ident}-verdict.json', verifier_mode]
    allocation = task.get('search_allocation')
    if allocation:
        slot = next(s for s in allocation['slots'] if s['kind'] == kind)
        result['search'] = {'slot': slot['slot']}
        if slot['trigger']:
            result['trigger'] = deepcopy(slot['trigger'])
    return result


def cli(root, *args):
    start = time.monotonic()
    result = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root), *args],
                            capture_output=True, text=True, encoding='utf-8', timeout=90)
    with (root / 'cli-trace.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'argv': list(args), 'returncode': result.returncode, 'stdout': result.stdout,
                                 'stderr': result.stderr, 'wall_seconds': time.monotonic() - start}) + '\n')
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def run(workspace):
    root = prepare(workspace)
    trajectory = []
    for ident, mode, kind in [('baseline', 'zero', 'explore'), ('p1', 'linear', 'explore'),
                               ('p2', 'linear', 'evidence'), ('alternative', 'quadratic', 'refine')]:
        task = cli(root, 'structure', 'request')['tasks'][0]
        value = proposal(root, task, ident, mode, kind)
        path = root / (ident + '-proposal.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        cli(root, 'structure', 'propose', '--proposal', str(path))
        decision = cli(root, 'structure', 'next')
        observed = cli(root, 'structure', 'advance', '--id', decision['selected'])
        trajectory.append({'request_id': task['id'], 'allocation': task['search_allocation'], 'proposal_id': ident,
                           'decision': decision, 'feedback': observed})
    state = ProjectStore(root).snapshot()
    report = {'scope': 'SCRIPTED_FINITE_DEVELOPMENT_TRAJECTORY', 'model': None, 'trajectory': trajectory,
              'attempts': len(state['receipts']), 'budget': state['budget'],
              'goal_status': trajectory[-1]['feedback']['goal_status'], 'scientific_benefit': 'UNMEASURED',
              'matched_model_comparison': 'NOT_RUN'}
    (root / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    result = run(parser.parse_args().workspace)
    print(json.dumps({k: result[k] for k in ('scope', 'attempts', 'goal_status', 'scientific_benefit')}))
