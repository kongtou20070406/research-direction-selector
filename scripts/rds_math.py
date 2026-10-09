"""Native objective and research-asset records, using the existing project ledger.

Records bind original bytes and declared dependencies; storage is not proof.
No MRS installation, automatic import or second database is required.
"""
import hashlib
import json
from pathlib import Path
import re

from rds_project import ProjectStore, canonical, digest, require
from rds_quick import cas_bytes
from rds_mutation import mutation

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 2048
FIELDS = {'statement', 'domain', 'quantifier_order', 'assumptions', 'evidence_standard', 'completion_standard'}
KINDS = {'lemma', 'algebraic-root', 'geometry', 'note', 'tool', 'tool-validation', 'tool-adoption', 'refutation'}


def read_bytes(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, 'Research asset exceeds 8 MiB')
    return raw


def objective_spec(raw):
    require(len(raw) <= 64 * 1024, 'Objective declaration exceeds 64 KiB')
    from rds_cli import strict_json
    spec = strict_json(raw.decode('utf-8-sig'))
    require(isinstance(spec, dict) and set(spec) == FIELDS | {'schema', 'question_id', 'goal_revision', 'scope'},
            'Objective needs schema, question_id, goal_revision, scope and all six mathematical fields')
    require(spec['schema'] == 'rds-objective-v1', 'Unsupported objective schema')
    for key in ('statement', 'domain', 'evidence_standard', 'completion_standard', 'question_id', 'goal_revision'):
        require(isinstance(spec[key], str) and spec[key].strip(), 'Objective needs nonempty ' + key)
    for key in ('quantifier_order', 'assumptions'):
        require(isinstance(spec[key], list) and all(isinstance(v, str) and v.strip() for v in spec[key]),
                'Objective needs a string list for ' + key)
    require(isinstance(spec['scope'], dict), 'Objective scope must be a declared object')
    canonical(spec)
    return spec


def _identity(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value),
            'Record identity must contain 1–128 safe characters')
    return value


def _store(root):
    store = ProjectStore(root)
    require(store.path.resolve().is_relative_to(store.root), 'Research ledger escapes project root')
    return store


