"""Local yield probe: does the open-grammar escalation turn unsolved worlds into claims?

Runs each law twice on the same code: with the grammar left open (escalates to
sqrt/exp/log/sin/cos) and with an explicit arithmetic grammar (never escalates).
Deterministic, N=1; the numbers are mechanism evidence, not a research benchmark.

    python -B benchmark/compass/yield_probe.py benchmark/compass/results/yield-probe.json
"""
import json
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
    ('arith', 'x*x+1', list(range(-4, 5)), 0),
    ('sin', 'sin(x)', list(range(0, 5)), 0.01),
    ('sqrt', 'sqrt(x)', [0, 1, 4, 9, 16], 0.01),
    ('exp', 'exp(x/4)', list(range(0, 9)), 0.05),
    ('popcount', '2*popcount(x)+1', list(range(0, 17)), 0),
]


def run(law, xs, tol, explicit):
    root = Path(tempfile.mkdtemp()) / 'c'
    path = fx.law_campaign(root, law, xs, hypotheses=[('lin', 'x+1')])
    value = json.loads(path.read_text(encoding='utf-8'))
    value['observables']['y']['tolerance'] = {'abs': tol}
    if explicit:
        value['grammar'] = {'operators': ['add', 'sub', 'mul', 'div']}
    fx.write(path, value)
    initialize(root, path)
    result = drive(root, publish=False)
    state = compass.fold(cstore.store_for(root))
    return {'status': result['status'],
            'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
            'claim': (result.get('verification') or {}).get('predict', {}).get('y'),
            'observations': len(state['observations'])}


def main(output):
    rows = []
    for name, law, xs, tol in WORLDS:
        for explicit in (False, True):
            row = {'world': name, 'grammar': 'arithmetic' if explicit else 'open',
                   **run(law, xs, tol, explicit)}
            rows.append(row)
            print(json.dumps(row))
    Path(output).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'yield-probe.json')
