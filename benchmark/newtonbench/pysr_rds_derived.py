"""RDS gives PySR the intermediate variable, PySR searches the reduced space, RDS replays.

Truth: y = 6.674e-05 * (m1 + m2)^2 / r^1.5, 24 points, 1% noise. PySR sees
(m1, m2, r, m1+m2); every frontier equation is mapped back to the original
variables for the RDS replay, so the replay never depends on the derived name.

    python -B benchmark/newtonbench/pysr_rds_derived.py results/pysr-rds-derived.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from pysr import PySRRegressor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'benchmark' / 'newtonbench'))
import rds_compass_model as model
from pysr_adapter import adapt_pysr_expression

C = 6.674e-05


def run(out_path):
    rng = np.random.default_rng(7)
    m1 = rng.uniform(1, 10, 24)
    m2 = rng.uniform(1, 5, 24)
    r = rng.uniform(1, 10, 24)
    y = C * (m1 + m2) ** 2 / r ** 1.5
    y = y * (1 + rng.uniform(-0.01, 0.01, y.size))
    X = np.column_stack([m1, m2, r, m1 + m2])
    started = time.time()
    search = PySRRegressor(niterations=25, binary_operators=['+', '-', '*', '/'],
                           unary_operators=['sqrt'], procs=0, random_state=0, verbosity=0)
    search.fit(X, y)
    elapsed = round(time.time() - started, 2)
    equations = search.equations_['equation'].head(6).tolist()
    rows = [{'id': f'p{i}', 'inputs': {'mass1': model.exact(repr(float(a))),
                                      'mass2': model.exact(repr(float(b))),
                                      'distance': model.exact(repr(float(c)))},
             'outputs': {'y': model.exact(repr(float(value)))}}
            for i, (a, b, c, value) in enumerate(zip(m1, m2, r, y))]
    tolerances = {'y': (model.exact(0), model.exact(2) / 100)}
    results = []
    for equation in equations:
        source = (equation.replace('x0', 'mass1').replace('x1', 'mass2')
                  .replace('x2', 'distance').replace('x3', 's12'))
        for item in adapt_pysr_expression(source, ['mass1', 'mass2', 'distance', 's12']):
            entry = {'equation': equation, 'supported': item['supported']}
            if item['supported']:
                expression = item['expression'].replace('s12', '(mass1 + mass2)')
                entry['rds'] = expression
                try:
                    hyp = model.compile_hypothesis({'id': 'h', 'predict': {'y': expression}},
                                                   ['mass1', 'mass2', 'distance'], ['y'])
                    replay = model.replay(hyp, rows, tolerances)
                    entry['replay'] = replay['status']
                    entry['counterexamples'] = len(replay['counterexamples'])
                except (ValueError, KeyError, TypeError) as exc:
                    entry['replay'] = 'COMPILE_ERROR: ' + str(exc)[:120]
            else:
                entry['reason'] = item['reason']
            results.append(entry)
    out = {'search_seconds': elapsed, 'derived': 's12 = mass1 + mass2',
           'equations': equations, 'results': results}
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else 'pysr-rds-derived.json')
