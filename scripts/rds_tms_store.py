"""Program-owned dependency snapshots in the existing project ledger and CAS.

The harness supplies the project root. Models send only a declaration or a
change, and receive an observation; they never carry the support table.
"""
from copy import deepcopy
import json
from pathlib import Path

from rds_hypergraph import ASSURANCE, _validate, review_hypergraph, audit_sources
from rds_math import MAX_BYTES, blob
from rds_project import ProjectStore, canonical, digest, require
from rds_quick import brief, cas_bytes


class SnapshotConflict(ValueError):
    """Another invocation advanced the same project's map before commit."""


def _store(root):
    store = ProjectStore(root)
    require(store.path.resolve().is_relative_to(store.root), 'Dependency ledger escapes project root')
    return store


def _last(db):
    return db.execute('SELECT sha256,body FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()


def current(root):
    """Read one bound snapshot, without scanning or rehashing the whole ledger."""
    store = _store(root)
    if not store.path.exists():
        return None
    with store._db(True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dependency_snapshots'").fetchone():
            return None
        row = _last(db)
    if row is None:
        return None
    require(len(row['body'].encode('utf-8')) <= MAX_BYTES, 'Dependency snapshot exceeds 8 MiB')
    value = json.loads(row['body'])
    require(digest(value) == row['sha256'], 'Dependency snapshot integrity failure')
    raw = blob(store.root, value['map'])
    spec = json.loads(raw)
    _validate(spec)
    return {**value, 'sha256': row['sha256'], 'dependency_map': spec}


def save(root, spec, *, expected, revision=None, source_base=None, validate_current=None):
    """Atomically append a revision; a stale caller cannot overwrite newer input."""
    store = _store(root)
    _validate(spec)
    raw = canonical(spec).encode('utf-8')
    require(len(raw) <= MAX_BYTES, 'Dependency map exceeds 8 MiB')
    ref = cas_bytes(store.root, raw)
    ref['path'] = Path(ref['path']).relative_to(store.root).as_posix()
    source_base = str(Path(source_base or store.root).resolve())
    value = {'parent': expected, 'map': ref, 'source_base_dir': source_base,
             'revision': deepcopy(revision)}
    body = canonical(value)
    require(len(body.encode('utf-8')) <= MAX_BYTES, 'Dependency revision exceeds 8 MiB')
    store.state_dir.mkdir(exist_ok=True)
    with store._db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS dependency_snapshots(sha256 TEXT PRIMARY KEY,body TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS dependency_snapshots_no_update BEFORE UPDATE ON dependency_snapshots
                BEGIN SELECT RAISE(ABORT,'dependency_snapshots is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS dependency_snapshots_no_delete BEFORE DELETE ON dependency_snapshots
                BEGIN SELECT RAISE(ABORT,'dependency_snapshots is append-only'); END;
        """)
        db.execute('BEGIN IMMEDIATE')
        # Program-owned collectors may bind publication to the ledger state
        # they observed. The callback reads this same locked connection; a run
        # cannot commit a newer receipt between validation and map publication.
        if validate_current is not None:
            validate_current(db)
        previous = _last(db)
        previous_sha = previous['sha256'] if previous else None
        if previous:
            old = json.loads(previous['body'])
            require(digest(old) == previous['sha256'], 'Dependency snapshot integrity failure')
            if old['map'] == ref and old['source_base_dir'] == source_base:
                def declarations(rev):
                    return [{k: change.get(k) for k in ('kind', 'id', 'operation', 'status', 'source')}
                            for change in (rev or {}).get('changes', [])]
                # A locked owned collector can reuse one concurrent publication
                # of exactly its map. Do not cross a declaration revision or an
                # intervening map change, even if later content is identical.
                owned_duplicate = (validate_current is not None and revision is None
                                   and old.get('revision') is None and old['parent'] == expected)
                same_head = (previous_sha == expected and
                             (not revision or declarations(revision) == declarations(old.get('revision'))))
                if same_head or owned_duplicate:
                    blob(store.root, old['map'])
                    return previous['sha256']
        if previous_sha != expected:
            raise SnapshotConflict('Current dependency snapshot changed; review the latest map before resubmitting')
        sha = digest(value)
        db.execute('INSERT INTO dependency_snapshots VALUES (?,?)', (sha, body))
    return sha


def maintain(root, *, initial=None, locator='tool-input', source_base=None, audit_files=False,
             format_repairs=(), **changes):
    """Compile, revise, analyze and persist in one application-side tool action."""
    saved = current(root)
    store = _store(root)
    controlled = False
    if store.path.exists():
        with store._db(True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone():
                controlled = 'advisor_policy' in store._contract(db)
    if controlled:
        require(initial is None,
                'Program-owned evidence cannot be replaced by a caller graph; submit proposed changes instead')
    spec = initial if initial is not None else saved['dependency_map'] if saved else {}
    expected = saved['sha256'] if saved else None
    base = source_base or (saved['source_base_dir'] if saved else str(Path(root).resolve()))
    if isinstance(initial, dict) and isinstance(initial.get('dependency_map'), dict):
        base = initial.get('source_base_dir', base)
    result = review_hypergraph(spec, locator=locator, **changes)
    result['input_review']['format_repairs'] = list(format_repairs)
    result['source_base_dir'] = str(Path(base).resolve())
    result['snapshot_sha256'] = expected
    if result['input_review']['errors']:
        return result  # No partial declarations reach the current map.
    if controlled:
        before = saved['dependency_map'] if saved else {'nodes': [], 'hyperedges': [], 'goals': []}
        after = result['dependency_map']
        for kind in ('nodes', 'hyperedges'):
            old = {row['id']: row for row in before[kind]}
            new = {row['id']: row for row in after[kind]}
            require({k: v for k, v in old.items() if k.startswith('owned:')} ==
                    {k: v for k, v in new.items() if k.startswith('owned:')},
                    'Program-owned evidence cannot be changed or removed by a caller')
            require(all(row == old.get(key) or row.get('status') in {'UNKNOWN', 'PROPOSED'}
                        for key, row in new.items() if not key.startswith('owned:')),
                    'Program-owned workflow accepts agent proposals, not handwritten support or refutation')
        require([goal for goal in before.get('goals', []) if str(goal).startswith('owned:')] ==
                [goal for goal in after.get('goals', []) if str(goal).startswith('owned:')],
                'Program-owned goals cannot be removed by a caller')
    if audit_files:
        result['source_file_audit'] = audit_sources(result['dependency_map'], base)
    try:
        result['snapshot_sha256'] = save(root, result['dependency_map'], expected=expected,
                                        revision=result.get('revision'), source_base=base)
    except SnapshotConflict as exc:
        # A stale analysis is not the current project's conclusion.
        return {'status': 'CONFLICT', 'assurance': ASSURANCE, 'authorization': 'UNCHANGED',
                'dependency_map': None, 'input_review': result['input_review'], 'snapshot_sha256': None,
                'next_step': {'action': 'review_current_snapshot', 'reason': str(exc)}}
    return result


def with_saved_dependencies(root, context):
    """Inject program state into the existing Advisor/exec context, outside the LLM."""
    require(isinstance(context, dict) and 'dependency_map' not in context,
            'Use --saved-dependencies or a context dependency_map, not both')
    saved = current(root)
    require(saved is not None, 'No saved dependency map; declare the current dependencies with hypergraph first')
    spec = deepcopy(saved['dependency_map'])
    spec['record_source_base_dir'] = saved['source_base_dir']
    # Preserve source resolution when advice/exec runs from a different cwd.
    for key in ('nodes', 'hyperedges'):
        for record in spec[key]:
            source = record['source']
            if isinstance(source, dict) and 'file' in source and not Path(source['file']).is_absolute():
                source['file'] = str((Path(saved['source_base_dir']) / source['file']).resolve())
    return {**deepcopy(context), 'dependency_map': spec}


def tms_tool(root, *, declaration=None, detail=False, **changes):
    """Provider-independent adapter: root is host context, not a model argument.

    Codex shell/MCP and Pi custom tools can use the same reducer. A Pi wrapper
    puts the brief observation in content and keeps a CAS locator in details.
    No model call, mandatory loop hook or scientific oracle is introduced.
    """
    updates = list(changes.pop('updates', ()))
    if declaration is not None:
        updates.append(declaration)
    result = maintain(root, updates=updates, **changes)
    return result if detail else brief(root, result, 'native-tms')
