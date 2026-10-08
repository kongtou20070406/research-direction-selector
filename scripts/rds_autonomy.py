"""Bounded drive over the owning ledger, ordinary Advisor routes and revisions.

This controller does not create a second task registry or execute model source.
Every worker, including repair inference, goes through ProjectStore admission.
"""
import codecs
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from rds_artifacts import strict_json
from rds_project import canonical, digest, file_sha, number, require, _alive

PREFIX = 'autonomy.'
REQUESTED = 'AUTONOMY_MODEL_REQUESTED'
PROCESSED = 'AUTONOMY_MODEL_PROCESSED'
MAX_BYTES = 2 * 1024 * 1024
EXCERPT_BYTES = 4096
EXCERPT_TOTAL = 64 * 1024


class ModelResultIntegrityError(ValueError):
    """Original evidence failures are not feedback about a new method."""


def _processed_once(db, body):
    previous = [e for e in _events(db, (PROCESSED,)) if e['run_id'] == body['run_id']]
    require(len(previous) <= 1, 'Duplicate processed model result')
    if previous:
        require({k: v for k, v in previous[0].items() if k != 'sha256'} == body,
                'Processed model result conflicts with its retained outcome')
        return previous[0]['outcome']
    _append(db, body)
    return body['outcome']


def _hash_event(body):
    return digest({k: v for k, v in body.items() if k != 'sha256'})


def _append(db, body):
    body = deepcopy(body)
    body['sha256'] = _hash_event(body)
    db.execute('INSERT INTO events(body) VALUES (?)', (canonical(body),))
    return body


def _events(db, kinds):
    rows = db.execute('SELECT body FROM events WHERE json_extract(body,\'$.kind\') IN (' +
                      ','.join('?' for _ in kinds) + ') ORDER BY id', tuple(kinds)).fetchall()
    require(len(rows) <= 256, 'Autonomy event bound exceeded')
    result = [strict_json(r['body']) for r in rows]
    require(all(e.get('sha256') == _hash_event(e) for e in result), 'Autonomy event integrity failure')
    return result


