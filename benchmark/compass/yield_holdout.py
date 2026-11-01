"""Local holdout probe: a sparse exception the confirmation points can miss.

One 32-input world whose law differs from the initial rival at x=30 only.
With spend_budget off, the drive buys two confirmation points and would
claim the rival; the holdout reserves x30/x31 and tests the claim on a
point the fit never saw.

    python -B benchmark/compass/yield_holdout.py benchmark/compass/results/yield-holdout.json
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

WORLD = ('sparse', 'x+1 if x != 14 else 1000', list(range(0, 16)), 0, {'experiments': 8})
VARIANTS = [
    ('economy', {'spend_budget': False, 'confirmations': 2}),
    ('holdout1', {'spend_budget': False, 'confirmations': 2, 'holdout': 1}),
    ('holdout2', {'spend_budget': False, 'confirmations': 2, 'holdout': 2}),
    ('holdout3', {'spend_budget': False, 'confirmations': 2, 'holdout': 3}),
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
            'holdout': [d['candidate'] for d in state['decisions'] if d['action'] == 'HOLDOUT'],
            'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-'))}


def main(output):
    name, law, xs, tol, budget = WORLD
    rows = []
    for variant, verification in VARIANTS:
        row = {'world': name, 'variant': variant, **run(law, xs, tol, budget, verification)}
        rows.append(row)
        print(json.dumps(row))
    Path(output).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'yield-holdout.json')
