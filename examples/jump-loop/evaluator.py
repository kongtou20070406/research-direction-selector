"""Frozen independent exact finite checker; never imports candidate code."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from polynomial_checker import check_output, evaluate

rid, source, output = sys.argv[1:]
read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt = json.loads(db.execute('SELECT body FROM receipts WHERE run_id=?', (rid,)).fetchone()[0])
replay = check_output(read('claim.json'), read('points.json'), read(source), sha('points.json'))
# The proposal's separate discriminator is measured by the frozen evaluator.
# It does not certify that an AI implementation produced correct predictions.
probe = read('out/synthesize.json')['result']['search']['probe']
data = read('points.json')
data['points'] = [{'id': 'discriminator', 'coordinates': [str(probe[v]) for v in data['variables']]}]
probe_value = int(evaluate(read('claim.json'), data)['values'][0][0])
answer = {'candidate_receipt_sha256': receipt['sha256'], 'claim_sha256': sha('claim.json'),
          'evaluator_sha256': sha('evaluator.py'), 'inputs_sha256': sha('points.json'),
          'verdict': replay['status'], 'replay': replay, 'probe_value': probe_value}
Path(output).write_text(json.dumps(answer), encoding='utf-8')
