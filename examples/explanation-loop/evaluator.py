"""Frozen public finite oracle, independent of the model and interpreter."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ident, candidate_output, verdict_output = sys.argv[1:]
raw = Path(candidate_output).read_bytes()
candidate = json.loads(raw)
data = json.loads(Path('data.json').read_text())
expected = [x % 2 for x in data['inputs']]
correct = candidate['predictions'] == expected
training = sum((candidate['predictions'][i] - y)**2 for i, (_, y) in enumerate(data['observations']))
with sqlite3.connect('file:.rds/project.sqlite3?mode=ro', uri=True) as db:
    event = json.loads(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='STRUCTURE_PROPOSAL' "
                                "AND json_extract(body,'$.id')=?", (ident,)).fetchone()[0])
    receipt = json.loads(db.execute('SELECT body FROM receipts WHERE run_id=?', (ident,)).fetchone()[0])
row = json.loads(Path(event['record']['path']).read_text(encoding='utf-8'))
verdict = {'scope_sha256': row['scope_sha256'], 'proposal_sha256': row['proposal_sha256'],
           'candidate_run_id': ident, 'candidate_receipt_sha256': receipt['sha256'],
           'output_sha256': hashlib.sha256(raw).hexdigest(), 'goal_status': 'PASS' if correct else 'FAIL',
           'observation': 'SUPPORT' if correct else 'REFUTE', 'training_squared_error': training,
           'training_residuals': [candidate['predictions'][i] - y for i, (_, y) in enumerate(data['observations'])],
           'checked_rows': len(expected)}
if ident != 'baseline':
    verdict['probe_target'] = data['probe_x'] % 2
Path(verdict_output).write_text(json.dumps(verdict), encoding='utf-8')
