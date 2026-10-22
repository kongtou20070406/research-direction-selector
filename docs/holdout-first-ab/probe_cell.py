"""Re-run one pareto cell to recover the claimed law expression.

The committed pareto-stage2.json stores claim / claim_exact / certification_check
but not the law expression (certify_arm at pareto.py:159 does not write it).
This reproduces the (regime-double, budget 12, rds) cell with the exact
construction pareto.py:main uses, and prints what the file cannot.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(r'D:/Myfile/work space/RDS/rds-357')
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests'),
                str(ROOT / 'benchmark' / 'newtonbench')]
import compass_fixture as fx
from rds_compass_declaration import initialize
import rds_compass as compass
import rds_compass_store as cstore
import rds_compass_model as model
from rds_compass_drive import drive
from sparse_suite import score

name, law, budget = 'regime-double', 'x+1 if x < 10 else 2*x', 12
root = Path(tempfile.mkdtemp()) / f'{name}-{budget}'
path = fx.law_campaign(
    root, law, range(32), hypotheses=[('lin', 'x+1')],
    verification={'holdout': 2, 'confirmations': 2, 'max_gap': 2},
    budget={'wall_seconds': 900, 'experiments': budget, 'max_experiments': budget},
    synthesis={'max_nodes': 5, 'max_candidates': 4000},
    escape={'max_jumps': 2, 'probe_points': 1})
initialize(root, path)
result = drive(root, publish=False)
state = compass.fold(cstore.store_for(root))
verification = result.get('verification') or {}
expr = (verification.get('predict') or {}).get('y')
print(json.dumps({
    'status': result['status'],
    'claim': verification.get('claim'),
    'law': expr,
    'claim_exact': score(expr, law) if expr else 0,
    'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
}, indent=1))
