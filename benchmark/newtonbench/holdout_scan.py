"""Holdout coverage scan: how many reserved points are needed to stop a sparse false claim?

Five sparse worlds, holdout of 2, 4 and 8 points, the same four-confirmation cap.
A false certification is a law that is exact on the measured points but wrong on
the full domain.

    python -B benchmark/newtonbench/holdout_scan.py results/holdout-scan.json
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
import rds_compass_model as model
import rds_compass_store as cstore
from rds_compass_drive import drive
from sparse_suite import TASKS, score

WORLDS = [task for task in TASKS if task[0].startswith('sparse')]


def main(out_path):
    rows = []
    for holdout in (2, 4, 8):
        for name, law in WORLDS:
            root = Path(tempfile.mkdtemp()) / f'{name}-{holdout}'
            path = fx.law_campaign(root, law, range(32), hypotheses=[('lin', 'x+1')],
                                   verification={'holdout': holdout, 'confirmations': 2,
                                                 'max_confirmations': 4},
                                   synthesis={'max_nodes': 5, 'max_candidates': 4000},
                                   escape={'max_jumps': 2, 'probe_points': 1})
            initialize(root, path)
            result = drive(root, publish=False)
            verification = result.get('verification') or {}
            expr = (verification.get('predict') or {}).get('y')
            state = compass.fold(cstore.store_for(root))
            row = {'holdout': holdout, 'task': name, 'status': result['status'],
                   'claim': verification.get('claim'),
                   'law': expr,
                   'exact_match': f'{score(expr, law)}/32' if expr else '0/32',
                   'measurements': len(state['observations'])}
            rows.append(row)
            print(json.dumps(row))
    Path(out_path).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'holdout-scan.json')
