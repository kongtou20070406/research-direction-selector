"""RDS expression synthesizer as a standalone CLI for external benchmarks.

Given measured points, return counterexample-filtered candidate laws from the
RDS dialect. No model call, no external API.

    python -B benchmark/newtonbench/rds_tool.py request.json

Request: {"inputs": ["mass1", "mass2", "distance"], "observable": "y",
          "tolerances": {"y": {"abs": 0, "rel": 0}},
          "rows": [{"inputs": {"mass1": "2", ...}, "outputs": {"y": "3.3e-05"}}, ...],
          "grammar": {"variables": [...], "operators": [...], "constants": [...]},
          "max_nodes": 9, "max_candidates": 200000}
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass_model as model


def main(path):
    request = json.load(open(path, encoding='utf-8'))
    tolerances = {name: model.tolerance(spec) for name, spec in request['tolerances'].items()}
    found = model.synthesize(
        request['rows'], request['inputs'], request['observable'], tolerances,
        grammar=request.get('grammar'), max_nodes=request.get('max_nodes', 9),
        max_candidates=request.get('max_candidates', 200000), probes=[],
        keep=request.get('keep', 8))
    print(json.dumps(found, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
