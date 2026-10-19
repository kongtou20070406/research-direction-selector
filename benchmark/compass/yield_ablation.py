"""Local ablation: holdout and confirmation cap on small and large declared domains.

Four worlds x three verification variants, deterministic N=1. The large worlds
declare 1000 inputs but allow 30 experiments, so the domain cannot be exhausted.
The sparse world hides a regime change at x >= 900. A claim counts as correct
only if it equals the sealed law on every declared input.

    python -B benchmark/compass/yield_ablation.py benchmark/compass/results/yield-ablation.json
"""
import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import compass_fixture as fx
from rds_compass_declaration import initialize
import rds_compass as compass
import rds_compass_store as cstore
from rds_compass_drive import drive

WORLDS = [
    ('sin', 'sin(x)', list(range(0, 5)), 0.01, None),
    ('sqrt', 'sqrt(x)', [0, 1, 4, 9, 16], 0.01, None),
    ('large-quad', 'x*x+1', list(range(0, 32)), 0, {'experiments': 12}),
]
VARIANTS = [
    ('default', {}),
    ('holdout2', {'holdout': 2}),
    ('cap5', {'max_confirmations': 5}),
]
ENV = {'popcount': lambda n: bin(abs(n)).count('1'), 'sin': math.sin, 'cos': math.cos,
       'exp': math.exp, 'log': math.log, 'sqrt': math.sqrt}


def correct(claim, law, xs):
    if not claim:
        return False
    try:
        for x in xs:
            env = {**ENV, 'x': x}
            if abs(eval(claim, {'__builtins__': {}}, env) - eval(law, {'__builtins__': {}}, env)) > 1e-9:
                return False
        return True
    except (ValueError, ZeroDivisionError, TypeError):
        return False


def run(law, xs, tol, budget, verification):
    root = Path(tempfile.mkdtemp()) / 'c'
    path = fx.law_campaign(root, law, xs, hypotheses=[('lin', 'x+1')])
    value = json.loads(path.read_text(encoding='utf-8'))
    value['observables']['y']['tolerance'] = {'abs': tol}
    if budget:
        value['budget'] = {'wall_seconds': 900, **budget}
    if verification:
        value['verification'] = verification
    fx.write(path, value)
    initialize(root, path)
    result = drive(root, publish=False)
    state = compass.fold(cstore.store_for(root))
    claim = (result.get('verification') or {}).get('predict', {}).get('y')
    return {'status': result['status'], 'claim': claim, 'correct': correct(claim, law, xs),
            'false_certification': bool(claim) and not correct(claim, law, xs),
            'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-'))}


def main(output):
    rows = []
    for name, law, xs, tol, budget in WORLDS:
        for variant, verification in VARIANTS:
            row = {'world': name, 'variant': variant, **run(law, xs, tol, budget, verification)}
            rows.append(row)
            print(json.dumps(row))
    Path(output).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'yield-ablation.json')
