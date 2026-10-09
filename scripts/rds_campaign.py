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
MAX_RECORDS = 2048
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
        self.directories = self.entries = self.events = self.runs = self.theory_events = self.records = 0

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
                        if os.path.normcase(entry.name) == os.path.normcase('.rds'):
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


def _retained_targets(root, db, inventory, *, workspace):
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
            target = target.resolve()
            require(target.is_relative_to(workspace),
                    'Retained campaign job escapes the selected workspace; bind a common ancestor')
            if value['kind'] == 'TOOL_PREPARATION_STARTED':
                _preparation_finished(root, value, target)
            _retained_job(target)
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
                    _retained_job(target)
                    yield target
        except OSError as exc:
            raise ValueError('Campaign retained-job inventory is unreadable or incomplete') from exc


def _retained_job(root):
    """A retained admission cannot be settled by vacuous all([]).

    Check each pointer before traversal deduplication: a job may have already
    appeared as an ordinary empty workspace project in the same inventory.
    Original terminal receipts and costs are then checked by the normal walk.
    """
    with _database(root) as db:
        _lineage(db)
        require(_table(db, 'runs') and db.execute('SELECT 1 FROM runs LIMIT 1').fetchone(),
                'Retained job requires an original run and verified terminal receipt')


def _guard_quiescent(root, db, contract, *, workspace=None):
    """Require the native QUICK guard's original immutable tail, not PASS."""
    from rds_guard import read
    bindings = [entry for entry in contract['bindings']
                if entry['path'] == 'rds-exec-request.json' and entry['role'] == 'config']
    if not bindings:
        return
    require(len(bindings) == 1, 'Retained QUICK request binding is ambiguous')
    request_path = (root / bindings[0]['path']).resolve()
    require(request_path.is_relative_to(root), 'Retained QUICK request escapes its original project')
    request, request_sha = read(request_path)
    require(request_sha == bindings[0]['sha256'] and isinstance(request, dict),
            'Retained QUICK request differs from its original binding')
    guard = request.get('guard')
    if guard is None:
        _prospective_quiescent(root, db, request, workspace=workspace)
        return
    require(isinstance(guard, dict) and isinstance(guard.get('path'), str), 'Retained QUICK guard identity is invalid')
    policy = [entry for entry in contract['bindings'] if entry['path'] == guard['path']]
    require(policy and len({entry['sha256'] for entry in policy}) == 1,
            'Retained QUICK guard has no original policy binding')
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' LIMIT 2").fetchall()
    require(len(rows) == 1, 'Campaign binding requires resolved guard review')
    event = strict_json(rows[0]['body'])
    ref = event.get('report') if isinstance(event, dict) else None
    require(isinstance(ref, dict) and isinstance(ref.get('path'), str)
            and isinstance(ref.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', ref['sha256'])
            and type(ref.get('bytes')) is int, 'Guard report binding is invalid')
    path = (root / ref['path']).resolve()
    require(path.parent == (root / '.rds/cas').resolve() and path.name == ref['sha256'] + '.json',
            'Guard report is outside its original CAS')
    report, report_sha = read(path)
    require(report_sha == ref['sha256'] and path.stat().st_size == ref['bytes'],
            'Guard report CAS integrity failure')
    require(isinstance(report, dict) and report.get('status') in {'PASS', 'FAIL', 'UNKNOWN'}
            and type(report.get('promotion_eligible')) is bool,
            'Guard report is not a completed original review')
    # QUICK records evaluator errors as UNKNOWN without a policy digest. That
    # is a settled error, not a successful scientific or regression verdict.
    require(report.get('policy_sha256') == policy[0]['sha256']
            or (report.get('status') == 'UNKNOWN' and report.get('promotion_eligible') is False
                and report.get('scientific_support') == 'UNKNOWN'
                and isinstance(report.get('reason'), str)
                and 'policy_sha256' not in report), 'Guard report differs from its frozen policy')
    _prospective_quiescent(root, db, request, report, ref, workspace=workspace)


def _prospective_quiescent(root, db, request, regression=None, guard_ref=None, *, workspace=None):
    if request.get('research_context') is None:
        return
    from rds_project import ProjectStore, TERMINAL
    from rds_quick import _prospective_completion
    context = request['research_context']
    require(isinstance(context, dict) and isinstance(context.get('ledger'), str),
            'Prospective campaign parent ledger is invalid')
    if workspace is not None:
        # Campaign publication owns this explicit global scope. Activation
        # also uses the settlement reader, without publishing a workspace.
        parent = Path(context['ledger'])
        require(parent.is_absolute() and parent.resolve().is_relative_to(workspace),
                'Prospective campaign parent ledger escapes the selected workspace; bind a common ancestor')
    require(db.execute('SELECT count(*) FROM runs').fetchone()[0] == 1,
            'Prospective QUICK settlement requires exactly one original run')
    runs = ProjectStore._runs(db)
    require(len(runs) == 1 and runs[0]['status'] in TERMINAL,
            'Prospective QUICK settlement requires its original terminal run')
    row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (runs[0]['id'],)).fetchone()
    receipt = ProjectStore._receipt(row) if row is not None else None
    require(receipt is not None and receipt['attempt_id'] == runs[0]['attempt_id']
            and receipt['manifest_sha256'] == runs[0]['manifest_sha256']
            and receipt['process_status'] == runs[0]['status'],
            'Prospective QUICK settlement requires its original terminal receipt')
    completion = _prospective_completion(root, request, receipt, regression=regression, guard_ref=guard_ref)
    require(completion is not None and completion['after_checkpoint'] is not None,
            'Campaign binding requires resolved prospective QUICK checkpoint settlement')


