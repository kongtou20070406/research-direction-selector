"""Discrete sparse-exception probe: the claim the bare agent makes, the claim RDS withholds.

Six points of `x + 1`; the truth differs at x=31 (1000). The bare agent writes
`x + 1` and scores 31/32. Compass with `holdout: 2` buys x=30 and x=31 after the
fit, refutes the leader at x=31 and ends without a claim.

    python -B benchmark/newtonbench/holdout_discrete.py results/holdout-discrete.json
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import compass_fixture as fx
from rds_compass_declaration import initialize
import rds_compass as compass
import rds_compass_store as cstore
from rds_compass_drive import drive


def main(out_path):
    root = Path(out_path).resolve().parent / 'sparse-campaign'
    shutil.rmtree(root, ignore_errors=True)
    path = fx.law_campaign(root, 'x+1 if x != 31 else 1000', range(32),
                           hypotheses=[('lin', 'x+1')],
                           verification={'holdout': 2, 'confirmations': 2})
    initialize(root, path)
    result = drive(root, publish=False)
    state = compass.fold(cstore.store_for(root))
    out = {'status': result['status'],
           'claim': (result.get('verification') or {}).get('claim'),
           'law': (result.get('verification') or {}).get('predict'),
           'holdout_points': state['plan'].get('holdout'),
           'holdout_decisions': [d['candidate'] for d in state['decisions'] if d['action'] == 'HOLDOUT'],
           'baseline_exact_match': '31/32 (x=31 wrong)',
           'baseline_claim': 'x + 1'}
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'holdout-discrete.json')
