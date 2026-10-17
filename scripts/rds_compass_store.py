"""Compass records inside the existing project ledger and CAS.

Every Compass fact is one ``COMPASS_*`` row in the project ``events`` table whose
body binds a CAS record by SHA-256 and the digest of the previous Compass row.
There is no second database, scheduler or support table: hypothesis status is
recomputed from these records plus original run receipts on every read.
"""
import hashlib
import json
from pathlib import Path
import time

from rds_cas import cas_json
from rds_project import ProjectStore, canonical, digest, require, _shell_argument

PREFIX = 'COMPASS_'
MAX_RECORD = 256 * 1024
MAX_EVENTS = 20000
KINDS = frozenset(('DECLARED', 'HYPOTHESIS', 'OBSERVATION', 'REFUTATION', 'DECISION', 'JUMP',
                   'DISPOSITION', 'VERIFICATION', 'TOOL_QUALIFIED', 'TOOL_REJECTED', 'TOOL_USED',
                   'TOOL_RETRACTED', 'TOOL_PROPOSED', 'HANDOFF', 'INTERRUPTED', 'REPORT'))


class CompassIntegrityError(ValueError):
    """A retained Compass row, its CAS record or the hash chain does not verify."""


LEDGER_TABLES = ('contract', 'budget', 'runs', 'receipts', 'events')


def store_for(root):
    """The project ledger, checked read-only before any Compass query (#359)."""
    store = ProjectStore(root)
    uninitialized = (f'Compass ledger is not initialized at {store.root}: no execution contract (a workspace '
                     'holding only research records is UNINITIALIZED). Review a declaration with a goal, an '
                     'experiment and a budget, then run: python -B scripts/rds_cli.py --root '
                     f'{_shell_argument(str(store.root))} compass init --declaration <file>. Nothing was initialized or run.')
    require(store.path.is_file(), uninitialized)
    with store._db(True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [name for name in LEDGER_TABLES if name not in tables]
        contract = 'contract' in tables and db.execute('SELECT 1 FROM contract WHERE id=1').fetchone()
        # Execution history: runs, receipts or Compass rows (pre-init tool preparation rows are not).
        probes = {'runs': '', 'receipts': '', 'events': " WHERE json_extract(body,'$.kind') LIKE 'COMPASS_%'"}
        history = any(db.execute(f'SELECT EXISTS(SELECT 1 FROM {name}{where})').fetchone()[0]
                      for name, where in probes.items() if name in tables)
    require(contract or history, uninitialized)
    if missing or not contract:
        # An execution ledger that lost its schema is preserved and diagnosed, never re-initialized over.
        raise CompassIntegrityError(f'Compass execution ledger at {store.path} is damaged: '
                                    + (f'missing tables {", ".join(missing)}' if missing else 'its contract row is missing')
                                    + '; the file is kept as is. Restore it from a backup; do not re-run init over it.')
    return store


def _ref(store, value):
    raw = canonical(value).encode('utf-8')
    require(len(raw) <= MAX_RECORD, 'Compass record exceeds 256 KiB')
    ref = cas_json(store.root, value)
    # Ledger rows keep a root-relative locator: absolute aliases (8.3 names,
    # symlinked temp dirs, mapped drives) must not change a record's identity.
    return {'sha256': ref['sha256'], 'path': Path(ref['path']).resolve().relative_to(store.root.resolve()).as_posix(),
            'bytes': ref['bytes']}


def read_ref(store, ref):
    base = store.root.resolve()
    path = (base / ref['path']).resolve()
    if path.parent != (base / '.rds' / 'cas').resolve() or path.name != ref['sha256'] + '.json':
        raise CompassIntegrityError('Compass record escapes the project CAS')
    raw = path.read_bytes()
    if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise CompassIntegrityError('Compass CAS record integrity failure: ' + ref['sha256'])
    return json.loads(raw.decode('utf-8'))


def _rows(db):
    rows = db.execute("SELECT id, body FROM events WHERE json_extract(body,'$.kind') LIKE 'COMPASS_%' "
                      "ORDER BY id LIMIT ?", (MAX_EVENTS + 1,)).fetchall()
    require(len(rows) <= MAX_EVENTS, 'Compass event limit reached; start a successor campaign')
    return [(row['id'], json.loads(row['body'])) for row in rows]


def events(store, db=None, *, load=True):
    """Verified Compass events in ledger order, each with its CAS record."""
    if db is None:
        with store._db(True) as connection:
            return events(store, connection, load=load)
    out, parent = [], None
    for row_id, body in _rows(db):
        if body.get('parent') != parent:
            raise CompassIntegrityError(f'Compass hash chain broken at event {row_id}')
        if body['record']['sha256'] != body['sha256']:
            raise CompassIntegrityError(f'Compass event/record mismatch at event {row_id}')
        # The row binds itself too: the tail has no successor to carry its digest.
        if body.get('seq') != len(out) + 1 or body.get('row_sha256') != _row_digest(body):
            raise CompassIntegrityError(f'Compass row self-binding failure at event {row_id}')
        parent = digest(body)
        item = {**body, 'event_id': row_id}
        if load:
            item['value'] = read_ref(store, body['record'])
        out.append(item)
    return out


def _row_digest(body):
    return digest({k: v for k, v in body.items() if k != 'row_sha256'})


def append(store, kind, ident, value, *, guard=None):
    """Append one record atomically; an identical (kind, id, content) is reused.

    ``guard(db, events)`` runs inside the write transaction so the caller can
    re-check the state it decided from; a changed state raises before commit.
    """
    require(kind in KINDS, 'Unknown Compass record kind: ' + str(kind))
    require(isinstance(ident, str) and 0 < len(ident) <= 160, 'Invalid Compass record id')
    ref = _ref(store, value)
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = _rows(db)
        for _, body in rows:
            if body['kind'] == PREFIX + kind and body['id'] == ident:
                require(body['sha256'] == ref['sha256'],
                        f'Compass {kind} id {ident} already holds different content')
                return {**body, 'reused': True}
        # Refused before the write: a row past the cap would make every later read fail.
        require(len(rows) < MAX_EVENTS, 'Compass event limit reached; start a successor campaign')
        if guard is not None:
            guard(db, [body for _, body in rows])
        parent = digest(rows[-1][1]) if rows else None
        # ``at`` orders this row against kernel run starts (novel-prediction checks).
        body = {'kind': PREFIX + kind, 'id': ident, 'sha256': ref['sha256'], 'record': ref,
                'parent': parent, 'seq': len(rows) + 1, 'at': time.time()}
        body['row_sha256'] = _row_digest(body)
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(body),))
    return {**body, 'reused': False}


