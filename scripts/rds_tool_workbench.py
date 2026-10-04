"""Prepare bounded, editable tool candidates under the existing project authority.

Receipts describe past attempts; syntax hints are not a profiler. Preparation
neither adopts code nor runs a pilot, and grants no new command or goal authority.
"""
import ast
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile

from rds_project import canonical, digest, file_sha, load_json, require, _shell_argument
from rds_method_revision import contract_history, pending_revision, MAX_BYTES

PREPARED = 'TOOL_WORKBENCH_PREPARED'
MAX_RECEIPTS = 8
TAIL_BYTES = 4096


def _internal(store, relative):
    """Only this program's internal bundle, never a project output allowance."""
    require(isinstance(relative, str) and not Path(relative).is_absolute()
            and not Path(relative).drive and ':' not in relative and '\\' not in relative,
            'Expected workbench-relative source path')
    parts = Path(relative).parts
    require(len(parts) == 4 and parts[:2] == ('.rds', 'tool-workbench')
            and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}', parts[2]),
            'Invalid workbench bundle path')
    base = store.root / '.rds' / 'tool-workbench'
    require(not base.is_symlink() and base.resolve().is_relative_to(store.root),
            'Workbench directory escapes project root')
    bundle = base / parts[2]
    require(not bundle.is_symlink() and bundle.resolve().is_relative_to(base.resolve()),
            'Workbench bundle escapes its directory')
    path = bundle / parts[3]
    require(not path.is_symlink() and path.resolve().parent == bundle.resolve(),
            'Workbench file escapes its bundle')
    return path


def _event(db, identifier):
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? "
                      "AND json_extract(body,'$.id')=?", (PREPARED, identifier)).fetchall()
    require(len(rows) <= 1, 'Duplicate workbench event')
    if not rows:
        return None
    import json
    event = json.loads(rows[0]['body'])
    require(event.get('sha256') == digest({k: v for k, v in event.items() if k != 'sha256'}),
            'Workbench event integrity failure')
    return event


def _verified_bundle(store, event):
    prefix = '.rds/tool-workbench/' + event['id'] + '/'
    require(event['bundle_path'] == prefix.rstrip('/'), 'Workbench event path mismatch')
    request = load_json(_internal(store, prefix + 'request.json'), max_bytes=MAX_BYTES,
                        expected_sha256=event['request_sha256'])
    load_json(_internal(store, prefix + 'diagnostics.json'), max_bytes=MAX_BYTES,
              expected_sha256=event['diagnostics_sha256'])
    require(request['id'] == event['id'] and request['parent_sha256'] == event['parent_sha256'],
            'Workbench request identity mismatch')
    for item in request['diagnostic_blobs']:
        path = Path(item['path']).resolve()
        require(path.parent == (store.root / '.rds/cas').resolve()
                and path.is_relative_to(store.root) and file_sha(path) == item['sha256'],
                'Workbench diagnostic CAS integrity failure')
    _internal(store, request['candidate_source'])
    return request


def resolve_source(store, relative):
    """Allow revision to consume only a ledger-bound workbench candidate."""
    path = _internal(store, relative)
    identifier = Path(relative).parts[2]
    with store._db(True) as db:
        event = _event(db, identifier)
        require(event is not None, 'Workbench candidate has no prepared event')
        request = _verified_bundle(store, event)
        require(request['candidate_source'] == relative, 'Only the workbench candidate is a revision source')
        require(contract_history(db)[-1]['sha256'] == request['parent_sha256'],
                'Workbench parent differs from effective contract')
    return path