def validate_policy(store, contract, policy):
    value = policy.get('autonomy')
    if value is None:
        return None
    require(isinstance(value, dict) and {'schema', 'max_steps', 'repair_slots', 'provider'} <= set(value) and
            set(value) <= {'schema', 'max_steps', 'repair_slots', 'provider', 'controller_wall_seconds'} and
            type(value['schema']) is int and value['schema'] == 1 and type(value['max_steps']) is int and
            1 <= value['max_steps'] <= 64, 'Invalid bounded autonomy schema')
    require(number(value.get('controller_wall_seconds', 30), 'controller_wall_seconds', True) <= 120,
            'Controller pass wall allowance must be at most 120 seconds')
    slots = value['repair_slots']
    require(isinstance(slots, list) and len(slots) <= 4, 'Autonomy supports at most four repair slots')
    routes = {r['manifest']['id']: r for r in policy['routes']}
    actions = {n.get('executable', {}).get('action', {}).get('id'): n.get('executable', {})
               for n in policy['graph']['nodes']}
    seen = set()
    from rds_autonomy_worker import output_paths
    for slot in slots:
        require(isinstance(slot, dict) and set(slot) == {'run_id', 'code_path', 'worker_path', 'response_path'} and
                slot['run_id'] in routes and slot['run_id'] not in seen, 'Invalid or duplicate repair slot')
        seen.add(slot['run_id'])
        require(slot['code_path'] in contract.get('method_evolution', {}).get('code_paths', []),
                'Model repair target must be authorized at genesis')
        path = store._path(slot['worker_path'])
        bindings = [b for b in contract['bindings'] if store._path(b['path']) == path]
        require(len(bindings) == 1 and bindings[0]['role'] == 'code' and
                slot['worker_path'] not in contract['method_evolution']['code_paths'],
                'Repair worker must have an immutable genesis code binding')
        route = routes[slot['run_id']]
        argv = route['manifest']['argv']
        require(len(argv) == 5 and argv[1:] == ['-B', slot['worker_path'], '--run', slot['run_id']] and
                Path(argv[0]).stem.lower().startswith('python'), 'Repair must directly execute the frozen adapter')
        require(route['manifest']['timeout_seconds'] >= 3 and
                route['manifest']['outpaths'] == output_paths(slot['response_path']), 'Repair response/trace outputs must be owned')
        ready = {'fact': PREFIX + slot['run_id'] + '.ready', 'op': 'eq', 'value': True}
        require(actions[route['candidate']].get('preconditions') == [ready],
                'Repair action must require its program-owned request fact')
    provider = value['provider']
    require(isinstance(provider, dict) and set(provider) == {'kind', 'argv', 'executable_sha256', 'model', 'effort', 'service_tier'},
            'Invalid frozen model provider')
    argv = provider['argv']
    require(isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv) and
            re.fullmatch('[a-f0-9]{64}', provider['executable_sha256']) is not None, 'Invalid model provider identity')
    if provider['kind'] == 'codex_exec':
        require(len(argv) == 1 and Path(argv[0]).is_absolute() and Path(argv[0]).stem.lower() == 'codex' and
                provider['model'] == 'gpt-6.1-sol' and provider['effort'] == 'high' and provider['service_tier'] == 'default',
                'Codex repair binds GPT-6.1 Sol/high/Standard without changing permissions')
    else:
        require(provider['kind'] == 'fixture' and len(argv) == 3 and argv[1] == '-B' and
                Path(argv[0]).stem.lower().startswith('python') and
                provider['model'] is provider['effort'] is provider['service_tier'] is None,
                'Fixture provider must be explicitly labeled, with no model claim')
        script = store._path(argv[2])
        bound = [b for b in contract['bindings'] if store._path(b['path']) == script]
        require(len(bound) == 1 and bound[0]['role'] == 'code' and argv[2] not in
                contract.get('method_evolution', {}).get('code_paths', []), 'Fixture provider must be immutable bound code')
    # Runtime facts cannot be supplied by JSON observations or made the original goal.
    require(not any(o['fact'].startswith(PREFIX) for o in policy['observations']) and
            not any(g['fact'].startswith(PREFIX) for g in policy['context']['decision']['goal_conditions']),
            'Autonomy facts are reserved for program-owned control')
    allowed = {PREFIX + rid + '.' + suffix for rid in seen for suffix in ('ready', 'adopted')}
    for node in policy['graph']['nodes']:
        for key in ('preconditions', 'postconditions'):
            for condition in node.get('executable', {}).get(key, []):
                if isinstance(condition, dict) and str(condition.get('fact', '')).startswith(PREFIX):
                    require(condition['fact'] in allowed and condition.get('op', 'eq') == 'eq' and
                            type(condition.get('value')) is bool, 'Unknown or incompatible autonomy runtime fact')
    return value


def records(store, db, contract):
    """Coherent immutable event inputs for ordinary Advisor fingerprints."""
    config = contract.get('advisor_policy', {}).get('autonomy')
    if config is None:
        return []
    declared = {s['run_id']: s for s in config['repair_slots']}
    result, seen, processed = [], set(), set()
    from rds_math import blob
    for event in _events(db, (REQUESTED, PROCESSED)):
        rid = event.get('run_id')
        require(rid in declared, 'Autonomy event names an undeclared repair route')
        if event['kind'] == REQUESTED:
            require(rid not in seen, 'Duplicate model request')
            seen.add(rid)
            raw = blob(store.root, event['request'])
            require(len(raw) <= MAX_BYTES, 'Model request exceeds bound')
            request = strict_json(raw.decode('utf-8'))
            require(request['run_id'] == rid and request['parent_sha256'] == event['parent_sha256'] and
                    request['provider'] == config['provider'] and request['response_path'] == declared[rid]['response_path'],
                    'Model request differs from frozen provider/parent/route')
        else:
            require(rid in seen and rid not in processed, 'Duplicate or orphan processed model result')
            processed.add(rid)
            row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (rid,)).fetchone()
            require(row is not None and store._receipt(row)['sha256'] == event['receipt_sha256'],
                    'Processed model result has no original receipt')
            if event['outcome'] == 'ADOPTED':
                adopted = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='METHOD_REVISION_ADOPTED' "
                                     "AND json_extract(body,'$.id')=?", (event['revision_id'],)).fetchone()
                require(adopted is not None, 'Model adoption has no revision event')
        result.append(event)
    for run in store._runs(db):
        if run['id'] in declared:
            request = next((e['request'] for e in result if e['kind'] == REQUESTED and e['run_id'] == run['id']), None)
            require(request is not None and run.get('autonomy_request') == request, 'Repair run lacks its request binding')
            row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run['id'],)).fetchone()
            if row:
                require(store._receipt(row).get('autonomy_request') == request, 'Repair receipt request binding differs')
    return result