def head(store, db=None):
    """Digest of the last Compass row: the state identity a decision binds."""
    if db is None:
        with store._db(True) as connection:
            return head(store, connection)
    rows = _rows(db)
    return digest(rows[-1][1]) if rows else None


def settlement_order(db):
    """Ledger event id of each run's receipt settlement (ATTEMPT_FINISHED), in the same id space as Compass rows."""
    return {json.loads(row['body'])['run_id']: row['id'] for row in db.execute(
        "SELECT id, body FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' ORDER BY id")}


def receipts(store, db=None):
    """All original run receipts, integrity-checked by the kernel decoder."""
    if db is None:
        with store._db(True) as connection:
            return receipts(store, connection)
    out = {}
    for row in db.execute('SELECT run_id, sha256, body FROM receipts ORDER BY run_id'):
        out[row['run_id']] = ProjectStore._receipt(row)
    return out


def runs(store, db=None):
    if db is None:
        with store._db(True) as connection:
            return runs(store, connection)
    return {run['id']: run for run in ProjectStore._runs(db)}


def recheck(store, receipt):
    """Every artifact a receipt binds (streams, manifest, outputs), re-hashed now; [] when all hold."""
    base, out = store.root.resolve(), []
    for row in receipt.get('artifacts', []):
        path = (store.root / row['path']).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            out.append({'path': row['path'], 'kind': row.get('kind'), 'binding': 'ARTIFACT_MISSING'})
        elif hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            out.append({'path': row['path'], 'kind': row.get('kind'), 'binding': 'ARTIFACT_HASH_MISMATCH'})
    return out


def artifact(store, receipt, relative):
    """Read one declared output exactly as its receipt bound it, or fail closed."""
    rows = [a for a in receipt.get('artifacts', []) if a.get('path') == relative]
    if len(rows) != 1 or not isinstance(rows[0].get('sha256'), str):
        return None, 'OUTPUT_NOT_IN_RECEIPT'
    path = (store.root / relative).resolve()
    if not path.is_relative_to(store.root.resolve()) or not path.is_file():
        return None, 'OUTPUT_MISSING'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != rows[0]['sha256']:
        return None, 'OUTPUT_HASH_MISMATCH'
    return raw, 'BOUND'
