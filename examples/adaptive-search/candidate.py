"""Frozen finite computation for a public development fixture, not discovery."""
import json
from pathlib import Path
import sys

mode, output = sys.argv[1:]
inputs = json.loads(Path('data.json').read_text(encoding='utf-8'))['inputs']
operations = {'zero': lambda x: 0, 'linear': lambda x: 3 * x, 'quadratic': lambda x: x * x}
predictions = [operations[mode](x) for x in inputs]
Path(output).parent.mkdir(exist_ok=True)
Path(output).write_text(json.dumps({'predictions': predictions}), encoding='utf-8')