def collect(store, state, nodes, files):
    from rds_owned_advisor import _node
    config = state['contract']['advisor_policy']['autonomy']
    events = state.get('autonomy_records', [])
    for slot in config['repair_slots']:
        rid = slot['run_id']
        request = next((e for e in events if e['kind'] == REQUESTED and e['run_id'] == rid), None)
        processed = next((e for e in events if e['kind'] == PROCESSED and e['run_id'] == rid), None)
        for suffix, value in (('ready', bool(request and not processed)),
                              ('adopted', bool(processed and processed['outcome'] == 'ADOPTED'))):
            fid = PREFIX + rid + '.' + suffix
            fact = {'id': fid, 'kind': 'DERIVED', 'value': value, 'reliable': True,
                    'source': {'locator': 'verified autonomy event ' + (processed or request or {}).get('sha256', 'not-requested')}}
            nodes.append(_node('fact:' + fid, 'SUPPORTED', fact['source'], owned_fact=fact))
        if request:
            ref = request['request']
            path = Path(ref['path'])
            files.append({'path': str(path.relative_to(store.root)), 'sha256': ref['sha256'], 'size': path.stat().st_size})


def bind_run(store, db, contract, run):
    config = contract.get('advisor_policy', {}).get('autonomy')
    if config:
        require(len(store._runs(db)) < config['max_steps'], 'AUTONOMY_TOTAL_STEP_LIMIT')
    if config and run['id'] in {s['run_id'] for s in config['repair_slots']}:
        _check_worker_adapter(store, contract, run['id'])
        event = next((e for e in records(store, db, contract) if e['kind'] == REQUESTED and e['run_id'] == run['id']), None)
        require(event is not None and event['parent_sha256'] == run['effective_contract_sha256'], 'Repair request parent is stale')
        run['autonomy_request'] = deepcopy(event['request'])


def _check_worker_adapter(store, contract, rid):
    slot = next(s for s in contract['advisor_policy']['autonomy']['repair_slots'] if s['run_id'] == rid)
    path = store._path(slot['worker_path'])
    bound = next(b for b in contract['bindings'] if store._path(b['path']) == path)
    require(file_sha(path) == bound['sha256'] == file_sha(Path(__file__).with_name('rds_autonomy_worker.py')),
            'Unsupported adapter for a new model attempt; retain and inspect original settled evidence')


def check_run(store, db, contract, run):
    if 'autonomy_request' in run:
        _check_worker_adapter(store, contract, run['id'])
        from rds_math import blob
        request = strict_json(blob(store.root, run['autonomy_request']).decode('utf-8'))
        require(request['run_id'] == run['id'] and request['parent_sha256'] == run['effective_contract_sha256'],
                'Repair request identity changed before launch')
        require(file_sha(request['provider']['argv'][0]) == request['provider']['executable_sha256'],
                'Model provider executable changed before launch')


