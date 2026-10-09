"""Explicit workspace ownership by one original project ledger.

Markers contain identity pointers only. They neither copy research state nor
protect against direct SQL, backup rollback, or same-user removal of markers.
SQLite access here is deliberately raw to avoid recursion through store guards.
"""
from contextlib import contextmanager
from collections import deque
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

from rds_project import canonical, digest, require
from rds_source_documents import strict_json

MARKER = '.rds-campaign.json'
ENVIRONMENT = 'RDS_CAMPAIGN_BINDING'
KIND = 'CAMPAIGN_LEDGER_BOUND'
QUICK_JOB_KIND = 'QUICK_JOB_ADMITTED'
MAX_BYTES = 16384
MAX_JOBS = 128
MAX_RUNS = 4096
MAX_THEORY_EVENTS = 2 * MAX_RUNS
MAX_EVENTS = 512
MAX_DIRECTORIES = 4096
MAX_ENTRIES = 32768
MAX_DEPTH = 32
FIELDS = {'schema', 'kind', 'binding_id', 'workspace_root', 'project_root',
          'ledger_kind', 'genesis_sha256', 'event_sha256'}


def _path(value):
    path = Path(value)
    require(path.is_absolute(), 'Campaign binding paths must be absolute')
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError('Campaign binding path cannot be resolved') from exc


def _shape(value):
    require(isinstance(value, dict) and set(value) == FIELDS, 'Malformed campaign binding fields')
    require(type(value['schema']) is int and value['schema'] == 1 and value['kind'] == KIND
            and value['ledger_kind'] == 'project', 'Unsupported campaign binding schema/kind')
    require(isinstance(value['binding_id'], str) and re.fullmatch('[0-9a-f]{32}', value['binding_id']),
            'Invalid campaign binding nonce')
    for name in ('genesis_sha256', 'event_sha256'):
        require(isinstance(value[name], str) and re.fullmatch('[0-9a-f]{64}', value[name]),
                'Invalid campaign binding digest')
    for name in ('workspace_root', 'project_root'):
        require(isinstance(value[name], str) and 0 < len(value[name]) <= 4096,
                'Invalid campaign binding path')
        require(os.path.normcase(str(_path(value[name]))) == os.path.normcase(value[name]),
                'Campaign binding paths must be canonical')
    workspace, project = _path(value['workspace_root']), _path(value['project_root'])
    require(workspace.is_dir() and project.is_dir() and project.is_relative_to(workspace),
            'Campaign workspace/project is missing or outside its workspace')
    require(digest({key: field for key, field in value.items() if key != 'event_sha256'})
            == value['event_sha256'], 'Campaign event digest mismatch')
    return value


def _decode(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'Campaign binding exceeds 16 KiB')
    try:
        return _shape(strict_json(raw.decode('utf-8-sig')))
    except (UnicodeError, RecursionError, TypeError, KeyError) as exc:
        raise ValueError('Malformed campaign binding JSON') from exc


def _read(path):
    try:
        with path.open('rb') as stream:
            return _decode(stream.read(MAX_BYTES + 1))
    except OSError as exc:
        raise ValueError('Campaign binding marker is missing or unreadable: ' + str(path)) from exc


@contextmanager
def _database(project, *, write=False):
    project = Path(project).resolve()
    path = project / '.rds' / 'project.sqlite3'
    require(path.is_file() and path.resolve().is_relative_to(project), 'Original campaign project ledger is missing')
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        if not write:
            db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield db
        if write:
            db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _lineage(db):
    from rds_method_revision import contract_history
    try:
        return contract_history(db)
    except sqlite3.Error as exc:
        raise ValueError('Original campaign project contract is missing or damaged') from exc


def _event(db):
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? LIMIT 2", (KIND,)).fetchall()
    require(len(rows) <= 1, 'Conflicting native campaign binding events')
    return _decode(rows[0]['body'].encode('utf-8')) if rows else None


def _verify(value):
    try:
        with _database(value['project_root']) as db:
            require(_lineage(db)[0]['sha256'] == value['genesis_sha256'], 'Campaign genesis contract changed')
            require(_event(db) == value, 'Original campaign nonce event is missing or changed')
    except (sqlite3.Error, OSError, KeyError, TypeError, RecursionError) as exc:
        raise ValueError('Original campaign ledger is unavailable or damaged') from exc


