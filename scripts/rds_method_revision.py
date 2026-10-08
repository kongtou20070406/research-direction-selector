"""Bounded same-ledger method changes; genesis, attempts and budget stay immutable.

Only the program copies preauthorized code bytes. A durable prepared event and
content-addressed originals let interrupted copies resume without a new ledger.
These records attest file identity and authorization, not scientific success.
"""
from copy import deepcopy
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from rds_project import canonical, digest, require

MAX_BYTES = 2 * 1024 * 1024
PREPARED = 'METHOD_REVISION_PREPARED'
ADOPTED = 'METHOD_REVISION_ADOPTED'


def validate_envelope(store, contract):
    envelope = contract.get('method_evolution')
    if envelope is None:
        return
    require(isinstance(envelope, dict) and set(envelope) == {'schema', 'max_revisions', 'code_paths'},
            'method_evolution requires schema, max_revisions and code_paths')
    require(type(envelope['schema']) is int and envelope['schema'] == 1
            and type(envelope['max_revisions']) is int and 1 <= envelope['max_revisions'] <= 8,
            'method_evolution requires schema 1 and max_revisions in 1..8')
    paths = envelope['code_paths']
    require(isinstance(paths, list) and 1 <= len(paths) <= 64
            and all(isinstance(p, str) for p in paths) and len(set(paths)) == len(paths),
            'method_evolution code_paths must be unique bound code paths')
    resolved = set()
    for path in paths:
        roles = {b['role'] for b in contract['bindings'] if b['path'] == path}
        require(roles == {'code'}, 'Revision code paths must be exclusively code bindings; evaluator/data remain frozen')
        target = store._path(path)
        require(target not in resolved, 'Revision code paths alias another target')
        require(all(b['path'] == path or store._path(b['path']) != target for b in contract['bindings']),
                'Revision code path aliases a frozen binding')
        resolved.add(target)


def _event_hash(event):
    return digest({k: v for k, v in event.items() if k != 'sha256'})


def _cas_dir(db):
    filename = db.execute('PRAGMA database_list').fetchone()[2]
    require(filename, 'Method revision needs a file-backed ledger')
    state = Path(filename).parent.resolve()
    directory = state / 'cas'
    require(directory.resolve().is_relative_to(state), 'Method CAS directory escapes operational state')
    return directory


def _blob(db, sha):
    require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'Invalid method CAS identity')
    path = _cas_dir(db) / (sha + '.method-bytes')
    require(path.resolve().is_relative_to(_cas_dir(db).resolve()), 'Method CAS file escapes its directory')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == sha, 'Method CAS integrity failure')
    return data


def _retain(db, data):
    sha = hashlib.sha256(data).hexdigest()
    path = _cas_dir(db) / (sha + '.method-bytes')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == data, 'Method CAS integrity failure')
    else:
        # Exclusive creation preserves any existing CAS bytes.
        with path.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return sha


def _genesis(db):
    row = db.execute('SELECT body,sha256 FROM contract WHERE id=1').fetchone()
    require(row is not None, 'Project contract is missing')
    value = json.loads(row['body'])
    require(digest(value) == row['sha256'], 'Contract integrity failure')
    return value