def _hints(path, data):
    result = {'assurance': 'STATIC_SOURCE_HINTS_NOT_PROFILE_OR_COMPLEXITY_FACTS',
              'functions': [], 'loops': [], 'external_calls': []}
    if not path.endswith('.py'):
        result['reason'] = 'Python AST hints unavailable for this source type'
        return result
    try:
        tree = ast.parse(data.decode('utf-8-sig'), filename=path)
    except (SyntaxError, UnicodeError) as exc:
        result['reason'] = str(exc)[:512]
        return result
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and len(result['functions']) < 64:
            result['functions'].append({'name': node.name, 'line': node.lineno})
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp,
                             ast.DictComp, ast.GeneratorExp)) and len(result['loops']) < 64:
            result['loops'].append({'kind': type(node).__name__, 'line': node.lineno})
        if isinstance(node, ast.Call) and len(result['external_calls']) < 64:
            target = node.func
            while isinstance(target, ast.Attribute):
                target = target.value
            if isinstance(target, ast.Name) and (target.id in imported or target.id in {'open', 'exec', 'eval'}):
                result['external_calls'].append({'expression': ast.unparse(node.func)[:256], 'line': node.lineno})
    return result


def _diagnostics(store, receipts):
    """Use owned receipt validation and original-artifact readers, retaining tails in CAS."""
    from rds_owned_advisor import _read_original, _original
    from rds_quick import cas_bytes
    ordered = sorted(receipts, key=lambda r: (r.get('ended_at', 0), r['run_id']), reverse=True)
    selected = ordered[:MAX_RECEIPTS]
    totals = {}
    for receipt in selected:
        for resource, entry in receipt['resources'].items():
            value = entry.get('measured')
            if type(value) in (int, float) and math.isfinite(value) and value >= 0 and not entry.get('unknown'):
                key = (resource, entry['unit'])
                totals[key] = totals.get(key, 0) + value
    records, blobs = [], []
    for receipt in selected:
        record = {'run_id': receipt['run_id'], 'receipt_sha256': receipt['sha256'],
                  'run_status': receipt['run_status'], 'timeout': receipt['timeout'], 'costs': [], 'stderr_tails': []}
        for resource, entry in receipt['resources'].items():
            value = entry.get('measured')
            key = (resource, entry.get('unit'))
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0 and not entry.get('unknown')
            total = totals.get(key, 0)
            record['costs'].append({'resource': resource, 'unit': entry.get('unit'),
                'measured': value if valid else None, 'fraction_of_selected_measured_cost': value / total if valid and total else None,
                'charged_estimate': entry.get('charged_estimate'), 'unknown': not valid})
        if receipt['run_status'] != 'SUCCEEDED' or receipt['timeout']:
            for item in receipt['artifacts']:
                if item['kind'] not in {'stderr.bin', 'scheduler.stderr.bin'}:
                    continue
                _read_original(store, item)
                # Stream hashing bounds memory even for large retained logs.
                hasher, tail = hashlib.sha256(), b''
                with _original(store, item['path']).open('rb') as stream:
                    for chunk in iter(lambda: stream.read(65536), b''):
                        hasher.update(chunk)
                        tail = (tail + chunk)[-TAIL_BYTES:]
                require(hasher.hexdigest() == item['sha256'], 'Diagnostic artifact hash mismatch')
                ref = cas_bytes(store.root, tail, suffix='wbtaillog')
                require(file_sha(ref['path']) == ref['sha256'], 'Diagnostic CAS integrity failure')
                blobs.append(ref)
                record['stderr_tails'].append({'original': deepcopy(item), 'tail': ref,
                    'text': tail.decode('utf-8', errors='replace'), 'truncated': item['size'] > TAIL_BYTES})
        records.append(record)
    dominant = sorted([{'run_id': r['run_id'], 'receipt_sha256': r['receipt_sha256'], **c}
                       for r in records for c in r['costs'] if not c['unknown']],
                      key=lambda c: (c['resource'], c['unit'], -c['measured']))
    return {'assurance': 'OWNED_RECEIPT_MEASUREMENTS_NOT_PROFILING_OR_SCIENTIFIC_PROOF',
            'scope': 'Fractions refer only to these selected receipt measurements, separately by resource and unit',
            'receipt_count': len(receipts), 'selected_count': len(records),
            'truncated': len(receipts) > MAX_RECEIPTS, 'receipts': records, 'dominant_costs': dominant}, blobs