def _json_cas(root, ref):
    from rds_math import read_bytes
    require(isinstance(ref, dict) and isinstance(ref.get('path'), str)
            and isinstance(ref.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', ref['sha256'])
            and type(ref.get('bytes')) is int, 'Native settlement report binding is invalid')
    path = (root / ref['path']).resolve()
    require(path.parent == (root / '.rds/cas').resolve() and path.name == ref['sha256'] + '.json',
            'Native settlement report is outside its original CAS')
    raw = read_bytes(path)
    from hashlib import sha256
    require(sha256(raw).hexdigest() == ref['sha256'] and len(raw) == ref['bytes'],
            'Native settlement report CAS integrity failure')
    return strict_json(raw.decode('utf-8-sig'))


def _native_tails_quiescent(root, db, contract, runs, receipts):
    """Foreign producers must settle their original native obligations.

    A canonical project retains these exact recovery APIs after binding. A
    terminal worker is only one part of the producer; no successful business
    verdict, fresh model request, or current-budget fingerprint is required.
    """
    from rds_project import ProjectStore
    store = ProjectStore(root)
    policy = contract.get('advisor_policy', {})
    if policy.get('autonomy'):
        from rds_autonomy import records, REQUESTED, PROCESSED
        events = records(store, db, contract)
        require({event['run_id'] for event in events if event['kind'] == REQUESTED}
                == {event['run_id'] for event in events if event['kind'] == PROCESSED},
                'Campaign binding requires processed original model outcomes')
    if not policy or not receipts:
        return
    from rds_owned_advisor import _state, OWNED_PREFIX
    # Validate original owned history/attempt/completion identities, without
    # treating mutable controller accounting as a new collection obligation.
    _state(store, db)
    last_finished = 0
    for receipt in receipts:
        rows = db.execute("SELECT id FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                          "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=? LIMIT 2",
                          (receipt['run_id'], receipt['sha256'])).fetchall()
        require(len(rows) == 1, 'Owned Advisor settlement requires original completion identity')
        last_finished = max(last_finished, rows[0]['id'])
    row = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')='OWNED_ADVISOR_REVIEW' "
                     'ORDER BY id DESC LIMIT 1').fetchone()
    require(row is not None and row['id'] > last_finished,
            'Campaign binding requires completed owned Advisor review settlement')
    require(len(row['body'].encode('utf-8')) <= MAX_BYTES, 'Owned Advisor review event exceeds its bound')
    event = strict_json(row['body'])
    report = _json_cas(root, event.get('report'))
    require(isinstance(report, dict) and report.get('fingerprint') == event.get('fingerprint')
            and isinstance(event.get('fingerprint'), str) and re.fullmatch('[0-9a-f]{64}', event['fingerprint'])
            and report.get('status') == event.get('status')
            and report.get('selected_run') == event.get('selected_run')
            and report.get('snapshot_sha256') == event.get('snapshot_sha256')
            and report.get('coverage', {}).get('runs') == len(runs)
            and report.get('coverage', {}).get('receipts') == len(receipts),
            'Owned Advisor settlement report differs from its original event/receipt inventory')
    require(_table(db, 'dependency_snapshots'), 'Owned Advisor settlement has no original dependency snapshot')
    row = db.execute('SELECT sha256,body FROM dependency_snapshots WHERE sha256=?',
                     (event['snapshot_sha256'],)).fetchone()
    from rds_math import MAX_BYTES as SNAPSHOT_BYTES, blob
    require(row is not None and len(row['body'].encode('utf-8')) <= SNAPSHOT_BYTES,
            'Owned Advisor settlement dependency snapshot is missing or exceeds its bound')
    snapshot = strict_json(row['body'])
    require(digest(snapshot) == row['sha256'], 'Owned Advisor settlement snapshot integrity failure')
    graph = strict_json(blob(root, snapshot['map']).decode('utf-8-sig'))
    from rds_hypergraph import _validate
    _validate(graph)
    nodes = {node['id']: node for node in graph['nodes']}
    edges = {edge['id']: edge for edge in graph['hyperedges']}
    for receipt, run in zip(receipts, runs):
        require(receipt['run_id'] == run['id'], 'Owned Advisor settlement run/receipt inventory differs')
        run_node, receipt_node = nodes.get(OWNED_PREFIX + 'run:' + run['id']), nodes.get(OWNED_PREFIX + 'receipt:' + run['id'])
        edge = edges.get(OWNED_PREFIX + 'completion:' + run['id'])
        source = {'locator': 'owned receipt ' + receipt['sha256']}
        require(isinstance(run_node, dict) and run_node.get('status') == 'SUPPORTED'
                and run_node.get('manifest_sha256') == run['manifest_sha256']
                and run_node.get('lifecycle_status') == run['status']
                and isinstance(receipt_node, dict) and receipt_node.get('status') == 'SUPPORTED'
                and receipt_node.get('receipt_sha256') == receipt['sha256']
                and receipt_node.get('outcome') == receipt['run_status'] and receipt_node.get('source') == source
                and isinstance(edge, dict) and edge.get('status') == 'SUPPORTED' and edge.get('source') == source
                and edge.get('premises') == [run_node['id']] and edge.get('conclusion') == receipt_node['id'],
                'Owned Advisor settlement dependency map differs from original terminal receipts')
    return report