def _transition(old, new, changes):
    require(set(old) == set(new), 'Method revision cannot change contract fields')
    for key in set(old) - {'advisor_policy', 'bindings'}:
        require(old[key] == new[key], 'Method revision changes immutable contract field: ' + key)
    require(old.get('method_evolution'), 'Method evolution was not authorized at genesis')
    require(len(old['bindings']) == len(new['bindings']), 'Revision cannot add or remove bindings')
    authorized = set(old['method_evolution']['code_paths'])
    changed_paths = {c['path'] for c in changes}
    require(len(changed_paths) == len(changes), 'Duplicate revision targets')
    for before, after in zip(old['bindings'], new['bindings']):
        require(set(before) == set(after) == {'path', 'role', 'sha256'}
                and before['path'] == after['path'] and before['role'] == after['role'],
                'Revision cannot reorder or change binding identities')
        if before['sha256'] != after['sha256']:
            require(before['path'] in changed_paths and (before['role'] == 'protocol'
                    or (before['role'] == 'code' and before['path'] in authorized)),
                    'Revision cannot change frozen input/evaluator/config bindings')
    require(old.get('advisor_policy', {}).get('context') == new.get('advisor_policy', {}).get('context'),
            'Revision cannot change decision, goals or scope')
    for protected in ('autonomy', 'confirmation'):
        require(old.get('advisor_policy', {}).get(protected) == new.get('advisor_policy', {}).get(protected),
                'Revision cannot change protected controller/confirmation declaration: ' + protected)
    forecast = old.get('advisor_policy', {}).get('feasibility')
    if forecast is not None:
        updated = new.get('advisor_policy', {}).get('feasibility')
        require(isinstance(updated, dict), 'Revision cannot remove the predictive feasibility gate')
        require(updated['max_pilot_wall_seconds'] <= forecast['max_pilot_wall_seconds'],
                'Revision cannot increase the original cumulative pilot allowance')
    for change in changes:
        before = [b for b in old['bindings'] if b['path'] == change['path']]
        after = [b for b in new['bindings'] if b['path'] == change['path']]
        require(len(before) == len(after) == 1 and before[0]['sha256'] == change['before_sha256']
                and after[0]['sha256'] == change['after_sha256'], 'Revision file changes disagree with bindings')


def _lineage(db, genesis=None):
    initial = _genesis(db)
    require(genesis is None or initial == genesis, 'Genesis contract differs')
    history = [{'sha256': digest(initial), 'contract': initial}]
    pending, seen_ids = None, set()
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind') IN (?,?) ORDER BY id",
                      (PREPARED, ADOPTED)).fetchall()
    require(len(rows) <= 16, 'Method revision event limit exceeded')
    for row in rows:
        event = json.loads(row['body'])
        require(event.get('sha256') == _event_hash(event), 'Method revision event integrity failure')
        require(event.get('genesis_sha256') == history[0]['sha256'], 'Method revision genesis mismatch')
        if event['kind'] == PREPARED:
            require(pending is None and event['id'] not in seen_ids, 'Duplicate or overlapping method revision')
            require(event['parent_sha256'] == history[-1]['sha256'], 'Method revision parent mismatch or cycle')
            require(len(history) <= initial.get('method_evolution', {}).get('max_revisions', 0),
                    'Method revision maximum reached')
            require(digest(event['contract']) == event['contract_sha256'], 'Revised contract integrity failure')
            require(event['contract_sha256'] not in {h['sha256'] for h in history}, 'Method revision cycle')
            _transition(history[-1]['contract'], event['contract'], event['changes'])
            for change in event['changes']:
                old_bytes, new_bytes = _blob(db, change['before_sha256']), _blob(db, change['after_sha256'])
                binding = next(b for b in event['contract']['bindings'] if b['path'] == change['path'])
                if binding['role'] == 'protocol':
                    before, after = json.loads(old_bytes.decode('utf-8-sig')), json.loads(new_bytes.decode('utf-8-sig'))
                    require(after == {**before, 'code_sha256': _code_sha(event['contract'])},
                            'Revision must preserve all protocol fields except derived code identity')
            pending = event
            seen_ids.add(event['id'])
        else:
            require(pending is not None and event['id'] == pending['id']
                    and event['prepared_sha256'] == pending['sha256']
                    and event['parent_sha256'] == history[-1]['sha256']
                    and event['contract_sha256'] == pending['contract_sha256']
                    and event['proposal_sha256'] == pending['proposal_sha256'], 'Method adoption lineage mismatch')
            history.append({'sha256': pending['contract_sha256'], 'contract': pending['contract']})
            pending = None
    return history, pending


def contract_history(db, genesis=None):
    """Verified genesis and effective ancestors; never trust snapshot claims alone."""
    return _lineage(db, genesis)[0]


def effective_contract(db):
    return contract_history(db)[-1]['contract']


def pending_revision(db):
    return _lineage(db)[1]


def _code_sha(contract):
    rows = [{'path': b['path'], 'sha256': b['sha256']} for b in contract['bindings'] if b['role'] == 'code']
    return rows[0]['sha256'] if len(rows) == 1 else digest(sorted(rows, key=lambda b: b['path']))