def _resolve(root, required):
    """Resolve every applicable marker; conflicts never fall back to UNBOUND."""
    root = Path(root).resolve()
    paths = [candidate / MARKER for candidate in (root, *root.parents)
             if (candidate / MARKER).exists() or (candidate / MARKER).is_symlink()]
    if required is not None:
        require(bool(required), 'Required campaign binding path is empty')
        paths.append(_path(required))
    value, selected = None, None
    for path in paths:
        current = _read(path)
        _verify(current)
        require(value is None or value == current, 'Conflicting campaign bindings')
        value, selected = current, path.resolve()
    return {**value, 'binding_path': str(selected)} if value is not None else None


def binding(root):
    """Resolve markers and a required host pointer without any fallback."""
    return _resolve(root, os.environ.get(ENVIRONMENT))


def enforce(root, kind='project'):
    """A binding owns one project DB, including refusal of reference DB writes."""
    value = binding(root)
    if value is not None:
        require(kind == 'project' and Path(root).resolve() == _path(value['project_root']),
                'Campaign requires its canonical project ledger: ' + value['project_root'])
    return value


def detached_admission(root, db):
    """Check a detached job under its original parent's write transaction.

    The native intent is already authoritative before marker publication.
    Callers must retain this transaction through child admission and record the
    actual job pointer before releasing it; this function is not a new mutex.
    """
    require(db.in_transaction, 'Detached admission requires its parent transaction')
    require(binding(root) is None,
            'Bound campaign refuses detached QUICK jobs; use the canonical project')
    events = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
    require(not events or _event(db) is None,
            'Campaign binding intent refuses detached QUICK admission')


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class _Inventory:
    def __init__(self):
        self.directories = self.entries = self.events = self.runs = self.theory_events = 0

    def directory(self, depth):
        self.directories += 1
        require(depth <= MAX_DEPTH and self.directories <= MAX_DIRECTORIES,
                'Campaign workspace directory/depth bound exceeded; inventory is incomplete')

    def entry(self):
        self.entries += 1
        require(self.entries <= MAX_ENTRIES, 'Campaign workspace entry bound exceeded; inventory is incomplete')


def _workspace_roots(workspace, inventory):
    """Inspect directories only, with explicit whole-operation bounds."""
    pending, visited = deque([(workspace, 0)]), set()
    while pending:
        directory, depth = pending.popleft()
        directory = directory.resolve()
        require(directory.is_relative_to(workspace), 'Campaign workspace directory escapes its scope')
        if directory in visited:
            continue
        visited.add(directory)
        inventory.directory(depth)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    inventory.entry()
                    path = Path(entry.path)
                    if entry.is_dir():
                        if entry.name == '.rds':
                            # Discover through the logical owner before resolve
                            # changes an in-root symlink/junction's basename.
                            for name in ('project.sqlite3', 'state.sqlite3'):
                                ledger = path / name
                                if ledger.exists() or ledger.is_symlink():
                                    require(ledger.is_file() and ledger.resolve().is_relative_to(directory),
                                            'Campaign native ledger is unavailable or escapes its owner')
                                    yield directory, name
                        pending.append((path, depth + 1))
        except OSError as exc:
            raise ValueError('Campaign workspace inventory is unreadable or incomplete') from exc


def _preparation_finished(root, value, target):
    """A terminal child alone does not settle the original preparation writer."""
    from rds_math import get, blob
    from rds_project import ProjectStore
    request = value.get('request')
    require(isinstance(request, dict) and value.get('request_sha256') == digest(request)
            and isinstance(request.get('token'), str) and re.fullmatch('[0-9a-f]{32}', request['token'])
            and request.get('validation_id') == 'validation:' + request['token'],
            'Retained tool preparation identity is invalid')
    record = get(root, request['validation_id'])
    require(record is not None and record.get('kind') == 'tool-validation',
            'Campaign binding requires resolved tool preparation')
    data = record.get('data', {})
    require(isinstance(data, dict) and isinstance(data.get('job_root'), str)
            and (root / data['job_root']).resolve() == target and data.get('run_id') == 'tool-check',
            'Retained tool preparation record points to another job')
    with _database(target) as db:
        row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', ('tool-check',)).fetchone()
        require(row is not None, 'Campaign binding requires resolved tool preparation receipt')
        receipt = ProjectStore._receipt(row)
    require(data.get('receipt_sha256') == digest(receipt)
            and strict_json(blob(root, record['asset']).decode('utf-8')) == {'receipt': receipt},
            'Retained tool preparation record differs from its original receipt')