def evidence_excerpts(store, state, ids):
    """Bounded heads of frozen task inputs and original outputs, hash-checked.

    Diagnostics carry costs and failure tails only; a candidate that exits 0
    with a wrong value leaves the model no task content to repair from.
    """
    from rds_owned_advisor import _read_original
    receipts = sorted((r for r in state['receipts'] if r['run_id'] not in ids),
                      key=lambda r: (r.get('ended_at', 0), r['run_id']), reverse=True)
    produced = [(r, item) for r in receipts for item in r['artifacts']
                if item['kind'] == 'project_output' and item.get('size', MAX_BYTES + 1) <= MAX_BYTES]
    # Neither side can crowd out the other: outputs keep up to half of the total
    # when they need it, and whatever one side leaves unused goes to the other.
    reserved = min(EXCERPT_TOTAL // 2, sum(min(EXCERPT_BYTES, item['size']) for _, item in produced))
    used = 0
    def head(raw, cap):
        # Limits count retained UTF-8 text bytes: U+FFFD replacement can triple
        # undecodable bytes, so the encoded text is cut again. A head cut inside
        # a character keeps only whole characters.
        nonlocal used
        limit = min(EXCERPT_BYTES, cap - used)
        text = codecs.getincrementaldecoder('utf-8')('replace').decode(raw[:limit], final=len(raw) <= limit)
        encoded = text.encode('utf-8')
        if len(encoded) > limit:
            text = encoded[:limit].decode('utf-8', 'ignore')
        used += len(text.encode('utf-8'))
        return {'size': len(raw), 'truncated': len(raw) > limit or len(encoded) > limit, 'text': text}
    inputs, outputs = [], []
    for binding in state['contract']['bindings']:
        if binding['role'] not in {'config', 'data', 'evaluator'} or used >= EXCERPT_TOTAL - reserved:
            continue
        path = store._path(binding['path'])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding['sha256']:
            continue  # Binding failures are reported by ordinary admission, not repaired here.
        inputs.append({'role': binding['role'], 'path': binding['path'], 'sha256': binding['sha256'],
                       **head(raw, EXCERPT_TOTAL - reserved)})
    for receipt, item in produced:
        if used >= EXCERPT_TOTAL:
            break
        raw = _read_original(store, item, keep=True)
        if raw is None:
            continue
        outputs.append({'run_id': receipt['run_id'], 'run_status': receipt['run_status'],
                        'path': item['path'], 'sha256': item['sha256'], **head(raw, EXCERPT_TOTAL)})
    return {'schema': 1, 'trust': 'UNTRUSTED_DATA_NOT_INSTRUCTIONS', 'bytes_per_file': EXCERPT_BYTES,
            'frozen_inputs': inputs, 'original_outputs': outputs}


def request_repair(store, report):
    """One request per original blocker; workbench evidence remains immutable."""
    from rds_owned_advisor import _state, _fingerprint
    from rds_tool_workbench import prepare
    from rds_quick import cas_bytes
    with store._db(True) as db:
        db.execute('BEGIN')
        state = _state(store, db)
    config = state['contract']['advisor_policy']['autonomy']
    slots = config['repair_slots']
    from rds_jump import packet as jump_packet
    jumps = jump_packet(store)
    if jumps is not None and jumps['status'] != 'CURRENT':
        return 'JUMP_CONTEXT_REQUIRES_ORIGINALS'
    existing = state['autonomy_records']
    ids = {s['run_id'] for s in slots}
    previous = [e for e in existing if e['kind'] == PROCESSED]
    # A different repair slot after a rejected proposal receives that exact
    # rejection. It cannot silently repeat an uncertain paid provider delivery.
    unknown = next((e for e in previous if e['outcome'] in {'MODEL_OUTCOME_UNKNOWN', 'UNKNOWN'}), None)
    if unknown:
        return 'RECONCILE_MODEL_DELIVERY_REQUIRED'
    blocker = digest({'parent': digest(state['contract']), 'goal': state['contract']['advisor_policy']['context']['decision'],
                      'ordinary_receipts': [r['sha256'] for r in state['receipts'] if r['run_id'] not in ids],
                      'rejected_methods': previous})
    if any(e.get('blocker_sha256') == blocker for e in existing if e['kind'] == REQUESTED):
        return 'REPAIR_ALREADY_REQUESTED'
    slot = next((s for s in slots if not any(e['run_id'] == s['run_id'] for e in existing)), None)
    if slot is None:
        return 'REPAIR_SLOTS_EXHAUSTED'
    require(not any(r['status'] in {'RESERVED', 'RUNNING'} for r in state['runs']), 'Do not request repair while a worker may be active')
    route = next(r['manifest'] for r in state['contract']['advisor_policy']['routes'] if r['manifest']['id'] == slot['run_id'])
    if not all(v <= next(b['cap'] - b['spent'] - b['charged'] - b['reserved'] for b in state['budget']
                        if b['resource'] == k) for k, v in route['resource_estimates'].items()):
        return 'BUDGET_EXHAUSTED'
    identifier = 'auto-' + slot['run_id']
    bundle = prepare(store, slot['code_path'], identifier)
    read = lambda path: strict_json(Path(path).read_text(encoding='utf-8'))
    request = {'schema': 1, 'run_id': slot['run_id'], 'parent_sha256': digest(state['contract']),
               'blocker_sha256': blocker, 'provider': deepcopy(config['provider']), 'response_path': slot['response_path'],
               'provider_timeout_seconds': route['timeout_seconds'] - 2,
               'workbench_id': identifier, 'workbench': read(bundle['request']), 'diagnostics': read(bundle['diagnostics']),
               'base_source': store._path(slot['code_path']).read_text(encoding='utf-8-sig'),
               'policy': deepcopy(state['contract']['advisor_policy']), 'original_review': report,
               'rejected_methods': deepcopy(previous),
               'evidence_excerpts': evidence_excerpts(store, state, ids),
               'authority': 'EXISTING_GOAL_BUDGET_COMMANDS_AND_CODE_PATH_ONLY'}
    if jumps is not None:
        request['jump_packet'] = jumps
    require(len(canonical(request).encode('utf-8')) <= MAX_BYTES, 'Model request exceeds bound')
    ref = cas_bytes(store.root, canonical(request).encode('utf-8'))
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        require(_fingerprint(_state(store, db)) == _fingerprint(state), 'State changed while preparing model request')
        _append(db, {'kind': REQUESTED, 'run_id': slot['run_id'], 'parent_sha256': request['parent_sha256'],
                     'blocker_sha256': blocker, 'request': ref})
    return 'REQUESTED'


def process_result(store, event, receipt):
    """Retain exact proposal before applying; resume partial/adopted transitions."""
    from rds_math import blob
    from rds_quick import cas_bytes
    from rds_method_revision import apply
    from rds_tool_workbench import prepare
    from rds_owned_advisor import _read_original
    rid = event['run_id']
    try:
        request = strict_json(blob(store.root, event['request']).decode('utf-8'))
        with store._db(True) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (rid,)).fetchone()
            run = store._run(db, rid)
            require(row is not None and store._receipt(row) == receipt and
                    receipt['attempt_id'] == run['attempt_id'] and receipt['manifest_sha256'] == run['manifest_sha256'] and
                    receipt['argv'] == run['manifest']['argv'] and receipt['executor_sha256'] == run['executor_sha256'] and
                    receipt['process_status'] == run['status'],
                    'Model result is not the retained original receipt')
            require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                               "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=?",
                               (rid, receipt['sha256'])).fetchone(), 'Model result lacks original completion')
            for artifact in receipt['artifacts']:
                _read_original(store, artifact)  # Includes raw provider/trace; no current-code rebinding during recovery.
            done = [e for e in _events(db, (PROCESSED,)) if e['run_id'] == rid]
            require(len(done) <= 1, 'Duplicate processed model result')
            if done:
                require(done[0]['receipt_sha256'] == receipt['sha256'], 'Processed model receipt differs')
                return done[0]['outcome']
    except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
        raise ModelResultIntegrityError(str(exc)) from exc
    try:
        with store._db(True) as db:
            prepared = next((e for e in _events(db, ('AUTONOMY_PROPOSAL_VALIDATED',)) if e['run_id'] == rid), None)
        if prepared:
            require(prepared['receipt_sha256'] == receipt['sha256'], 'Retained proposal receipt differs')
    except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
        raise ModelResultIntegrityError(str(exc)) from exc
    if prepared:
        try:
            proposal = strict_json(blob(store.root, prepared['proposal']).decode('utf-8'))
        except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
            raise ModelResultIntegrityError(str(exc)) from exc
    else:
        outcome, reason, proposal = 'MODEL_OUTCOME_UNKNOWN', 'Provider has no original successful response', None
        if receipt['run_status'] == 'SUCCEEDED':
            try:
                item = next((a for a in receipt['artifacts'] if a['path'] == request['response_path']), None)
                require(item is not None, 'Model response is not owned')
                raw = _read_original(store, item, keep=True)
                response = strict_json(raw.decode('utf-8')) if raw is not None else None
                if response is not None:
                    require(response['request_sha256'] == event['request']['sha256'] and response['run_id'] == rid and
                            response['provider'] == request['provider'], 'Model response/request/provider identity differs')
                    outcome, reason = response['status'].upper(), response['reason']
                else:
                    reason = 'ORIGINAL_MODEL_RESPONSE_JSON_BYTE_LIMIT'
            except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
                raise ModelResultIntegrityError(str(exc)) from exc
            if response is not None and response['status'] != 'unknown' and request.get('jump_packet') is not None:
                from rds_jump import validate_use
                usage = validate_use(request['jump_packet'], strict_json(response.get('jump_use_json', '')))
                body = {'kind': 'AUTONOMY_JUMP_REFERENCED', 'run_id': rid,
                        'request_sha256': event['request']['sha256'], 'receipt_sha256': receipt['sha256'],
                        'usage': usage, 'response_source_sha256': digest(response.get('source', '')),
                        'response_policy_sha256': digest(response.get('policy_json', '')),
                        'status': 'RESPONSE_REFERENCED', 'scientific_support': 'UNKNOWN'}
                with store._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    existing_use = [e for e in _events(db, ('AUTONOMY_JUMP_REFERENCED',)) if e['run_id'] == rid]
                    require(len(existing_use) <= 1, 'Duplicate jump response reference')
                    if existing_use:
                        require({k: v for k, v in existing_use[0].items() if k != 'sha256'} == body,
                                'Jump response reference conflicts with original')
                    else:
                        _append(db, body)
            if response is not None and response['status'] == 'proposed':
                require(isinstance(response['source'], str) and response['source'].strip() and
                        len(response['source'].encode('utf-8')) <= MAX_BYTES and
                        isinstance(response['reason'], str) and 1 <= len(response['reason'].strip()) <= 2048,
                        'Model proposal source/reason is empty or oversized')
                policy = strict_json(response['policy_json'])
                bundle = prepare(store, request['workbench']['code_path'], request['workbench_id'])
                source = Path(bundle['candidate_source'])
                source.write_text(response['source'], encoding='utf-8')
                proposal = {'id': request['workbench_id'], 'parent_sha256': request['parent_sha256'],
                            'reason': response['reason'], 'policy': policy,
                            'code_replacements': [{'path': request['workbench']['code_path'],
                                                   'source': str(source.relative_to(store.root)).replace('\\', '/'),
                                                   'sha256': file_sha(source)}]}
                ref = cas_bytes(store.root, canonical(proposal).encode('utf-8'))
                with store._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    _append(db, {'kind': 'AUTONOMY_PROPOSAL_VALIDATED', 'run_id': rid,
                                 'receipt_sha256': receipt['sha256'], 'proposal': ref})
        if proposal is None:
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                _processed_once(db, {'kind': PROCESSED, 'run_id': rid, 'receipt_sha256': receipt['sha256'],
                                     'outcome': outcome, 'reason': reason})
            return outcome
    def admit_adoption(db):
        from rds_steering import check_dispatch
        check_dispatch(db, rid)
    result = apply(store, proposal, admission_guard=admit_adoption)
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        _processed_once(db, {'kind': PROCESSED, 'run_id': rid, 'receipt_sha256': receipt['sha256'],
                             'outcome': 'ADOPTED', 'revision_id': proposal['id'], 'revision_sha256': result['contract_sha256']})
    return 'ADOPTED'


