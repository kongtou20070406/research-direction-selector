"""Research notes in the existing project event ledger."""
import json
import math
from pathlib import Path

from rds_project import ProjectStore

TYPES = ('goal', 'expect', 'observe', 'attempt', 'claim', 'constraint', 'answer', 'dismiss', 'shown')
FIELDS = {'route', 'outcome', 'metric', 'value', 'tolerance', 'scale', 'ref', 'aggregate', 'endpoint', 'source', 'key', 'reason', 'auto'}


def append(root, type, text='', **fields):
    if type not in TYPES or set(fields) - FIELDS or not isinstance(text, str) or len(text) > 2000:
        raise ValueError('Invalid note type, text or fields')
    if fields.get('outcome', 'unknown') not in ('ok', 'fail', 'unknown'):
        raise ValueError('Invalid outcome')
    for name in ('value', 'tolerance'):
        if name in fields and (type_of_number(fields[name]) is False or not math.isfinite(fields[name])):
            raise ValueError(f'{name} must be a finite number')
    if 'tolerance' in fields and fields['tolerance'] < 0:
        raise ValueError('tolerance must be nonnegative')
    if 'ref' in fields and (not isinstance(fields['ref'], list) or not all(isinstance(x, str) and x for x in fields['ref'])):
        raise ValueError('ref must be a list of note ids')
    if type == 'answer' and (not fields.get('key') or not fields.get('ref')) or type == 'dismiss' and (not fields.get('key') or not fields.get('reason')):
        raise ValueError('Closure requires key and evidence or reason')
    store = ProjectStore(root)
    store.state_dir.mkdir(exist_ok=True)
    event = {'kind': 'RESEARCH_NOTE', 'type': type, 'text': text, **fields}
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables and 'events' not in tables:
            raise ValueError('Existing database is not an RDS event ledger')
        from rds_campaign import detached_admission
        detached_admission(store.root, db)
        db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS events_no_{operation.lower()} BEFORE {operation} ON events BEGIN SELECT RAISE(ABORT,'events are append-only'); END")
        store.record_event(db, event)
        rowid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    return {**event, 'id': f'n{rowid}'}


def type_of_number(value):
    return type(value) in (int, float)


def read(root, limit=None):
    store = ProjectStore(root)
    if not store.path.is_file():
        return []
    with store.readonly() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return []
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError('limit must be a nonnegative integer')
        sql = "SELECT id,body FROM events WHERE json_extract(body,'$.kind')='RESEARCH_NOTE' ORDER BY id"
        if limit is not None:
            sql = "SELECT id,body FROM (" + sql + " DESC LIMIT ?) ORDER BY id"
        rows = db.execute(sql, () if limit is None else (limit,)).fetchall()
        return [{**json.loads(row['body']), 'id': f"n{row['id']}"} for row in rows]