def _retained_targets(root, db, inventory):
    if _table(db, 'events'):
        remaining = MAX_EVENTS - inventory.events
        rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind') IN "
                          "('EXTERNAL_RUN_ALLOWANCE','TOOL_PREPARATION_STARTED','QUICK_JOB_ADMITTED') LIMIT ?",
                          (remaining + 1,)).fetchall()
        inventory.events += len(rows)
        require(inventory.events <= MAX_EVENTS, 'Campaign retained-event bound exceeded; inventory is incomplete')
        for row in rows:
            require(len(row['body'].encode('utf-8')) <= MAX_BYTES, 'Retained job event exceeds 16 KiB')
            value = strict_json(row['body'])
            require(isinstance(value, dict), 'Retained job event is invalid')
            request = value.get('request', {})
            require(isinstance(request, dict), 'Retained job request is invalid')
            job = value.get('job_root') or request.get('job_root')
            require(isinstance(job, str) and job, 'Retained child job pointer is missing')
            target = (root / job).resolve()
            # Only this old allowance shape names a source workspace. Native
            # pointers and ordinary allowances already name the actual ledger.
            token = target.name
            legacy = (value['kind'] == 'EXTERNAL_RUN_ALLOWANCE'
                      and re.fullmatch('[0-9a-f]{32}', token)
                      and target.parent.name == 'tool-checks' and target.parent.parent.name == 'rsi'
                      and target.parent.parent.parent.name == '.rds'
                      and value.get('request_sha256') == digest({'tool_validation': token}))
            if legacy:
                target = target / '.rds' / 'exec' / 'tool-check'
            if value['kind'] == 'TOOL_PREPARATION_STARTED':
                _preparation_finished(root, value, target)
            yield target
    directory = root / '.rds' / 'exec'
    if directory.exists() or directory.is_symlink():
        require(directory.is_dir() and directory.resolve().is_relative_to(root),
                'Retained QUICK directory escapes its original project')
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    inventory.entry()
                    target = Path(entry.path).resolve()
                    require(entry.is_dir() and target.is_relative_to(root),
                            'Retained QUICK job is unavailable or escapes its original project')
                    yield target
        except OSError as exc:
            raise ValueError('Campaign retained-job inventory is unreadable or incomplete') from exc


def _theory_quiescent(db, inventory):
    if not _table(db, 'events'):
        return
    names = ('attempt_id', 'run_id', 'manifest_sha256', 'request_sha256', 'accounting', 'allowance')
    # Project only the original identity: outcomes may also retain large worker
    # results, which are unnecessary to detect unfinished allowance writers.
    fields = ','.join("json_extract(body,'$." + name + "') AS " + name for name in ('kind',) + names)
    rows = db.execute('SELECT ' + fields + " FROM events WHERE json_extract(body,'$.kind') IN "
                      "('THEORY_ALLOWANCE','THEORY_OUTCOME') LIMIT ?",
                      (MAX_THEORY_EVENTS - inventory.theory_events + 1,)).fetchall()
    inventory.theory_events += len(rows)
    require(inventory.theory_events <= MAX_THEORY_EVENTS,
            'Campaign theory-event bound exceeded; inventory is incomplete')
    allowances, outcomes = {}, {}
    for row in rows:
        identity = {name: row[name] for name in names}
        require(all(isinstance(identity[name], str) and 0 < len(identity[name]) <= MAX_BYTES for name in names),
                'Theory allowance/outcome identity is invalid')
        identity['allowance'] = strict_json(identity['allowance'])
        target = allowances if row['kind'] == 'THEORY_ALLOWANCE' else outcomes
        require(identity['attempt_id'] not in target, 'Theory allowance/outcome attempt is duplicated')
        target[identity['attempt_id']] = identity
    require(allowances == outcomes, 'Campaign binding requires resolved theory allowances with exact outcomes')


def _ledger_quiescent(root, db, inventory):
    from rds_project import ProjectStore, TERMINAL
    if _table(db, 'contract') and db.execute('SELECT 1 FROM contract WHERE id=1').fetchone():
        _lineage(db)
    _theory_quiescent(db, inventory)
    if _table(db, 'runs'):
        inventory.runs += db.execute('SELECT count(*) FROM runs').fetchone()[0]
        require(inventory.runs <= MAX_RUNS,
                'Campaign retained-run bound exceeded; inventory is incomplete')
        runs = ProjectStore._runs(db)
        require(all(run['status'] in TERMINAL for run in runs),
                'Campaign binding requires no active runs or reserved resources; settled retained child jobs required')
        for run in runs:
            row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run['id'],)).fetchone()
            receipt = ProjectStore._receipt(row) if row is not None else None
            require(receipt is not None and receipt['run_id'] == run['id']
                    and receipt['attempt_id'] == run['attempt_id']
                    and receipt['manifest_sha256'] == run['manifest_sha256']
                    and receipt['process_status'] == run['status'],
                    'Retained child has no verified terminal receipt')
    if _table(db, 'budget'):
        require(not db.execute('SELECT 1 FROM budget WHERE reserved!=0 LIMIT 1').fetchone(),
                'Retained child has reserved resources')


