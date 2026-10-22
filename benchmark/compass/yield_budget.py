"""Local budget probe: a 64-input domain that the 20-experiment budget cannot exhaust.

The declared domain is larger than the budget, so the drive must stop without
enumerating it. Three confirmation caps are compared on the same sealed law.

    python -B benchmark/compass/yield_budget.py benchmark/compass/results/yield-budget.json
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

LAW, XS, TOL = 'x*x+1', list(range(0, 64)), 0
VARIANTS = [
    ('default', {}),
    ('cap5', {'max_confirmations': 5}),
    ('cap10', {'max_confirmations': 10}),
]
ENV = {'popcount': lambda n: bin(abs(n)).count('1'), 'sin': math.sin, 'cos': math.cos,
       'exp': math.exp, 'log': math.log, 'sqrt': math.sqrt}


def correct(claim, law, xs):
    if not claim:
        return False
    try:
        return all(abs(eval(claim, {'__builtins__': {}}, {**ENV, 'x': x})
                       - eval(law, {'__builtins__': {}}, {**ENV, 'x': x})) <= 1e-9 for x in xs)
    except (ValueError, ZeroDivisionError, TypeError):
        return False


def run(verification):
    root = Path(tempfile.mkdtemp()) / 'c'
    path = fx.law_campaign(root, LAW, XS, hypotheses=[('lin', 'x+1')])
    value = json.loads(path.read_text(encoding='utf-8'))
    value['observables']['y']['tolerance'] = {'abs': TOL}
    value['budget'] = {'wall_seconds': 900, 'experiments': 20}
    if verification:
        value['verification'] = verification
    fx.write(path, value)
    initialize(root, path)
    result = drive(root, publish=False)
    state = compass.fold(cstore.store_for(root))
    claim = (result.get('verification') or {}).get('predict', {}).get('y')
    return {'status': result['status'], 'claim': claim, 'correct': correct(claim, LAW, XS),
            'cost': compass.report(cstore.store_for(root))['cost']}


def main(output):
    rows = []
    for variant, verification in VARIANTS:
        row = {'variant': variant, **run(verification)}
        rows.append(row)
        print(json.dumps(row))
    Path(output).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'yield-budget.json')
