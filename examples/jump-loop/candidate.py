"""Only this source is replaceable by the authorized AI method revision."""
import hashlib
import json
from pathlib import Path
import sys


def predict(x, y):
    return x + y


data = json.loads(Path('points.json').read_text(encoding='utf-8'))
answer = {'schema': 1, 'inputs_sha256': hashlib.sha256(Path('points.json').read_bytes()).hexdigest(),
          'point_ids': [p['id'] for p in data['points']],
          'polynomial_ids': [p['id'] for p in data['polynomials']],
          'values': [[str(predict(*(int(v) for v in p['coordinates'])))] for p in data['points']]}
Path(sys.argv[1]).write_text(json.dumps(answer), encoding='utf-8')