def _reference_quiescent(root):
    from rds_cli import RDSState
    path = root / '.rds' / 'state.sqlite3'
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        db.execute('PRAGMA query_only=ON')
        row = db.execute('SELECT body FROM state WHERE id=1').fetchone()
        require(row is not None and len(row[0].encode('utf-8')) <= 8 * 1024 * 1024,
                'Campaign reference ledger state is missing or exceeds its bound')
        state = RDSState.read_state(db)
        require(isinstance(state, dict) and isinstance(state.get('plans'), dict), 'Campaign reference state is invalid')
        RDSState.invariants(state)
        require(all(amount == 0 for amount in state['budget']['reserved'].values()),
                'Campaign reference ledger has reserved resources')
        require(all(isinstance(plan, dict) and plan.get('run_status') not in {'RESERVED', 'RUNNING', 'RECOVERY_REQUIRED'}
                    for plan in state['plans'].values()), 'Campaign binding requires settled reference plans')
    finally:
        db.close()


def _quiescent(db, project, workspace=None):
    inventory, pending, visited = _Inventory(), deque([project]), set()
    for root, name in _workspace_roots(workspace or project, inventory):
        if name == 'project.sqlite3':
            pending.append(root)
        else:
            _reference_quiescent(root)
    while pending:
        root = pending.popleft().resolve()
        if root in visited:
            continue
        visited.add(root)
        require(len(visited) <= MAX_JOBS + 1, 'Campaign retained-job bound exceeded; inventory is incomplete')
        if root == project:
            _ledger_quiescent(root, db, inventory)
            pending.extend(_retained_targets(root, db, inventory))
        else:
            with _database(root) as child:
                _ledger_quiescent(root, child, inventory)
                pending.extend(_retained_targets(root, child, inventory))


def _publish(path, value):
    """Hard-link a complete same-directory file; never overwrite another marker."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.rds-campaign-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((canonical(value) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(_read(path) == value, 'Campaign marker already has a different binding')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def bind(store, workspace):
    """Commit an append-once native identity, then publish its exact marker."""
    from rds_mutation import mutation
    with mutation():
        return _bind(store, workspace)


def _bind(store, workspace):
    project, workspace = Path(store.root).resolve(), Path(workspace).resolve()
    require(workspace.is_dir() and project.is_dir() and project.is_relative_to(workspace),
            'Campaign workspace must be an existing ancestor of its project')
    path = workspace / MARKER
    required = os.environ.get(ENVIRONMENT)
    repair = False
    if required is not None:
        require(bool(required), 'Required campaign binding path is empty')
        required_path = _path(required)
        if not required_path.exists() and not required_path.is_symlink():
            require(required_path == path, 'Missing required campaign marker differs from requested workspace')
            repair = True
    existing = _resolve(project, None) if repair else binding(project)
    if existing is not None:
        require(_path(existing['project_root']) == project and _path(existing['workspace_root']) == workspace,
                'Project is already bound to another campaign workspace')
    with _database(project, write=True) as db:
        lineage = _lineage(db)
        value = _event(db)
        require(not repair or value is not None,
                'Required campaign marker is missing or unreadable; original binding event is required for repair')
        # A committed intent without its marker may have been interrupted.
        # Recheck pending work before recovering publication as well.
        if existing is None:
            _quiescent(db, project, workspace)
        if value is None:
            event = {'schema': 1, 'kind': KIND, 'binding_id': uuid.uuid4().hex,
                     'workspace_root': str(workspace), 'project_root': str(project),
                     'ledger_kind': 'project', 'genesis_sha256': lineage[0]['sha256']}
            value = {**event, 'event_sha256': digest(event)}
            _shape(value)
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical(value),))
        else:
            require(_path(value['project_root']) == project and _path(value['workspace_root']) == workspace
                    and value['genesis_sha256'] == lineage[0]['sha256'],
                    'Native campaign event belongs to another identity')
    _publish(path, value)
    return {**value, 'binding_path': str(path.resolve())}
