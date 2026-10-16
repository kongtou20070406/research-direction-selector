"""Compass experiment program: one NewtonBench simulator query per kernel run.

Copied into each Compass project root as ``experiment.py`` and bound by hash in the kernel contract.
``task.json`` (also bound) names the NewtonBench checkout, module, difficulty, law version and noise level.
The program calls NewtonBench's own ``run_experiment_for_module`` for the vanilla_equation system and writes
``{"outputs": {"y": value}}``. A non-finite value is reported without an observable (no measurement).

    python -B experiment.py --id <candidate> --<input> <p/q> ... --output <path>
"""
import argparse
import importlib
import json
import math
import pathlib
import sys
import types
from fractions import Fraction

task = json.loads(pathlib.Path('task.json').read_text(encoding='utf-8'))
parser = argparse.ArgumentParser()
parser.add_argument('--id', required=True)
parser.add_argument('--output', required=True)
for name in task['inputs']:
    parser.add_argument('--' + name, required=True)
args = parser.parse_args()

# The kernel launches the interpreter by its resolved path, outside the venv: add the venv's packages explicitly.
for path in reversed(task.get('site_packages', [])):
    sys.path.insert(0, path)
sys.path.insert(0, task['newtonbench'])
stub = types.ModuleType('utils.call_llm_api')


def _no_llm(*_args, **_kwargs):
    raise RuntimeError('The simulator never calls a language model')


stub.call_llm_api = _no_llm
import utils  # noqa: E402,F401
sys.modules['utils.call_llm_api'] = stub
module = importlib.import_module('modules.' + task['module'])

inputs = {name: float(Fraction(getattr(args, name))) for name in task['inputs']}
value = module.run_experiment_for_module(**inputs, noise_level=task['noise'], difficulty=task['difficulty'],
                                         system='vanilla_equation', law_version=task['version'])
value = float(value)
body = {'outputs': {'y': value}} if math.isfinite(value) else {'outputs': {}, 'non_finite': repr(value)}
out = pathlib.Path(args.output)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(body), encoding='utf-8')