def _idle(store, db):
    require(not any(r['status'] in {'RESERVED', 'RUNNING'} for r in store._runs(db)),
            'Method revision requires no reserved or running attempts')
    from rds_autonomy import controller_reservation
    held = controller_reservation(store, db)
    require(all(abs(r['reserved'] - (held if r['resource'] == 'wall_seconds' else 0.)) < 1e-9
                for r in db.execute('SELECT resource,reserved FROM budget')),
            'Method revision requires no outstanding budget reservations')


def _policy_change(store, db, old, new):
    previous, revised = old.get('advisor_policy'), new.get('advisor_policy')
    require(isinstance(previous, dict) and isinstance(revised, dict), 'Method revision requires an owned policy')
    require(previous['context'] == revised.get('context'), 'Revision cannot change decision, goals or scope')
    registered = {r['id']: r for r in store._runs(db)}
    old_routes = {r['manifest']['id']: r for r in previous['routes']}
    new_routes = {r['manifest']['id']: r for r in revised.get('routes', [])}
    goals = {g['fact'] for g in previous['context']['decision']['goal_conditions']}
    final_observations = [o for o in previous['observations'] if o['fact'] in goals]
    require(final_observations == [o for o in revised.get('observations', []) if o['fact'] in goals],
            'Revision cannot remap final goal evidence or remove its independent producer')
    producers = {o['run_id'] for o in final_observations}
    producers.update(rid for rid in old_routes if any(g.startswith('run.' + rid + '.') for g in goals))
    for rid in producers:
        require(rid in new_routes, 'Revision cannot remove a final verification route')
        before, after = deepcopy(old_routes[rid]), deepcopy(new_routes[rid])
        before['manifest']['protocol'].pop('sha256')
        after['manifest']['protocol'].pop('sha256')
        require(before == after, 'Revision cannot change a final verification operation or allowance')
    for run_id, run in registered.items():
        if run_id in old_routes:
            require(new_routes.get(run_id) == old_routes[run_id]
                    and new_routes[run_id]['manifest'] == run['manifest'],
                    'Registered route manifest is immutable: ' + run_id)
        require([o for o in previous['observations'] if o['run_id'] == run_id]
                == [o for o in revised.get('observations', []) if o['run_id'] == run_id],
                'Registered observations are immutable: ' + run_id)
        require([b for b in previous.get('tool_bindings', []) if b['run_id'] == run_id]
                == [b for b in revised.get('tool_bindings', []) if b['run_id'] == run_id],
                'Registered tool application is immutable: ' + run_id)
    from rds_owned_advisor import validate_policy
    validate_policy(store, new)