def retained_owned_review(store):
    """Read the settled foreign review; never collect through another owner."""
    scope = binding(store.root)
    if scope is None or store.root == Path(scope['project_root']):
        return None
    from rds_project import TERMINAL
    with store._db(True) as db:
        db.execute('BEGIN')
        contract = store._contract(db)
        runs = store._runs(db)
        require(all(run['status'] in TERMINAL for run in runs),
                'Foreign owned recovery requires original terminal work')
        receipts = [store._receipt(row) for row in db.execute('SELECT run_id,sha256,body FROM receipts ORDER BY run_id')]
        require(len(receipts) == len(runs) and all(
            receipt['run_id'] == run['id'] and receipt['attempt_id'] == run['attempt_id']
            and receipt['manifest_sha256'] == run['manifest_sha256']
            and receipt['process_status'] == run['status']
            for receipt, run in zip(receipts, runs)),
            'Foreign owned recovery requires its complete original terminal receipt inventory')
        return _native_tails_quiescent(store.root, db, contract, runs, receipts)


def _intent_quiescent(root, db, project, workspace):
    if not _table(db, 'events'):
        return
    value = _event(db)
    if value is None:
        return
    require(_path(value['project_root']) == root and _lineage(db)[0]['sha256'] == value['genesis_sha256'],
            'Retained campaign binding intent has changed its original ledger identity')
    scope = _path(value['workspace_root'])
    overlap = scope.is_relative_to(workspace) or workspace.is_relative_to(scope)
    require(not overlap or (root == project and scope == workspace),
            'Conflicting foreign campaign binding intent; recover its original marker first')


