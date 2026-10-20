"""Sparse and regime-change suite: does the regime-split fix generalise?

Ten 32-input worlds, six initial points (x=0..5), holdout of two, and a
32-point truth. Each campaign is scored on the law Compass reports, not on a
claim: a `CONSISTENT_BUT_UNCONFIRMED` law still counts as a found law.

    python -B benchmark/newtonbench/sparse_suite.py results/sparse-suite.json
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import compass_fixture as fx
from rds_compass_declaration import initialize
import rds_compass as compass
import rds_compass_model as model
import rds_compass_store as cstore
from rds_compass_drive import drive

TASKS = [
    ('sparse-tail', 'x+1 if x != 31 else 1000'),
    ('sparse-mid', 'x+1 if x != 20 else 1000'),
    ('sparse-edge', 'x+1 if x != 30 else 1000'),
    ('sparse-two', 'x+1 if x not in (7, 23) else 1000'),
    ('sparse-small', 'x+1 if x != 31 else 2'),
    ('regime-double', 'x+1 if x < 10 else 2*x'),
    ('regime-square', '3 if x < 5 else x*x'),
    ('regime-const', '5 if x < 20 else x'),
    ('regime-linear', '2*x+1 if x < 16 else x-3'),
    ('regime-tail', 'x+1 if x < 30 else 7'),
]


def truth(law, x):
    return eval(law, {'__builtins__': {}}, {'x': x})


def score(expr, law):
    try:
        hyp = model.compile_hypothesis({'id': 'h', 'predict': {'y': expr}}, ['x'], ['y'])
    except (ValueError, KeyError, TypeError):
        return 0
    right = 0
    for x in range(32):
        values, status = model.predict(hyp, {'x': model.exact(x)})
        if status == 'PREDICTED' and values.get('y') == model.exact(truth(law, x)):
            right += 1
    return right


def main(out_path):
    rows = []
    for name, law in TASKS:
        root = Path(tempfile.mkdtemp()) / name
        path = fx.law_campaign(root, law, range(32), hypotheses=[('lin', 'x+1')],
                               verification={'holdout': 2, 'confirmations': 2, 'max_confirmations': 4},
                               synthesis={'max_nodes': 5, 'max_candidates': 4000},
                               escape={'max_jumps': 2, 'probe_points': 1})
        initialize(root, path)
        result = drive(root, publish=False)
        verification = result.get('verification') or {}
        expr = (verification.get('predict') or {}).get('y')
        rows.append({'task': name, 'status': result['status'], 'claim': verification.get('claim'),
                     'law': expr, 'exact_match': f'{score(expr, law)}/32' if expr else '0/32'})
        print(json.dumps(rows[-1]))
    Path(out_path).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'sparse-suite.json')
