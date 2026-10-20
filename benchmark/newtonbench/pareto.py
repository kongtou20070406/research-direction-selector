"""Cost/correctness Pareto probe: RDS with an experiment budget vs a fixed six-point baseline.

Three worlds that need more evidence than the six initial points. The RDS arm
runs with a hard experiment ceiling; the baseline arm only sees the same six
points and fits a straight line. Each run records experiments used and exact
match on the 32-point truth.

    python -B benchmark/newtonbench/pareto.py results/pareto.json
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).resolve().parent)]
import compass_fixture as fx
from rds_compass_declaration import initialize
import rds_compass as compass
import rds_compass_store as cstore
from rds_compass_drive import drive
from sparse_suite import score

WORLDS = [
    ('sparse-tail', 'x+1 if x != 31 else 1000'),
    ('sparse-mid', 'x+1 if x != 20 else 1000'),
    ('regime-double', 'x+1 if x < 10 else 2*x'),
]
BUDGETS = [6, 8, 12, 16, 24, 32]


def baseline(law, budget):
    """Same budget, no RDS: uniform points, least-squares straight line.

    The baseline must not be starved: it gets the same number of measurements as
    the RDS arm, just chosen uniformly and fitted with a line.
    """
    if budget >= 32:
        xs = list(range(32))
    else:
        xs = sorted(set(round(i * 31 / (budget - 1)) for i in range(budget)))
    ys = [eval(law, {'__builtins__': {}}, {'x': x}) for x in xs]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom if denom else 0.0
    intercept = mean_y - slope * mean_x
    right = 0
    for x in range(32):
        truth = eval(law, {'__builtins__': {}}, {'x': x})
        if slope * x + intercept == truth:
            right += 1
    return right


def main(out_path):
    rows = []
    for budget in BUDGETS:
        for name, law in WORLDS:
            rows.append({'task': name, 'arm': 'baseline-uniform', 'budget': budget,
                         'experiments': budget, 'exact_match': baseline(law, budget)})
    for budget in BUDGETS:
        for name, law in WORLDS:
            root = Path(tempfile.mkdtemp()) / f'{name}-{budget}'
            path = fx.law_campaign(root, law, range(32), hypotheses=[('lin', 'x+1')],
                                   verification={'holdout': 2, 'confirmations': 2, 'max_gap': 2},
                                   budget={'wall_seconds': 900, 'experiments': budget,
                                           'max_experiments': budget},
                                   synthesis={'max_nodes': 5, 'max_candidates': 4000},
                                   escape={'max_jumps': 2, 'probe_points': 1})
            initialize(root, path)
            result = drive(root, publish=False)
            state = compass.fold(cstore.store_for(root))
            verification = result.get('verification') or {}
            claim = verification.get('claim')
            expr = (verification.get('predict') or {}).get('y')
            best_expr = ((verification.get('best_effort') or {}).get('predict') or {}).get('y')
            claimed = claim == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'
            rows.append({'task': name, 'arm': 'rds', 'budget': budget,
                         'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
                         'status': result['status'], 'claim': claim,
                         'claim_exact': score(expr, law) if (expr and claimed) else 0,
                         'best_effort_exact': score(best_expr, law) if best_expr else 0,
                         'exact_match': score(expr, law) if expr else 0})
            print(json.dumps(rows[-1]))
    Path(out_path).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'pareto.json')