def _tool_structure(path, data):
    """Conservative source identity, not a claim that the replacement is useful.

Python whitespace/comments/docstrings, local renaming and numeric tuning alone cannot claim a
new tool. Non-Python code retains byte identity; its pilot must establish its
behavior. This deliberately does not infer algorithmic complexity from source.
"""
    if not path.endswith('.py'):
        return hashlib.sha256(data).hexdigest()
    try:
        tree = ast.parse(data.decode('utf-8-sig'), filename=path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError('Replacement Python tool is not valid UTF-8 source: ' + path) from exc
    local_names = {}
    for node in ast.walk(tree):
        name = (node.id if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) else
                node.arg if isinstance(node, ast.arg) else
                node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None)
        if name is not None and name not in local_names:
            local_names[name] = 'LOCAL_' + str(len(local_names))
    class Structure(ast.NodeTransformer):
        def visit_Name(self, node):
            node.id = local_names.get(node.id, node.id)
            return node
        def visit_arg(self, node):
            node.arg = local_names.get(node.arg, node.arg)
            return self.generic_visit(node)
        def visit_FunctionDef(self, node):
            node.name = local_names.get(node.name, node.name)
            return self.generic_visit(node)
        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        def visit_Constant(self, node):
            if isinstance(node.value, (int, float, complex)) and not isinstance(node.value, bool):
                return ast.copy_location(ast.Constant(value='NUMERIC_TUNING'), node)
            if isinstance(node.value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', node.value):
                return ast.copy_location(ast.Constant(value='NUMERIC_TUNING'), node)
            return node
        def visit_Expr(self, node):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return None
            return self.generic_visit(node)
    return ast.dump(Structure().visit(tree), include_attributes=False)


def _replace(path, data):
    """Atomic replacement at one file boundary; PREPARED handles multiple files."""
    descriptor, temporary = tempfile.mkstemp(prefix='.rds-revision-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(store, proposal, *, admission_guard=None):
    """Validate, durably prepare and adopt an authorized revision, or resume it."""
    require(isinstance(proposal, dict) and set(proposal) == {'id', 'parent_sha256', 'reason', 'policy', 'code_replacements'},
            'Revision requires id, parent_sha256, reason, policy and code_replacements')
    require(isinstance(proposal['id'], str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}', proposal['id']),
            'Invalid revision ID')
    require(isinstance(proposal['reason'], str) and 1 <= len(proposal['reason'].strip()) <= 2048,
            'Revision reason must be nonempty and bounded')
    require(len(canonical(proposal).encode('utf-8')) <= MAX_BYTES, 'Revision proposal exceeds limit')
    proposal_sha = digest(proposal)
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        history, pending = _lineage(db)
        previous = history[-1]['contract']
        adopted = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? AND json_extract(body,'$.id')=?",
                             (ADOPTED, proposal['id'])).fetchone()
        if adopted:
            event = json.loads(adopted['body'])
            require(event['proposal_sha256'] == proposal_sha, 'Revision ID reused with changed proposal')
            _, errors = store._bindings(previous)
            require(not errors, '; '.join(errors))
            return {**event, 'status': 'ALREADY_ADOPTED', 'execution_started': False}
        if admission_guard is not None:
            admission_guard(db)
        _idle(store, db)
        if pending:
            require(pending['id'] == proposal['id'] and pending['proposal_sha256'] == proposal_sha,
                    'A prepared revision must be resumed with its exact proposal')
        else:
            validate_envelope(store, previous)
            require(previous.get('method_evolution'), 'Method evolution was not authorized at genesis')
            require(len(history) <= previous['method_evolution']['max_revisions'], 'Method revision maximum reached')
            require(proposal['parent_sha256'] == history[-1]['sha256'], 'Revision parent differs from effective contract')
            _, errors = store._bindings(previous)
            require(not errors, '; '.join(errors))
            replacements = proposal['code_replacements']
            require(isinstance(replacements, list) and len(replacements) <= len(previous['method_evolution']['code_paths']),
                    'Invalid code replacements')
            revised = deepcopy(previous)
            revised['advisor_policy'] = deepcopy(proposal['policy'])
            material, targets = [], set()
            for replacement in replacements:
                require(isinstance(replacement, dict) and set(replacement) == {'path', 'source', 'sha256'},
                        'Invalid code replacement')
                path = replacement['path']
                require(path in previous['method_evolution']['code_paths'] and path not in targets,
                        'Code replacement path was not authorized or is duplicated')
                require(isinstance(replacement['source'], str), 'Replacement source must name a project path')
                relative = replacement['source'].replace('\\', '/')
                target = store._path(path)
                if relative.startswith('.rds/tool-workbench/'):
                    from rds_tool_workbench import resolve_source
                    source = resolve_source(store, relative)
                else:
                    source = store._path(replacement['source'])
                require(source != target, 'Revision source must be separate; in-place caller edits are forbidden')
                data = source.read_bytes()
                require(len(data) <= MAX_BYTES and hashlib.sha256(data).hexdigest() == replacement['sha256'],
                        'Replacement source hash mismatch or size limit exceeded')
                binding = next(b for b in revised['bindings'] if b['path'] == path)
                targets.add(path)
                if binding['sha256'] != replacement['sha256']:
                    material.append((path, target.read_bytes(), data))
                    binding['sha256'] = replacement['sha256']
            # Identity protocols are program-derived, never caller-submitted.
            for binding in revised['bindings']:
                if binding['role'] != 'protocol':
                    continue
                require(sum(b['path'] == binding['path'] for b in revised['bindings']) == 1,
                        'Protocol revision cannot change an alias of frozen data/config/evaluator')
                raw = store._path(binding['path']).read_bytes()
                protocol = json.loads(raw.decode('utf-8-sig'))
                updated = {**protocol, 'code_sha256': _code_sha(revised)}
                if updated != protocol:
                    data = canonical(updated).encode('utf-8')
                    material.append((binding['path'], raw, data))
                    binding['sha256'] = hashlib.sha256(data).hexdigest()
            registered = {r['id'] for r in store._runs(db)}
            protocols = {b['path']: b['sha256'] for b in revised['bindings'] if b['role'] == 'protocol'}
            for route in revised['advisor_policy']['routes']:
                if route['manifest']['id'] not in registered:
                    ref = route['manifest']['protocol']
                    require(ref['path'] in protocols, 'Route protocol is not bound')
                    ref['sha256'] = protocols[ref['path']]
            changes = [{'path': p, 'before_sha256': hashlib.sha256(a).hexdigest(),
                        'after_sha256': hashlib.sha256(b).hexdigest()} for p, a, b in material]
            require(any(p in previous['method_evolution']['code_paths'] and _tool_structure(p, a) != _tool_structure(p, b)
                        for p, a, b in material),
                    'Method revision needs a new or improved bound tool; names, metadata and numeric tuning alone are insufficient')
            _transition(previous, revised, changes)
            _policy_change(store, db, previous, revised)
            require(digest(revised) not in {h['sha256'] for h in history}, 'Method revision makes no change or cycles')
            # All checks above precede CAS writes and any bound-file mutation.
            for _, before, after in material:
                _retain(db, before)
                _retain(db, after)
            pending = {'kind': PREPARED, 'id': proposal['id'], 'genesis_sha256': history[0]['sha256'],
                       'parent_sha256': history[-1]['sha256'], 'proposal_sha256': proposal_sha,
                       'reason': proposal['reason'], 'contract': revised, 'contract_sha256': digest(revised),
                       'changes': changes, 'prepared_at': time.time()}
            pending['sha256'] = _event_hash(pending)
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical(pending),))
    # PREPARED has committed before any copies, allowing recovery after a kill.
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        history, retained = _lineage(db)
        # Another exact-proposal caller may have completed the prepared copy.
        if retained is None:
            event = json.loads(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? AND json_extract(body,'$.id')=?",
                                         (ADOPTED, proposal['id'])).fetchone()['body'])
            require(event['proposal_sha256'] == proposal_sha, 'Revision ID reused with changed proposal')
            return {**event, 'status': 'ALREADY_ADOPTED', 'execution_started': False}
        require(retained == pending, 'Prepared revision changed before adoption')
        if admission_guard is not None:
            admission_guard(db)
        _idle(store, db)
        changes = {c['path']: c for c in pending['changes']}
        # Verify every target and unaffected input before writing any target.
        for binding in history[-1]['contract']['bindings']:
            actual = hashlib.sha256(store._path(binding['path']).read_bytes()).hexdigest()
            allowed = {binding['sha256']}
            if binding['path'] in changes:
                allowed.add(changes[binding['path']]['after_sha256'])
            require(actual in allowed, 'Prepared revision input changed: ' + binding['path'])
        for change in pending['changes']:
            target = store._path(change['path'])
            data = _blob(db, change['after_sha256'])
            if hashlib.sha256(target.read_bytes()).hexdigest() != change['after_sha256']:
                _replace(target, data)
        _, errors = store._bindings(pending['contract'])
        require(not errors, '; '.join(errors))
        event = {'kind': ADOPTED, 'id': proposal['id'], 'genesis_sha256': history[0]['sha256'],
                 'parent_sha256': pending['parent_sha256'], 'prepared_sha256': pending['sha256'],
                 'proposal_sha256': proposal_sha, 'contract_sha256': pending['contract_sha256'],
                 'adopted_at': time.time()}
        event['sha256'] = _event_hash(event)
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
    return {**event, 'status': 'ADOPTED', 'execution_started': False}
