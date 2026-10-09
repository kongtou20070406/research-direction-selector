"""Independent exact finite oracle. Does not consume candidate success flags."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ident, candidate_output, verdict_output, mode = sys.argv[1:]
raw = Path(candidate_output).read_bytes()
actual = json.loads(raw)['predictions']
xs = json.loads(Path('data.json').read_text(encoding='utf-8'))['inputs']
expected = [x ** 2 for x in xs]
valid = len(actual) == len(expected) and all(type(x) is int for x in actual)
correct = valid and actual == expected
mse = sum((a - b) ** 2 for a, b in zip(actual, expected)) / len(xs) if valid else None
db = sqlite3.connect('file:.rds/project.sqlite3?mode=ro', uri=True)
event = json.loads(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='STRUCTURE_PROPOSAL' AND json_extract(body,'$.id')=?", (ident,)).fetchone()[0])
proposal = json.loads(Path(event['record']['path']).read_text(encoding='utf-8'))
receipt_sha = db.execute('SELECT sha256 FROM receipts WHERE run_id=?', (ident,)).fetchone()[0]
db.close()
verdict = {'scope_sha256': proposal['scope_sha256'], 'proposal_sha256': proposal['proposal_sha256'],
           'candidate_run_id': ident, 'candidate_receipt_sha256': receipt_sha,
           'output_sha256': hashlib.sha256(raw).hexdigest(), 'goal_status': 'PASS' if correct else 'FAIL',
           'observation': 'SUPPORT' if correct else 'REFUTE'}
if mode != 'missing':
    verdict['mse'] = mse
Path(verdict_output).write_text(json.dumps(verdict), encoding='utf-8')
