"""Declared cost inputs for plan/prerequisite tests, not measured forecasts.

These cases ask what native admission does GIVEN cost feasibility. They do not
require three measured pilots. No receipt, cost or owned state is fabricated;
the original estimator response stays visible. Real measurement/applicability,
UNKNOWN and refusal cases use the original estimator separately.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_feasibility
from rds_project import digest
from tool_evolution_cli_fixture import validate_fixture_root

BOUNDS = {'slow': (10000.0, 30000.0), 'fast': (2.0, 6.0), 'verify': (1.0, 3.0)}

def validate_premise(premise):
    if premise.get('purpose') != 'cost-feasible-plan-and-prerequisite-test' or set(premise['routes']) != set(BOUNDS):
        raise ValueError('Unexpected feasibility test premise purpose or routes')
    for run_id, bounds in BOUNDS.items():
        route = premise['routes'][run_id]
        values = (route['lower_wall_seconds'], route['upper_wall_seconds'])
        if any(type(v) not in (int, float) for v in values) or values != bounds:
            raise ValueError('Declared test costs cannot adapt to measured cost or allowance')

def declared_estimator(premise, original):
    validate_premise(premise)
    expected_root = Path(premise['root']).resolve()
    def estimate(store, state, run_id, manifest, model):
        real = original(store, state, run_id, manifest, model)
        if run_id not in premise['routes'] or model is None:
            return real
        bound = premise['routes'][run_id]
        if (Path(store.root).resolve() != expected_root
                or digest(state['contract']) != premise['contract_sha256']
                or digest(manifest) != bound['manifest_sha256'] or digest(model) != bound['model_sha256']):
            return {'status':'UNKNOWN', 'run_id':run_id, 'lower_wall_seconds':0, 'upper_wall_seconds':None,
                    'reason':'Declared test cost identity differs', 'native_estimate':deepcopy(real)}
        # Missing pilot data is deliberately replaced by the GIVEN test input.
        # Existing failed/stale pilots or broken protocol evidence stay UNKNOWN.
        if real['status'] != 'CONDITIONAL_FORECAST':
            supplied_pilots = set(model['pilot_runs']) & {r['run_id'] for r in state['receipts']}
            if supplied_pilots or real.get('reason') != 'Pilot is not successful':
                return real
        return {'status':'CONDITIONAL_FORECAST', 'run_id':run_id,
                'lower_wall_seconds':bound['lower_wall_seconds'], 'upper_wall_seconds':bound['upper_wall_seconds'],
                'basis':'TEST_DECLARED_COST_PREMISE', 'sources':[],
                'assumptions':['Synthetic GIVEN cost feasibility, not a measured runtime forecast'],
                'native_estimate':deepcopy(real)}
    return estimate

def load_premise(expected_root, premise_path, args):
    validate_fixture_root(expected_root, premise_path, args)
    premise = json.loads(Path(premise_path).read_text(encoding='utf-8'))
    if Path(premise['root']).resolve() != Path(expected_root).resolve():
        raise ValueError('Declared test cost root differs')
    validate_premise(premise)
    return premise

def main():
    expected_root, premise_path, *args = sys.argv[1:]
    premise = load_premise(expected_root, premise_path, args)
    import rds_cli
    sys.argv = [str(ROOT / 'scripts/rds_cli.py'), *args]
    with patch.object(rds_feasibility, '_estimate', declared_estimator(premise, rds_feasibility._estimate)):
        return rds_cli.main()

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
