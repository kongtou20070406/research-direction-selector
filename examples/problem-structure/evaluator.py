"""Frozen independent finite-case oracle; reads actual original candidate bytes.

The caller can supply no success flags. This oracle checks these fixtures only.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ident, candidate_run, candidate_output, verdict_output, mode = sys.argv[1:]
raw = Path(candidate_output).read_bytes()
predictions = json.loads(raw)['predictions']
data = json.loads(Path('data.json').read_text())
if data['case'] == 'knowledge':
    expected = [a ^ b for a, b in data['inputs']]
elif data['case'] == 'decomposition':
    expected = [((a + b) // 2) * ((a - b) // 2) for a, b in data['inputs']]
else:
    expected = [a % 2 for a, in data['inputs']]
correct = len(predictions) == len(expected) and predictions == expected
db = sqlite3.connect('file:.rds/project.sqlite3?mode=ro', uri=True)
try:
    event = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='STRUCTURE_PROPOSAL' AND json_extract(body,'$.id')=?", (ident,)).fetchone()
    receipt = json.loads(db.execute('SELECT body FROM receipts WHERE run_id=?', (candidate_run,)).fetchone()[0])
finally:
    db.close()
verdict = {'observation': 'SUPPORT' if correct else 'REFUTE', 'goal_status': 'PASS' if correct else 'FAIL',
           'candidate_run_id': candidate_run, 'candidate_receipt_sha256': receipt['sha256'],
           'output_sha256': hashlib.sha256(raw).hexdigest(), 'correct': correct, 'checked_rows': len(expected)}
if event:
    proposal = json.loads(Path(json.loads(event[0])['record']['path']).read_text(encoding='utf-8'))
    verdict.update(scope_sha256=proposal['scope_sha256'], proposal_sha256=proposal['proposal_sha256'])
if mode == 'wrong_scope':
    verdict['scope_sha256'] = '0' * 64
if mode == 'unknown':
    verdict.update(observation='UNKNOWN', goal_status='UNKNOWN', correct=None)
Path(verdict_output).parent.mkdir(parents=True, exist_ok=True)
Path(verdict_output).write_text(json.dumps(verdict), encoding='utf-8')
