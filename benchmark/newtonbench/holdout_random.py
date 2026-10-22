"""Random holdout: estimate the false-certification rate instead of one layout.

Three sparse worlds with a mid-domain exception, four random holdout points,
ten seeds. A false certification is a claim that is wrong on the full domain.

    python -B benchmark/newtonbench/holdout_random.py results/holdout-random.json
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
from sparse_suite import TASKS, score

WORLDS = [task for task in TASKS if task[0] in ('sparse-mid', 'sparse-edge', 'sparse-two')]
SEEDS = range(1, 11)


def main(out_path):
    rows = []
    for seed in SEEDS:
        for name, law in WORLDS:
            root = Path(tempfile.mkdtemp()) / f'{name}-{seed}'
            path = fx.law_campaign(root, law, range(32), hypotheses=[('lin', 'x+1')],
                                   verification={'holdout': 4, 'holdout_seed': seed, 'confirmations': 2,
                                                 'max_confirmations': 4},
                                   synthesis={'max_nodes': 5, 'max_candidates': 4000},
                                   escape={'max_jumps': 2, 'probe_points': 1})
            initialize(root, path)
            result = drive(root, publish=False)
            verification = result.get('verification') or {}
            expr = (verification.get('predict') or {}).get('y')
            state = compass.fold(cstore.store_for(root))
            row = {'seed': seed, 'task': name, 'status': result['status'],
                   'claim': verification.get('claim'), 'law': expr,
                   'exact_match': f'{score(expr, law)}/32' if expr else '0/32',
                   'holdout': state['plan'].get('holdout')}
            rows.append(row)
            print(json.dumps(row))
    Path(out_path).write_text(json.dumps(rows, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'holdout-random.json')
