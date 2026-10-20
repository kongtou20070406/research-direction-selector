"""Feynman devset Compass sweep: 5 equations x budgets B in {8, 16, 32}.

Each campaign declares the devset rows as candidates (exact rational text), an
experiment that evaluates the equation's law in a subprocess, and an empty
hypothesis list. Scored per the pre-registered rule:

    claim_exact = drive status == GOAL_IDENTIFIED and the reported law replays
                  with 0 counterexamples over ALL devset rows under the
    devset tolerances (model.replay). A PARTIAL_FIT / best-effort law is
    never claim_exact.

    experiments = drive steps with action in {EXPERIMENT, PROBE, CONFIRM,
                  HOLDOUT}; a declaration failure is recorded and the sweep
                  moves on.

    python -B benchmark/newtonbench/devset_run.py [out.json] [equation]
"""
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(HERE)]
import devset
import rds_compass_model as model
from rds_compass_declaration import initialize
from rds_compass_drive import drive

BUDGETS = (8, 16, 32)
EXPERIMENTS = ('EXPERIMENT', 'PROBE', 'CONFIRM', 'HOLDOUT')
EXPERIMENT = '''import argparse, json, math, pathlib
from fractions import Fraction
p = argparse.ArgumentParser()
p.add_argument('--output', required=True)
task = json.loads(pathlib.Path('task.json').read_text(encoding='utf-8'))
for name in task['variables']:
    p.add_argument('--' + name, required=True)
a = p.parse_args()
env = {name: Fraction(getattr(a, name)) for name in task['variables']}
env.update(exp=math.exp, sqrt=math.sqrt, log=math.log, sin=math.sin, cos=math.cos, pi=math.pi)
value = eval(task['law'], {'__builtins__': {}}, env)
text = str(value) if isinstance(value, Fraction) else repr(float(value))
out = pathlib.Path(a.output)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'outputs': {'y': text}}), encoding='utf-8')
'''


def campaign(name, budget, base):
    """Write one campaign root; return (root, spec)."""
    spec = devset.load(name)
    variables = spec['grammar']['variables']
    rows = spec['rows']
    root = base / name / ('B' + str(budget))
    root.mkdir(parents=True)
    (root / 'experiment.py').write_text(EXPERIMENT, encoding='utf-8')
    (root / 'task.json').write_text(json.dumps({'law': spec['truth']['law'],
                                                'variables': variables}), encoding='utf-8')
    inputs = {}
    for var in variables:
        values = [model.exact(row['inputs'][var]) for row in rows]
        inputs[var] = {'min': model.text(min(values)), 'max': model.text(max(values)),
                       'integer': all(v.denominator == 1 for v in values)}
    argv = ['{python}', '-B', 'experiment.py', '--output', '{output}']
    for var in variables:
        argv += ['--' + var, '{' + var + '}']
    declaration = {
        'schema': 'rds-compass-declaration-v1',
        'goal': {'kind': 'IDENTIFY_LAW',
                 'statement': f'Identify the law behind {name} over the declared candidate points',
                 'observable': spec['truth']['observable']},
        'inputs': inputs,
        'observables': {spec['truth']['observable']:
                        {'tolerance': spec['tolerances'][spec['truth']['observable']]}},
        'experiment': {'argv': argv,
                       'files': {'code': ['experiment.py'], 'data': ['task.json'],
                                 'evaluator': ['experiment.py']},
                       'timeout_seconds': 20, 'wall_seconds': 25},
        'candidates': [{'id': row['id'], 'inputs': dict(row['inputs'])} for row in rows],
        'hypotheses': [],
        'grammar': spec['grammar'],
        'budget': {'wall_seconds': 900, 'experiments': budget, 'max_experiments': budget},
    }
    (root / 'compass.json').write_text(json.dumps(declaration, indent=1), encoding='utf-8')
    return root, spec


def counterexamples(expr, spec):
    """Replay the reported law over all devset rows; count counterexamples."""
    observable = spec['truth']['observable']
    hyp = model.compile_hypothesis({'id': 'h', 'predict': {observable: expr}},
                                   spec['grammar']['variables'], [observable], goal=observable)
    observations = [{'id': row['id'],
                     'inputs': {k: model.exact(v) for k, v in row['inputs'].items()},
                     'outputs': {k: model.exact(v) for k, v in row['outputs'].items()}}
                    for row in spec['rows']]
    return len(model.replay(hyp, observations,
                            {observable: model.tolerance(spec['tolerances'][observable])})['counterexamples'])


def run_one(name, budget, base):
    root, spec = campaign(name, budget, base)
    initialize(root, root / 'compass.json')
    result = drive(root, publish=False)
    steps = result.get('steps', [])
    verification = result.get('verification') or {}
    expr = (verification.get('predict') or {}).get(spec['truth']['observable'])
    bad = counterexamples(expr, spec) if expr else None
    return {'equation': name, 'B': budget, 'status': result['status'],
            'claim': verification.get('claim'), 'law': expr, 'counterexamples': bad,
            'claim_exact': result['status'] == 'GOAL_IDENTIFIED' and bad == 0,
            'experiments': sum(1 for s in steps if s.get('action') in EXPERIMENTS)}


def main(out_path, only=None):
    base = Path(tempfile.mkdtemp(prefix='devset-run-'))
    runs = []
    start = time.monotonic()
    for name in devset.list_equations():
        if only and name != only:
            continue
        for budget in BUDGETS:
            t0 = time.monotonic()
            try:
                row = run_one(name, budget, base)
            except Exception as exc:  # one failed declaration must not sink the sweep
                row = {'equation': name, 'B': budget, 'claim_exact': False,
                       'error': f'{type(exc).__name__}: {exc}'}
            row['seconds'] = round(time.monotonic() - t0, 1)
            runs.append(row)
            print(json.dumps(row), flush=True)
    out = {'schema': 1, 'budgets': list(BUDGETS), 'campaign_root': str(base),
           'claim_exact_rule': 'status == GOAL_IDENTIFIED and 0 counterexamples on full devset replay',
           'runs': runs, 'elapsed_seconds': round(time.monotonic() - start, 1)}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n', encoding='utf-8')
    print('wrote ' + str(out_path), flush=True)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else HERE / 'results' / 'devset-run.json',
         sys.argv[2] if len(sys.argv) > 2 else None)
