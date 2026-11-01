"""RDS EIG advisor as a standalone CLI for external benchmarks.

Given measured points and a small rival set, return the next inputs with the
highest expected information gain per cost. This is the RDS Advisor without
the Compass loop: it chooses what to measure, it does not fit a law.

    python -B benchmark/newtonbench/eig_advisor.py request.json

Request: {"rows": [...], "candidates": [{"id": "...", "inputs": {...}, "cost": "1"}],
          "rivals": [{"id": "...", "predict": "expression"}],
          "tolerances": {"y": {"abs": 0, "rel": 0.01}}}
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass_model as model


def choose(rows, candidates, rivals, tolerances):
    inputs = list(candidates[0]['inputs'])
    hyps = [model.compile_hypothesis({'id': r['id'], 'predict': {'y': r['predict']}}, inputs, ['y'])
            for r in rivals]
    observed = [{k: model.exact(v) for k, v in row['inputs'].items()} for row in rows]
    return model.rank_experiments(hyps, candidates, tolerances, observed=observed, limit=8)


def main(path):
    request = json.load(open(path, encoding='utf-8'))
    tolerances = {name: model.tolerance(spec) for name, spec in request['tolerances'].items()}
    print(json.dumps(choose(request.get('rows', []), request['candidates'], request['rivals'],
                            tolerances), indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
