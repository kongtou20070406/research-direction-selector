"""Independent exact table comparison; does not import the search/evaluator DSL."""
import json
import hashlib
from pathlib import Path
import sqlite3
import sys

candidate = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
expected = json.loads(Path('oracle.json').read_text(encoding='utf-8'))
matched = candidate['predictions'] == expected
probe = next(row['value'] for row in expected if row['inputs'] == candidate['probe'])
with sqlite3.connect('file:.rds/project.sqlite3?mode=ro', uri=True) as db:
    events = [json.loads(row[0]) for row in db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='STRUCTURE_PROPOSAL'")]
    if len(events) != 1:
        raise ValueError('Example requires one retained generated proposal')
    event = events[0]
    raw = Path(event['record']['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != event['record']['sha256']:
        raise ValueError('Proposal original changed')
    row = json.loads(raw)
    receipt = json.loads(db.execute("SELECT body FROM receipts WHERE run_id='jump-candidate'").fetchone()[0])
result = {'goal_status': 'PASS' if matched else 'FAIL', 'probe_value': probe,
          'observation': 'SUPPORT' if matched else 'REFUTE',
          'scope_sha256': row['scope_sha256'], 'proposal_sha256': row['proposal_sha256'],
          'candidate_run_id': 'jump-candidate', 'candidate_receipt_sha256': receipt['sha256'],
          'output_sha256': hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
          'scope': 'EXACT_DECLARED_FINITE_ROWS', 'rows': len(expected)}
Path(sys.argv[2]).write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
