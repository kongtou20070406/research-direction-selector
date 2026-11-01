"""PySR candidates -> RDS dialect -> replay: one joint smoke.

PySR searches the expression; the adapter maps it to the RDS dialect; RDS
replays it against the same measurements. The search time is recorded as its
own cost, not hidden in agent tokens.

    python -B benchmark/newtonbench/pysr_rds_loop.py results/pysr-rds-smoke.json
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


def run(out_path):
    x = np.linspace(0.5, 5.0, 20)
    y = 2 * x ** 2 + 1
    started = time.time()
    search = PySRRegressor(niterations=8, binary_operators=['+', '*', '/'],
                           unary_operators=[], procs=0, random_state=0, verbosity=0)
    search.fit(x.reshape(-1, 1), y)
    elapsed = round(time.time() - started, 2)
    equations = search.equations_['equation'].head(5).tolist()
    rows = [{'id': f'p{i}', 'inputs': {'x': model.exact(repr(float(xi)))},
             'outputs': {'y': model.exact(repr(float(yi)))}} for i, (xi, yi) in enumerate(zip(x, y))]
    tolerances = {'y': (model.exact(0), model.exact(1) / 10 ** 6)}
    results = []
    for equation in equations:
        for item in adapt_pysr_expression(equation.replace('x0', 'x'), ['x']):
            entry = {'equation': equation, 'rds': item['expression'], 'supported': item['supported']}
            if item['supported']:
                try:
                    hyp = model.compile_hypothesis({'id': 'h', 'predict': {'y': item['expression']}}, ['x'], ['y'])
                    entry['replay'] = model.replay(hyp, rows, tolerances)['status']
                except (ValueError, KeyError, TypeError) as exc:
                    entry['replay'] = 'COMPILE_ERROR: ' + str(exc)[:120]
            else:
                entry['reason'] = item['reason']
            results.append(entry)
    out = {'search_seconds': elapsed, 'equations': equations, 'results': results}
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else 'pysr-rds-smoke.json')
