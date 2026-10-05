"""Test subprocess entrypoint with explicit, identity-bound forecast premises.

Only the flow test uses this bootstrap. Production CLI, receipts, measured costs,
admission gates and worker/verifier processes are unchanged. An applicable real
estimate is required first; synthetic bounds are labelled and the real estimate
is retained separately. This does not measure runtime scalability.
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


def controlled_estimator(premise, original):
    expected_root = Path(premise['root']).resolve()

    def estimate(store, state, run_id, manifest, model):
        real = original(store, state, run_id, manifest, model)
        if run_id not in premise['routes'] or real['status'] != 'CONDITIONAL_FORECAST':
            return real
        bound = premise['routes'][run_id]
        if (Path(store.root).resolve() != expected_root
                or digest(state['contract']) != premise['contract_sha256']
                or digest(manifest) != bound['manifest_sha256']
                or digest(model) != bound['model_sha256']):
            return {'status': 'UNKNOWN', 'run_id': run_id, 'lower_wall_seconds': 0,
                    'upper_wall_seconds': None, 'reason': 'Test forecast premise identity differs',
                    'real_forecast': real}
        return {**real, 'lower_wall_seconds': bound['lower_wall_seconds'],
                'upper_wall_seconds': bound['upper_wall_seconds'],
                'basis': 'TEST_SYNTHETIC_CONDITIONAL_FORECAST', 'sources': [],
                'assumptions': ['Explicit flow-test premise; not a measured runtime forecast'],
                'real_forecast': deepcopy(real)}

    return estimate


def validate_fixture_root(expected_root, premise_path, args):
    # Windows may expose the same directory with different case or aliases.
    # Keep the explicit CLI root and same-root premise requirements.
    expected_root = Path(expected_root).resolve()
    if (len(args) < 2 or args[0] != '--root'
            or Path(args[1]).resolve() != expected_root
            or Path(premise_path).resolve().parent != expected_root):
        raise ValueError('Flow-test bootstrap requires its exact explicit fixture root')


def main():
    # Parent supplies exact fixture identity, followed by the actual CLI args.
    expected_root, premise_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    args = sys.argv[3:]
    validate_fixture_root(expected_root, premise_path, args)
    premise = json.loads(premise_path.read_text(encoding='utf-8'))
    if Path(premise['root']).resolve() != expected_root or set(premise['routes']) != {'fast', 'verify'}:
        raise ValueError('Flow-test premise has an unexpected root or route')
    # Bounds are fixed independently of live allowance/deadline or measured wall.
    expected_bounds = {'fast': (2.0, 6.0), 'verify': (1.0, 3.0)}
    for run_id, bounds in expected_bounds.items():
        route = premise['routes'][run_id]
        if (route['lower_wall_seconds'], route['upper_wall_seconds']) != bounds:
            raise ValueError('Flow-test premise cannot adapt to available resources')
    import rds_cli
    sys.argv = [str(ROOT / 'scripts/rds_cli.py'), *args]
    with patch.object(rds_feasibility, '_estimate', controlled_estimator(premise, rds_feasibility._estimate)):
        return rds_cli.main()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
