"""Run ONE (world, budget) cell of the pareto suite, without touching results/pareto.json.

Saturation-stopping preregistration (benchmark/newtonbench/saturation-protocol.md section 2.1)
compares a candidate stopping rule against the frozen baseline recorded in
benchmark/newtonbench/results/pareto.json. That baseline file is the control and must not be
overwritten, so this probe writes to its own path instead.

The declaration below is copied verbatim from pareto.py's rds arm so the cell stays comparable.

    python -B benchmark/newtonbench/probe_cell.py regime-double 24 out.json

``--max-gap`` (default 2, the same value pareto.py hard-codes) overrides ONLY the declared
``verification.max_gap``. The default keeps every run comparable with the frozen baseline; the
flag exists to attribute PROBE/COVERAGE_GAP spending to that one rule.
"""
import argparse
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
from pareto import WORLDS


def run(world, budget, max_gap=2, decisions_out=None):
    law = dict(WORLDS)[world]
    root = Path(tempfile.mkdtemp()) / f'{world}-{budget}'
    path = fx.law_campaign(root, law, range(32), hypotheses=[('lin', 'x+1')],
                           verification={'holdout': 2, 'confirmations': 2, 'max_gap': max_gap},
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
    claimed = claim == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'
    actions = {}
    for decision in state['decisions']:
        actions[decision['action']] = actions.get(decision['action'], 0) + 1
    if decisions_out:
        # Optional attribution dump: reads the folded decisions only; no declaration value changes.
        Path(decisions_out).write_text(json.dumps(
            [{'seq': d['seq'], 'action': d['action'], 'reason': d.get('reason')}
             for d in state['decisions']], indent=1) + '\n', encoding='utf-8')
    return {
        'world': world,
        'budget': budget,
        'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
        'status': result['status'],
        'claim': claim,
        'claim_exact': score(expr, law) if (expr and claimed) else 0,
        'exact_match': score(expr, law) if expr else 0,
        'predict': expr,
        'actions': actions,
        'declaration': {
            'verification': state['plan']['verification'],
            'candidates': len(state['plan']['candidates']),
        },
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('world')
    parser.add_argument('budget', type=int)
    parser.add_argument('out')
    parser.add_argument('--max-gap', type=int, default=2, dest='max_gap',
                        help='declared verification.max_gap; default 2 matches pareto.py')
    parser.add_argument('--dump-decisions', dest='decisions_out', default=None,
                        help='optional path for the per-decision action/reason list (read-only)')
    args = parser.parse_args()
    row = run(args.world, args.budget, max_gap=args.max_gap, decisions_out=args.decisions_out)
    Path(args.out).write_text(json.dumps(row, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(row))
