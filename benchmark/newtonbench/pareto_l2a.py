"""L2-a pareto arm: pareto.py's RDS sweep with a declared agent, so LOW_MARGINAL_EIG can fire.

The declaration is pareto.py's verbatim (same worlds, same budgets, same verification/synthesis/
escape settings); the only difference is ``agent='reject'`` handed to
``compass_fixture.law_campaign`` (tests/compass_fixture.py:51). Without a declared agent,
``_agent_live`` is always False and the L2-a route is dead code, so the acceptance run must
declare one.

Output is a list of rows with the same keys as pareto.py plus ``trigger`` (the count of
LOW_MARGINAL_EIG escapes seen in the mission) so the report can show the route actually fired.

    python -B benchmark/newtonbench/pareto_l2a.py benchmark/newtonbench/results/pareto-l2a.json
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
from pareto import BUDGETS, WORLDS, baseline


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
                                   escape={'max_jumps': 2, 'probe_points': 1},
                                   agent='reject')
            initialize(root, path)
            result = drive(root, publish=False)
            state = compass.fold(cstore.store_for(root))
            verification = result.get('verification') or {}
            claim = verification.get('claim')
            expr = (verification.get('predict') or {}).get('y')
            best_expr = ((verification.get('best_effort') or {}).get('predict') or {}).get('y')
            claimed = claim == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'
            triggers = sum(1 for d in state['decisions'] if d['action'] == 'ESCAPE'
                           and (d.get('basin') or {}).get('trigger') == 'LOW_MARGINAL_EIG')
            rows.append({'task': name, 'arm': 'rds', 'budget': budget,
                         'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
                         'status': result['status'], 'claim': claim,
                         'claim_exact': score(expr, law) if (expr and claimed) else 0,
                         'best_effort_exact': score(best_expr, law) if best_expr else 0,
                         'exact_match': score(expr, law) if expr else 0,
                         'predict': expr,
                         'low_marginal_eig_triggers': triggers})
            print(json.dumps(rows[-1]), flush=True)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')
    print('wrote ' + str(out_path), flush=True)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else
         str(Path(__file__).resolve().parent / 'results' / 'pareto-l2a.json'))