def _write(path, data):
    require(len(data) <= MAX_BYTES, 'Workbench file exceeds byte limit')
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _refresh(store, request, proposal_path):
    proposal = load_json(proposal_path, max_bytes=MAX_BYTES)
    require(isinstance(proposal, dict) and set(proposal) == {'id', 'parent_sha256', 'reason', 'policy', 'code_replacements'},
            'Workbench proposal shape changed')
    require(proposal['id'] == request['id'] and proposal['parent_sha256'] == request['parent_sha256'],
            'Workbench proposal identity changed')
    replacement = proposal['code_replacements']
    require(isinstance(replacement, list) and len(replacement) == 1 and set(replacement[0]) == {'path', 'source', 'sha256'}
            and replacement[0]['path'] == request['code_path'] and replacement[0]['source'] == request['candidate_source'],
            'Workbench replacement target/source changed')
    require(isinstance(proposal['reason'], str) and 1 <= len(proposal['reason'].strip()) <= 2048,
            'Workbench reason must be bounded and nonempty')
    with store._db(True) as db:
        contract = contract_history(db)[-1]['contract']
        from rds_method_revision import _policy_change
        revised = deepcopy(contract)
        revised['advisor_policy'] = proposal['policy']
        _policy_change(store, db, contract, revised)
    source = _internal(store, request['candidate_source'])
    require(source.stat().st_size <= MAX_BYTES, 'Workbench candidate exceeds byte limit')
    replacement[0]['sha256'] = file_sha(source)
    raw = canonical(proposal).encode('utf-8')
    require(len(raw) <= MAX_BYTES, 'Workbench proposal exceeds byte limit')
    # Proposal is the sole generated file refreshed after permitted candidate/model edits.
    if proposal_path.read_bytes() != raw:
        descriptor, name = tempfile.mkstemp(prefix='.proposal-', dir=proposal_path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, proposal_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return proposal


def prepare(store, code_path, identifier):
    """Generate/refresh one immutable request and editable candidate/proposal pair."""
    require(isinstance(identifier, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}', identifier),
            'Invalid workbench ID')
    prefix = '.rds/tool-workbench/' + identifier + '/'
    request_path = _internal(store, prefix + 'request.json')
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        history = contract_history(db)
        genesis, contract = history[0]['contract'], history[-1]['contract']
        require(genesis.get('method_evolution') and code_path in genesis['method_evolution']['code_paths'],
                'Tool improvement path was not authorized at genesis')
        require(pending_revision(db) is None, 'Resume pending method revision before preparing a tool')
        require(len(history) <= genesis['method_evolution']['max_revisions'], 'Method revision maximum reached')
        require('advisor_policy' in contract, 'Tool workbench requires an owned policy')
        _, errors = store._bindings(contract)
        require(not errors, '; '.join(errors))
        event = _event(db, identifier)
        if event:
            request = _verified_bundle(store, event)
            require(request['code_path'] == code_path and request['parent_sha256'] == history[-1]['sha256'],
                    'Workbench ID reused with different code path or parent')
        else:
            require(not request_path.parent.exists(), 'Existing unrecorded workbench bundle; refusing overwrite')
            data = store._path(code_path).read_bytes()
            require(len(data) <= MAX_BYTES, 'Tool source exceeds byte limit')
            runs = {run['id']: run for run in store._runs(db)}
            receipts = [store._receipt(row) for row in db.execute('SELECT run_id,sha256,body FROM receipts ORDER BY run_id')]
            for receipt in receipts:
                run = runs.get(receipt['run_id'])
                require(run is not None and receipt['manifest_sha256'] == run['manifest_sha256']
                        and receipt['attempt_id'] == run['attempt_id'] and receipt['process_status'] == run['status'],
                        'Diagnostic receipt differs from its owned run')
                require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                        "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=?",
                        (receipt['run_id'], receipt['sha256'])).fetchone() is not None,
                        'Diagnostic receipt has no owned completion event')
            diagnostics, blobs = _diagnostics(store, receipts)
            policy = contract['advisor_policy']
            goals = policy['context']['decision']['goal_conditions']
            producers = [{'goal_condition': deepcopy(goal), 'observations': [deepcopy(o) for o in policy['observations']
                          if o['fact'] == goal['fact']], 'independence_assurance': 'DECLARED_PRODUCER_NOT_VERIFIED_INDEPENDENCE'} for goal in goals]
            start = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED' ORDER BY id LIMIT 1").fetchone()
            import json
            request = {'schema': 1, 'id': identifier, 'parent_sha256': history[-1]['sha256'],
                'genesis_sha256': history[0]['sha256'], 'code_path': code_path,
                'base_code_sha256': hashlib.sha256(data).hexdigest(),
                'candidate_source': prefix + 'candidate' + Path(code_path).suffix,
                'preserved': {'context': deepcopy(policy['context']), 'goal_conditions': deepcopy(goals),
                    'evaluator_data_bindings': [deepcopy(b) for b in contract['bindings'] if b['role'] in {'evaluator', 'data'}],
                    'budget': deepcopy(contract['budget']), 'stop_policy': deepcopy(contract.get('stop_policy')),
                    'campaign_T0': json.loads(start['body']) if start else None,
                    'allowed_commands': deepcopy(contract['allowed_commands'])},
                'goal_verification_producers': producers, 'source_hints': _hints(code_path, data), 'diagnostic_blobs': blobs,
                'instructions': ['Edit only the candidate source and proposal policy/reason; prepare again to refresh its hash.',
                    'Preserve exact goal predicates, evaluator, data, total budget and existing campaign T0.',
                    'Improve the actual tool; comments, renaming and numeric tuning alone fail revision admission.',
                    'Review measured dominant costs/failure tails and the static function/loop/call hints.',
                    'Use only already allowed argv. Preserve registered routes/observations and include separate goal verification.',
                    'Preparation and a structural change do not establish correctness or speed; run authorized pilots and the independent goal verifier.']}
            proposal = {'id': identifier, 'parent_sha256': history[-1]['sha256'],
                'reason': 'Improve the authorized bound tool after reviewing receipt diagnostics and preserving final verification',
                'policy': deepcopy(policy), 'code_replacements': [{'path': code_path, 'source': request['candidate_source'],
                    'sha256': request['base_code_sha256']}]}
            request_path.parent.mkdir(parents=True, exist_ok=False)
            _write(_internal(store, request['candidate_source']), data)
            _write(request_path, canonical(request).encode('utf-8'))
            _write(_internal(store, prefix + 'diagnostics.json'), canonical(diagnostics).encode('utf-8'))
            _write(_internal(store, prefix + 'proposal.json'), canonical(proposal).encode('utf-8'))
            event = {'kind': PREPARED, 'id': identifier, 'parent_sha256': history[-1]['sha256'],
                     'bundle_path': prefix.rstrip('/'), 'request_sha256': file_sha(request_path),
                     'diagnostics_sha256': file_sha(_internal(store, prefix + 'diagnostics.json'))}
            event['sha256'] = digest(event)
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
    proposal_path = _internal(store, prefix + 'proposal.json')
    proposal = _refresh(store, request, proposal_path)
    command = 'python -B scripts/rds_cli.py --root ' + _shell_argument(store.root) + ' project '
    return {'status': 'TOOL_WORKBENCH_PREPARED', 'execution_started': False, 'id': identifier,
            'bundle_path': str(request_path.parent), 'request': str(request_path),
            'request_sha256': event['request_sha256'], 'diagnostics': str(_internal(store, prefix + 'diagnostics.json')),
            'candidate_source': str(_internal(store, request['candidate_source'])), 'proposal': str(proposal_path),
            'candidate_sha256': proposal['code_replacements'][0]['sha256'],
            'revision_command': command + 'revise --proposal ' + _shell_argument(proposal_path),
            'next_command': command + 'next',
            'pilot_run_ids': proposal['policy'].get('feasibility', {}).get('pilots', []),
            'goal_verification_producers': request['goal_verification_producers'],
            'scientific_claim': None, 'improvement_measured': False}