def records(root):
    store = _store(root)
    if not store.path.exists():
        return []
    with store._db(True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_records'").fetchone():
            return []
        rows = db.execute('SELECT sha256,body FROM research_records ORDER BY rowid LIMIT ?', (MAX_RECORDS + 1,)).fetchall()
    require(len(rows) <= MAX_RECORDS, 'Research ledger exceeds its 2048-record bound')
    values = []
    for row in rows:
        value = json.loads(row['body'])
        require(digest(value) == row['sha256'], 'Research record integrity failure')
        values.append(value)
    return values


def blob(root, ref):
    require(isinstance(ref, dict) and isinstance(ref.get('sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', ref['sha256']), 'Invalid research blob binding')
    base = Path(root).resolve()
    path = (base / ref['path']).resolve()
    require(path.parent == (base / '.rds' / 'cas').resolve()
            and path.name == ref['sha256'] + '.bin', 'Research blob is outside its CAS')
    raw = read_bytes(path)
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'] and len(raw) == ref['bytes'],
            'Research blob integrity failure')
    return raw


def get(root, record_id):
    _identity(record_id)
    store = _store(root)
    if not store.path.exists():
        return None
    with store._db(True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_records'").fetchone():
            return None
        row = db.execute('SELECT sha256,body FROM research_records WHERE id=?', (record_id,)).fetchone()
    if row is None:
        return None
    value = json.loads(row['body'])
    require(digest(value) == row['sha256'] and value['id'] == record_id, 'Research record integrity failure')
    blob(root, value['asset'])
    if value['kind'] == 'tool':
        blob(root, value['data']['origin'])
    return value


def objective(root):
    value = get(root, 'objective')
    if value is not None:
        spec = objective_spec(blob(root, value['asset']))
        require(spec == value['data'], 'Objective declaration differs from original bytes')
    return value


def put(root, record_id, kind, raw, *, dependencies=(), data=None):
    _identity(record_id)
    require(kind in KINDS | {'objective'}, 'Unsupported research asset kind')
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'Invalid research asset bytes')
    dependencies = list(dependencies)
    require(len(dependencies) <= 64 and len(set(dependencies)) == len(dependencies), 'At most 64 distinct dependencies')
    bound = []
    for record in dependencies:
        require(record != record_id, 'A research asset cannot depend on itself')
        parent = get(root, record)
        require(parent is not None, 'Missing research dependency: ' + record)
        bound.append({'id': record, 'sha256': digest(parent)})
    goal = objective(root) if kind != 'objective' else None
    # One bounded publication: binding cannot intervene after the original
    # asset bytes are visible and before their native record commits.
    with mutation():
        ref = cas_bytes(root, raw)
        ref['path'] = Path(ref['path']).relative_to(Path(root).resolve()).as_posix()
        value = {'schema': 'rds-research-record-v1', 'id': record_id, 'kind': kind, 'asset': ref,
                 'objective_sha256': goal['asset']['sha256'] if goal else None,
                 'dependencies': sorted(bound, key=lambda v: v['id']), 'data': data or {},
                 'mathematical_status': 'UNKNOWN', 'research_policy_gain_measured': False}
        store = _store(root)
        store.state_dir.mkdir(exist_ok=True)
        with store._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS research_records(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS research_records_no_update BEFORE UPDATE ON research_records
            BEGIN SELECT RAISE(ABORT,'research_records are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS research_records_no_delete BEFORE DELETE ON research_records
            BEGIN SELECT RAISE(ABORT,'research_records are append-only'); END;
        """)
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT sha256,body FROM research_records WHERE id=?', (record_id,)).fetchone()
            if existing:
                require(digest(json.loads(existing['body'])) == existing['sha256'], 'Research record integrity failure')
                require(existing['sha256'] == digest(value), 'Record is immutable; use a new explicit identity or project')
            else:
                require(db.execute('SELECT COUNT(*) FROM research_records').fetchone()[0] < MAX_RECORDS,
                        'Research ledger is full; preserve it and choose an explicit continuation')
                db.execute('INSERT INTO research_records VALUES (?,?,?)', (record_id, digest(value), canonical(value)))
    return value


def bind_objective(root, raw):
    spec = objective_spec(raw)
    previous = objective(root)
    require(previous is not None or not records(root), 'Bind the objective before native assets; use an explicit new project')
    if previous is None and _store(root).path.exists():
        with _store(root)._db(True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone():
                row = db.execute('SELECT body FROM contract WHERE id=1').fetchone()
                require(row is None, 'An existing operational contract cannot be retroactively objective-bound; use an explicit new project')
    return put(root, 'objective', 'objective', raw, data=spec)


def check_context(root, context):
    goal = objective(root)
    if goal is None:
        return
    require(isinstance(context, dict), 'Research context must be an object')
    decision = context.get('decision', {})
    require(isinstance(decision, dict), 'Research decision must be an object')
    spec = goal['data']
    binding = {'question_id': spec['question_id'], 'goal_revision': spec['goal_revision'], 'sha256': goal['asset']['sha256']}
    require('objective_binding' not in context or context['objective_binding'] == binding,
            'Research context differs from the frozen objective')
    if decision.get('id') == spec['question_id']:
        require(decision.get('goal_revision') == spec['goal_revision'], 'Research context differs from the frozen objective')
    scope = context.get('scope', decision.get('scope', {}))
    require(isinstance(scope, dict) and all(k not in scope or scope[k] == v for k, v in spec['scope'].items()),
            'Research scope conflicts with the frozen objective')
    return binding


def affected(root, record_id):
    seed = get(root, record_id)
    require(seed is not None, 'Unknown research asset')
    entries = records(root)
    dependents = {}
    for value in entries:
        for dep in value['dependencies']:
            dependents.setdefault(dep['id'], set()).add(value['id'])
    seen, pending = {record_id}, [record_id]
    while pending:
        for child in dependents.get(pending.pop(), set()) - seen:
            seen.add(child)
            pending.append(child)
    return {'status': 'REVIEW_REQUIRED', 'id': record_id, 'affected': sorted(seen - {record_id}),
            'meaning': 'Declared dependency lineage needs review; alternative proofs and mathematical falsity are not decided'}


def command(args):
    if args.action == 'bind':
        value = bind_objective(args.root, read_bytes(args.objective))
    elif args.action == 'add':
        require(objective(args.root) is not None, 'Bind a mathematical objective first')
        value = put(args.root, args.id, args.kind, read_bytes(args.file), dependencies=args.depends)
    elif args.action == 'get':
        value = get(args.root, args.id)
        require(value is not None, 'Unknown research record')
    elif args.action == 'affected':
        return affected(args.root, args.id)
    elif args.action == 'refute':
        require(get(args.root, args.id) is not None, 'Unknown research record')
        evidence = read_bytes(args.evidence)
        token = digest({'id': args.id, 'reason': args.reason, 'evidence': hashlib.sha256(evidence).hexdigest()})[:24]
        value = put(args.root, 'refutation:' + token, 'refutation', evidence, dependencies=[args.id],
                    data={'target': args.id, 'reason': args.reason, 'verdict': 'DECLARED_REFUTATION'})
        return {'status': 'DECLARED_REFUTATION', 'record': value, 'review': affected(args.root, args.id)}
    else:
        goal = objective(args.root)
        entries = records(args.root)
        return {'status': 'BOUND' if goal else 'UNBOUND', 'objective': goal, 'records': len(entries),
                'mathematical_status': 'UNKNOWN', 'research_policy_gain_measured': False}
    return {'status': 'BOUND' if value['kind'] == 'objective' else 'RECORDED_UNVERIFIED', 'id': value['id'],
            'source_sha256': value['asset']['sha256'], 'record_sha256': digest(value), 'research_record': value}
