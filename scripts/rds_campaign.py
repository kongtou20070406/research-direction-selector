"""Explicit workspace ownership by one original project ledger.

Markers contain identity pointers only. They neither copy research state nor
protect against direct SQL, backup rollback, or same-user removal of markers.
SQLite access here is deliberately raw to avoid recursion through store guards.
"""
from contextlib import contextmanager
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


def _terminal_child(root):
    """Retained jobs must be settled before their future writers are restricted."""
    from rds_project import ProjectStore, TERMINAL
    with _database(root) as db:
        _lineage(db)
        runs = ProjectStore._runs(db)
        # A fully initialized but never reserved child is harmless retained
        # preparation, including admission refused during marker publication.
        require(all(run['status'] in TERMINAL for run in runs),
                'Campaign binding requires settled retained child jobs')
        require(not db.execute('SELECT 1 FROM budget WHERE reserved!=0 LIMIT 1').fetchone(),
                'Retained child has reserved resources')
        for run in runs:
            row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run['id'],)).fetchone()
            require(row is not None and ProjectStore._receipt(row)['run_id'] == run['id'],
                    'Retained child has no verified terminal receipt')


def _quiescent(db, project):
    require(not db.execute("SELECT 1 FROM runs WHERE status IN ('RESERVED','RUNNING') LIMIT 1").fetchone()
            and not db.execute('SELECT 1 FROM budget WHERE reserved!=0 LIMIT 1').fetchone(),
            'Campaign binding requires no active runs or reserved resources')
    jobs = []
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind') IN "
                      "('EXTERNAL_RUN_ALLOWANCE','TOOL_PREPARATION_STARTED','QUICK_JOB_ADMITTED') LIMIT ?",
                      (MAX_JOBS + 1,)).fetchall()
    require(len(rows) <= MAX_JOBS, 'Campaign retained-job bound exceeded')
    for row in rows:
        value = strict_json(row['body'])
        job = value.get('job_root') or value.get('request', {}).get('job_root')
        require(isinstance(job, str) and job, 'Retained child job pointer is missing')
        target = (project / job).resolve()
        # Legacy tool allowance names its source workspace, not its final job.
        native = target / '.rds' / 'exec' / 'tool-check'
        if native.is_dir():
            target = native
        jobs.append(target)
    # Plain QUICK predates parent pointer events. Its retained native ledgers
    # still own reservations/receipts, including a interrupted admission whose
    # child committed before the parent's pointer transaction did.
    directory = project / '.rds' / 'exec'
    if directory.exists():
        require(directory.is_dir() and directory.resolve().is_relative_to(project),
                'Retained QUICK directory escapes its original project')
        with os.scandir(directory) as entries:
            for entry in entries:
                require(len(jobs) < MAX_JOBS, 'Campaign retained-job bound exceeded')
                target = Path(entry.path).resolve()
                require(entry.is_dir() and target.is_relative_to(project),
                        'Retained QUICK job is unavailable or escapes its original project')
                jobs.append(target)
    for job in set(jobs):
        _terminal_child(job)


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
    project, workspace = Path(store.root).resolve(), Path(workspace).resolve()
    require(workspace.is_dir() and project.is_dir() and project.is_relative_to(workspace),
            'Campaign workspace must be an existing ancestor of its project')
    existing = binding(project)
    if existing is not None:
        require(_path(existing['project_root']) == project and _path(existing['workspace_root']) == workspace,
                'Project is already bound to another campaign workspace')
    with _database(project, write=True) as db:
        lineage = _lineage(db)
        value = _event(db)
        # A committed intent without its marker may have been interrupted.
        # Recheck pending work before recovering publication as well.
        if existing is None:
            _quiescent(db, project)
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
    path = workspace / MARKER
    _publish(path, value)
    return {**value, 'binding_path': str(path.resolve())}