def _claim(store):
    owner = uuid.uuid4().hex
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        events = _events(db, ('AUTONOMY_DRIVE_CLAIMED', 'AUTONOMY_DRIVE_RELEASED'))
        active = None
        for e in events:
            if e['kind'] == 'AUTONOMY_DRIVE_CLAIMED':
                active = e
            elif active and e['owner'] == active['owner']:
                active = None
        if active:
            require(_alive(active['pid']) is False, 'Another controller may be active; observe before recovering')
            allowance = active['controller_reservation']
            db.execute("UPDATE budget SET reserved=reserved-?,charged=charged+? WHERE resource='wall_seconds'", (allowance, allowance))
            _append(db, {'kind': 'AUTONOMY_DRIVE_RELEASED', 'owner': active['owner'], 'reason': 'OWNER_DEAD_NO_WORKER_RELAUNCH'})
            db.commit()  # Dead-owner accounting must survive an exhausted new budget.
            return _claim(store)  # Re-read ownership in a new transaction, including races.
        from rds_steering import check_dispatch
        check_dispatch(db, None)
        config = store._contract(db)['advisor_policy']['autonomy']
        row = db.execute("SELECT * FROM budget WHERE resource='wall_seconds'").fetchone()
        available = max(0., row['cap'] - row['spent'] - row['charged'] - row['reserved'])
        allowance = min(config.get('controller_wall_seconds', 30), available)
        require(allowance > 0, 'Controller has no remaining wall budget')
        db.execute("UPDATE budget SET reserved=reserved+? WHERE resource='wall_seconds'", (allowance,))
        _append(db, {'kind': 'AUTONOMY_DRIVE_CLAIMED', 'owner': owner, 'pid': os.getpid(), 'started_at': time.time(),
                     'controller_reservation': allowance})
    return owner, allowance