def _comparisons_quiescent(root, db, inventory):
    """Every original prospective comparison must have its bound native result."""
    if not _table(db, 'research_records'):
        return
    rows = db.execute('SELECT id FROM research_records LIMIT ?',
                      (MAX_RECORDS - inventory.records + 1,)).fetchall()
    inventory.records += len(rows)
    require(inventory.records <= MAX_RECORDS,
            'Campaign research-record bound exceeded; inventory is incomplete')
    from rds_math import records, get, blob
    from rds_tool_compare import _observation
    values = records(root)
    for plan in values:
        data = plan.get('data', {})
        if not isinstance(data, dict) or data.get('type') != 'tool-comparison-plan':
            continue
        request = data.get('request')
        require(plan.get('kind') == 'note' and isinstance(request, dict)
                and plan['id'] == 'comparison-plan:' + digest(request)[:32]
                and blob(root, plan['asset']) == canonical(data).encode('utf-8'),
                'Original comparison plan identity is invalid')
        result = get(root, 'comparison:' + digest(request)[:32])
        require(result is not None and result.get('kind') == 'note'
                and result.get('data', {}).get('type') == 'tool-comparison-result'
                and result['data'].get('plan_id') == plan['id']
                and blob(root, result['asset']) == canonical(result['data']).encode('utf-8'),
                'Campaign binding requires resolved immutable comparison result')
        dependencies = [{'id': plan['id'], 'sha256': digest(plan)}]
        for arm in ('baseline', 'candidate'):
            candidate = get(root, request[arm]['id'])
            require(candidate is not None and candidate['kind'] == 'tool'
                    and digest(candidate) == request[arm]['sha256'],
                    'Original comparison tool binding changed')
            observation = result['data'].get(arm)
            require(isinstance(observation, dict) and isinstance(observation.get('validation_id'), str),
                    'Comparison result has no settled native validation')
            checked = _observation(root, candidate, {'id': observation['validation_id']}, plan)
            require(checked == observation, 'Comparison result differs from its original native receipt')
            dependencies.append({'id': observation['validation_id'], 'sha256': observation['validation_sha256']})
        require(result.get('dependencies') == sorted(dependencies, key=lambda value: value['id']),
                'Comparison result dependencies differ from its original plan and validations')


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


def _ledger_quiescent(root, db, inventory, *, workspace, allow_pending_revision=False, canonical_project=False):
    from rds_project import ProjectStore, TERMINAL
    contract = None
    if _table(db, 'contract') and db.execute('SELECT 1 FROM contract WHERE id=1').fetchone():
        from rds_method_revision import pending_revision
        lineage = _lineage(db)
        require(allow_pending_revision or pending_revision(db) is None,
                'Campaign binding requires resolved method revision')
        contract = lineage[-1]['contract']
        _guard_quiescent(root, db, contract, workspace=workspace)
    _comparisons_quiescent(root, db, inventory)
    _theory_quiescent(db, inventory)
    if _table(db, 'runs'):
        inventory.runs += db.execute('SELECT count(*) FROM runs').fetchone()[0]
        require(inventory.runs <= MAX_RUNS,
                'Campaign retained-run bound exceeded; inventory is incomplete')
        runs = ProjectStore._runs(db)
        require(all(run['status'] in TERMINAL for run in runs),
                'Campaign binding requires no active runs or reserved resources; settled retained child jobs required')
        receipts = []
        for run in runs:
            row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run['id'],)).fetchone()
            receipt = ProjectStore._receipt(row) if row is not None else None
            require(receipt is not None and receipt['run_id'] == run['id']
                    and receipt['attempt_id'] == run['attempt_id']
                    and receipt['manifest_sha256'] == run['manifest_sha256']
                    and receipt['process_status'] == run['status'],
                    'Retained child has no verified terminal receipt')
            receipts.append(receipt)
        if contract is not None and not canonical_project:
            _native_tails_quiescent(root, db, contract, runs, receipts)
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
    workspace = (workspace or project).resolve()
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
            _intent_quiescent(root, db, project, workspace)
            _ledger_quiescent(root, db, inventory, workspace=workspace, allow_pending_revision=True, canonical_project=True)
            pending.extend(_retained_targets(root, db, inventory, workspace=workspace))
        else:
            with _database(root) as child:
                _intent_quiescent(root, child, project, workspace)
                _ledger_quiescent(root, child, inventory, workspace=workspace)
                pending.extend(_retained_targets(root, child, inventory, workspace=workspace))


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