def controller_reservation(store, db):
    """Only this live controller's hold may coexist with an idle revision."""
    if 'autonomy' not in store._contract(db).get('advisor_policy', {}):
        return 0.
    active = None
    for e in _events(db, ('AUTONOMY_DRIVE_CLAIMED', 'AUTONOMY_DRIVE_RELEASED')):
        if e['kind'] == 'AUTONOMY_DRIVE_CLAIMED':
            active = e
        elif active and e['owner'] == active['owner']:
            active = None
    return active['controller_reservation'] if active and active['pid'] == os.getpid() else 0.


def drive(store, max_steps=8, prepare_only=False):
    """Perform a bounded foreground pass; wait/unknown/deadline needs a later pass."""
    require(type(max_steps) is int and 1 <= max_steps <= 64, 'drive max_steps must be 1..64')
    require(type(prepare_only) is bool, 'drive prepare_only must be Boolean')
    from rds_owned_advisor import review, _state
    with store._db(True) as db:
        contract = store._contract(db)
    policy = contract.get('advisor_policy')
    require(policy and 'autonomy' in policy, 'project drive requires a frozen autonomy declaration')
    config = validate_policy(store, contract, policy)
    from rds_steering import SteeringBlocked, status as steering_status
    instruction = steering_status(store)
    if instruction['steering']['paused']:
        return {**instruction, 'status': 'HUMAN_STEERING_REQUIRED', 'executed': []}
    try:
        owner, allowance = _claim(store)
    except SteeringBlocked as exc:
        return {**steering_status(store), 'status': 'HUMAN_STEERING_REQUIRED', 'reason': str(exc), 'executed': []}
    except (ValueError, OSError, sqlite3.Error) as exc:
        return {'status': 'CONTROLLER_ADMISSION_BLOCKED', 'reason': str(exc), 'executed': [],
                'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN'}
    started, worker_wall, executed = time.monotonic(), 0., []
    result = {'status': 'STEP_LIMIT', 'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN', 'executed': executed}
    try:
        for _ in range(max_steps * 4 + 8):
            require(time.monotonic() - started - worker_wall < allowance, 'CONTROLLER_WALL_ALLOWANCE_EXHAUSTED')
            with store._db(True) as db:
                db.execute('BEGIN')
                state = _state(store, db)
                pending = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='AUTONOMY_PROPOSAL_VALIDATED' ORDER BY id DESC LIMIT 1").fetchone()
            active = [r for r in state['runs'] if r['status'] in {'RESERVED', 'RUNNING'}]
            running = [r for r in active if r['status'] == 'RUNNING' or r['attempt_id'] is not None]
            if running:
                for r in running:
                    store.recover(r['id'])
                with store._db(True) as db:
                    refreshed = store._runs(db)
                if any(r['status'] == 'RUNNING' for r in refreshed):
                    result['status'] = 'WAITING_FOR_ORIGINAL_ATTEMPT'
                    break
                continue
            # Paid work is retained for collection/recovery. A pause also prevents
            # adopting an old model proposal or purchasing a different repair.
            if state.get('steering', {}).get('paused'):
                result.update(status='HUMAN_STEERING_REQUIRED', steering=state['steering'])
                break
            # Finish/restore model adoption before any future research step.
            receipts = {r['run_id']: r for r in state['receipts']}
            events = state['autonomy_records']
            unfinished = [e for e in events if e['kind'] == REQUESTED and e['run_id'] in receipts and
                          not any(p['kind'] == PROCESSED and p['run_id'] == e['run_id'] for p in events)]
            if unfinished:
                original = unfinished[0]
                try:
                    process_result(store, original, receipts[original['run_id']])
                except (ModelResultIntegrityError, SteeringBlocked):
                    raise  # Keep original evidence/proposals; a pause is not rejection.
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    # An invalid suggestion is feedback for the next distinct
                    # authorized method, not an excuse to repeat this attempt.
                    with store._db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        from rds_method_revision import pending_revision
                        require(pending_revision(db) is None,
                                'Prepared adoption needs exact retained recovery: ' + str(exc))
                        _processed_once(db, {'kind': PROCESSED, 'run_id': original['run_id'],
                                             'receipt_sha256': receipts[original['run_id']]['sha256'],
                                             'outcome': 'PROPOSAL_REJECTED', 'reason': str(exc)})
                continue
            if len(executed) >= max_steps:
                break
            report = review(store)
            result['advisor'] = report
            if report.get('steering_handoff'):
                result.update(status='HUMAN_STEERING_REQUIRED', steering=report['steering'])
                break
            if report['status'] != 'REVIEWED':
                result['status'] = 'EVIDENCE_REPAIR_REQUIRED'
                break
            goal = next((r['search']['selection_review'].get('goal', {}).get('status') for r in report['recommendations']
                         if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH'), None)
            if goal == 'TRUE':
                confirmed = report.get('confirmation')
                result['status'] = ('GOAL_CONFIRMED' if confirmed and confirmed['task_confirmation'] == 'PASS'
                                    else 'GOAL_PREDICATES_MET_CONFIRMATION_' + (confirmed or {}).get('task_confirmation', 'UNDECLARED'))
                break
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                store._campaign_deadline(db, state['contract'], admit=True)
            manifest = report.get('selected_manifest')
            if manifest is None:
                confirmation = report.get('confirmation')
                if confirmation and all(v != 'PENDING' for v in confirmation['execution'].values()) and confirmation['task_confirmation'] == 'UNKNOWN':
                    result.update(status='DOMAIN_CONFIRMATION_UNKNOWN', confirmation=confirmation,
                                  reason='Original execution evidence cannot establish the declared domain confirmation; additional independent evidence is required')
                    break
                requested = request_repair(store, report)
                if requested == 'REQUESTED':
                    continue
                result['status'] = requested
                result['unresolved_obstacle'] = {'scope': 'DECLARED_AUTHORITY_AND_FINITE_REPAIR_SLOTS',
                    'reason': requested, 'tried_runs': [r['id'] for r in state['runs']],
                    'repair_results': [e for e in events if e['kind'] == PROCESSED],
                    'scientific_impossibility': 'UNKNOWN'}
                break
            if prepare_only and manifest['id'] in {s['run_id'] for s in config['repair_slots']}:
                # Paid-result recovery above still runs. Pause only before the
                # selected repair worker, retaining the verified original input.
                if manifest['id'] not in {r['id'] for r in state['runs']}:
                    require(len(state['runs']) < config['max_steps'], 'AUTONOMY_TOTAL_STEP_LIMIT')
                event = next(e for e in events if e['kind'] == REQUESTED and e['run_id'] == manifest['id'])
                from rds_math import blob
                request = strict_json(blob(store.root, event['request']).decode('utf-8'))
                require(request['parent_sha256'] == digest(state['contract']), 'Repair request parent is stale')
                result.update(status='MODEL_REQUEST_READY', run_id=manifest['id'],
                              request=deepcopy(event['request']), parent_sha256=request['parent_sha256'],
                              provider_timeout_seconds=request['provider_timeout_seconds'],
                              provider=deepcopy(request['provider']))
                break
            if manifest['id'] not in {r['id'] for r in state['runs']}:
                require(time.monotonic() - started - worker_wall < allowance, 'CONTROLLER_WALL_ALLOWANCE_EXHAUSTED')
                store.register(manifest)
            def admit(db, run):
                require(time.monotonic() - started - worker_wall < allowance,
                        'CONTROLLER_WALL_ALLOWANCE_EXHAUSTED')
                claims = _events(db, ('AUTONOMY_DRIVE_CLAIMED', 'AUTONOMY_DRIVE_RELEASED'))
                require(claims[-1]['kind'] == 'AUTONOMY_DRIVE_CLAIMED' and claims[-1]['owner'] == owner,
                        'Controller no longer owns its admission allowance')
            admit_check = time.monotonic() - started - worker_wall
            require(admit_check < allowance, 'CONTROLLER_WALL_ALLOWANCE_EXHAUSTED')
            before = time.monotonic()
            receipt = store.execute(manifest['id'], admission_guard=admit)
            elapsed = time.monotonic() - before
            wall = receipt.get('resources', {}).get('wall_seconds', {})
            worker_wall += min(elapsed, wall.get('measured') or wall.get('charged_estimate') or 0.)
            executed.append({'run_id': manifest['id'], 'attempt_id': receipt.get('attempt_id'),
                             'run_status': receipt.get('run_status'), 'receipt_sha256': receipt.get('sha256')})
        else:
            result['status'] = 'CONTROL_TRANSITION_LIMIT'
    except SteeringBlocked as exc:
        result.update(status='HUMAN_STEERING_REQUIRED', reason=str(exc), steering=steering_status(store)['steering'])
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, UnicodeError) as exc:
        result.update(status='HANDOFF_REQUIRED', reason=str(exc))
    finally:
        overhead = max(0., time.monotonic() - started - worker_wall)
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE budget SET reserved=reserved-?,spent=spent+?,charged=charged+? WHERE resource='wall_seconds'",
                       (allowance, min(overhead, allowance), max(0., overhead - allowance)))
            _append(db, {'kind': 'AUTONOMY_DRIVE_RELEASED', 'owner': owner, 'reason': result['status'],
                         'controller_wall_seconds': overhead, 'executed': deepcopy(executed)})
        result['controller_wall_seconds'] = overhead
    return result
