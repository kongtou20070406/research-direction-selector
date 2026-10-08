"""Program-owned Advisor input and admission over the existing project ledger.

The frozen contract declares the finite candidate scope and JSON observations.
Every invocation accounts for every owned run/receipt, checks original bytes,
and makes the resulting TMS nodes the input to Advisor. This is an evidence
coverage contract, not an OS sandbox or a scientific-success certificate.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import sqlite3
import time

from rds_artifacts import ArtifactFact, _extract, strict_json
from rds_project import canonical, digest, number, require

OWNED_PREFIX = 'owned:'
ASSURANCE = 'PROGRAM_OWNED_EVIDENCE_NOT_SCIENTIFIC_PROOF'
MAX_RUNS = 128
MAX_ARTIFACTS = 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
TERMINAL = {'COMPLETED', 'FAILED', 'INTERRUPTED'}
CONTEXT_FIELDS = {'decision', 'research_mode', 'method_constraints', 'scope',
                  'max_depth', 'max_candidates', 'target_types', 'targets'}


def _text(value, maximum=128):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def validate_policy(store, contract):
    """Validate the frozen declaration before project initialization; no writes."""
    if 'advisor_policy' not in contract:
        return None
    policy = contract['advisor_policy']
    require(isinstance(policy, dict) and {'schema', 'context', 'graph', 'routes', 'observations'} <= set(policy)
            and set(policy) <= {'schema', 'context', 'graph', 'routes', 'observations', 'feasibility', 'tool_bindings', 'confirmation', 'autonomy', 'graph_ranker'},
            'advisor_policy needs schema, context, graph, routes and observations')
    require(type(policy['schema']) is int and policy['schema'] == 1, 'advisor_policy schema must be 1')
    require(len(canonical(policy).encode('utf-8')) <= MAX_JSON_BYTES, 'advisor_policy exceeds 2 MiB')
    context, graph = policy['context'], policy['graph']
    require(isinstance(context, dict) and not set(context) - CONTEXT_FIELDS,
            'advisor_policy context has unsupported or program-owned fields; facts and costs cannot be self-signed')
    decision = context.get('decision')
    require(isinstance(decision, dict) and _text(decision.get('id')) and _text(decision.get('goal_revision'))
            and isinstance(decision.get('scope'), dict), 'Owned Advisor needs decision id, goal_revision and scope')
    goals = decision.get('goal_conditions')
    require(isinstance(goals, list) and 1 <= len(goals) <= 32, 'Owned Advisor needs explicit goal_conditions')
    require(all(isinstance(g, dict) and set(g) <= {'fact', 'op', 'value'} and _text(g.get('fact'))
                and 'value' in g for g in goals), 'Invalid owned goal condition')
    require(isinstance(graph, dict) and set(graph) <= {'nodes', 'edges', 'schema'}, 'Invalid owned judgment graph')
    require(isinstance(graph.get('nodes'), list) and len(graph['nodes']) <= 128
            and isinstance(graph.get('edges'), list) and len(graph['edges']) <= 512, 'Owned graph limits exceeded')
    actions = {}
    for node in graph['nodes']:
        require(isinstance(node, dict), 'Graph node must be an object')
        executable = node.get('executable', {})
        require(isinstance(executable, dict), 'Graph node executable must be an object')
        action = executable.get('action')
        if action is not None:
            require(isinstance(action, dict) and _text(action.get('id')) and action['id'] not in actions,
                    'Owned action IDs must be unique')
            actions[action['id']] = action
    # The context is frozen, so an action the Advisor discards now is discarded on every later review.
    from rds_advisor_search import _action_valid
    for action_id, action in actions.items():
        admitted, reason = _action_valid(action, decision.get('current_choice'))
        require(admitted, f"Owned action '{action_id}' would never be admitted by the Advisor: {reason}; "
                "correct it before project init, because advisor_policy freezes with the contract")
    routes = policy['routes']
    retained = {}
    history = []
    if store.path.is_file() and 'method_evolution' in contract:
        with store._db(True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone():
                from rds_method_revision import contract_history
                history = contract_history(db)
                retained = {r['id']: r for r in store._runs(db)}
    require(isinstance(routes, list) and 1 <= len(routes) <= 64, 'Owned routes must contain 1..64 manifests')
    ids, candidates, output_owners = {}, set(), set()
    manifest_fields = {'schema', 'id', 'arm', 'control_id', 'protocol', 'argv', 'outpaths',
                       'resource_estimates', 'timeout_seconds', 'description'}
    for route in routes:
        require(isinstance(route, dict) and set(route) == {'candidate', 'manifest'}, 'Invalid owned route')
        candidate, spec = route['candidate'], route['manifest']
        require(_text(candidate) and candidate in actions and candidate not in candidates,
                'Each owned route must name one unique configured action ID')
        require(isinstance(spec, dict) and not set(spec) - manifest_fields and type(spec.get('schema')) is int
                and spec['schema'] == 1, 'Invalid frozen manifest')
        ident = spec.get('id')
        require(_text(ident, 80) and all(c.isalnum() or c in '-_' for c in ident) and ident not in ids,
                'Invalid or duplicate frozen run ID')
        require(spec.get('arm') in {'control', 'treatment', 'tool'} and
                (spec.get('control_id') is None or _text(spec['control_id'], 80)), 'Invalid frozen arm')
        require(spec['arm'] != 'treatment' or spec.get('control_id'), 'Treatment requires a control ID')
        store._command(spec.get('argv'))
        require(spec['argv'] in contract.get('allowed_commands', []), 'Frozen route command is not authorized')
        timeout = number(spec.get('timeout_seconds'), 'owned timeout_seconds', True)
        estimates = spec.get('resource_estimates')
        require(isinstance(estimates, dict) and set(estimates) == set(contract.get('budget', {})),
                'Frozen route estimates must match budget dimensions')
        for resource, amount in estimates.items():
            number(amount, 'owned estimate.' + resource)
        require(estimates.get('wall_seconds', -1) >= timeout, 'Frozen wall estimate must cover timeout')
        protocol = spec.get('protocol')
        historic = retained.get(ident)
        contracts = [contract]
        if historic and historic['manifest'] == spec:
            contracts += [h['contract'] for h in history if h['sha256'] ==
                          historic.get('effective_contract_sha256', history[0]['sha256'])]
        require(isinstance(protocol, dict) and set(protocol) == {'path', 'sha256'} and any(
            b.get('role') == 'protocol' and all(b.get(k) == protocol[k] for k in protocol)
            for c in contracts for b in c.get('bindings', [])), 'Frozen route protocol is not contract-bound')
        outputs = spec.get('outpaths')
        require(isinstance(outputs, list) and len(outputs) <= 64 and (outputs or spec['arm'] == 'tool'),
                'Frozen outputs must be a bounded list')
        for output in outputs:
            key = store._output_key(store._path(output, True, contract))
            require(key not in output_owners, 'Frozen routes must own distinct outputs')
            output_owners.add(key)
        candidates.add(candidate)
        ids[ident] = spec
    require(candidates == set(actions), 'Every configured action must have a frozen route; hidden candidates are not accepted')
    require(all(spec.get('control_id') is None or spec['control_id'] in ids for spec in ids.values()),
            'Frozen control ID must name a policy route')
    observations, fact_ids = policy['observations'], set()
    require(isinstance(observations, list) and len(observations) <= 128, 'Owned observations limit exceeded')
    for observation in observations:
        require(isinstance(observation, dict) and set(observation) <= {'fact', 'run_id', 'path', 'selector', 'format'}
                and {'fact', 'run_id', 'path', 'selector'} <= set(observation), 'Invalid owned observation')
        fid, rid = observation['fact'], observation['run_id']
        require(_text(fid) and not fid.startswith(('run.', 'owned-tool-gate.', 'autonomy.', 'confirmation.')) and fid not in fact_ids, 'Duplicate or reserved observation fact ID')
        require(rid in ids and observation['path'] in ids[rid]['outpaths'], 'Observation must name a frozen run output')
        selector = observation['selector']
        require(observation.get('format', 'json') == 'json' and isinstance(selector, dict)
                and set(selector) == {'pointer'}, 'Owned observations support JSON pointer selectors only')
        pointer = selector['pointer']
        require(isinstance(pointer, str) and len(pointer) <= 2048 and (pointer == '' or pointer.startswith('/'))
                and len(pointer.split('/')) <= 33
                and all(p[:1] in ('0', '1') for s in pointer.split('/') for p in s.split('~')[1:]),
                'Invalid owned JSON pointer')
        fact_ids.add(fid)
    lifecycle = {f'run.{rid}.{key}' for rid in ids for key in ('status', 'completed', 'succeeded', 'failed', 'timed_out')}
    require(all(g['fact'] in fact_ids | lifecycle for g in goals), 'Goal predicates must refer to owned observations or lifecycle facts')
    # Existing graph validation and method checks remain authoritative.
    from rds_advisor_search import search_directions
    search_directions(graph, {**deepcopy(context), 'facts': {}})
    from rds_feasibility import validate
    validate(store, contract, policy)
    if 'tool_bindings' in policy:
        from rds_tool_applicability import validate_bindings
        validate_bindings(store, contract, policy['tool_bindings'])
    if 'confirmation' in policy:
        from rds_domain_confirmation import validate_policy as validate_confirmation
        validate_confirmation(store, contract, policy)
    if 'autonomy' in policy:
        from rds_autonomy import validate_policy as validate_autonomy
        validate_autonomy(store, contract, policy)
    if 'graph_ranker' in policy:
        from rds_graph_ranker import validate
        validate(policy['graph_ranker'])
    return policy


def _state(store, db):
    """Bounded, coherent read, shared by analysis and the write-lock recheck."""
    contract = store._contract(db)
    require(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] <= MAX_RUNS, 'Owned run coverage limit exceeded')
    require(db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] <= MAX_RUNS, 'Owned receipt coverage limit exceeded')
    runs = store._runs(db)
    receipts = [store._receipt(row) for row in db.execute('SELECT run_id,sha256,body FROM receipts ORDER BY run_id')]
    budget = [dict(row) for row in db.execute('SELECT * FROM budget ORDER BY resource')]
    completed = []
    for receipt in receipts:
        require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                           "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=? LIMIT 1",
                           (receipt['run_id'], receipt['sha256'])).fetchone() is not None,
                'Owned receipt has no matching completion event')
        completed.append(receipt['sha256'])
    campaign = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED' ORDER BY id LIMIT 1").fetchone()
    history = [{'sha256': digest(contract), 'contract': contract}]
    if 'method_evolution' in contract:
        from rds_method_revision import contract_history
        history = contract_history(db)
    from rds_owned_history import history_cut
    try:
        verified_history = history_cut(store, db)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ValueError('Repair checkpoint integrity before executing a candidate: ' + str(exc)) from exc
    state = {'contract': contract, 'runs': sorted(runs, key=lambda r: r['id']), 'receipts': receipts,
            'budget': budget, 'completion_events': completed,
            'history_cut': verified_history,
            'contract_history': history,
            'exposures': [strict_json(row['body']) for row in db.execute('SELECT body FROM exposures ORDER BY id')],
            'campaign_started': strict_json(campaign['body']) if campaign else None}
    if 'autonomy' in contract.get('advisor_policy', {}):
        from rds_autonomy import records
        state['autonomy_records'] = records(store, db, contract)
    from rds_postcommit_confirmation import enabled, records as confirmation_records
    if enabled(contract):
        state['confirmation_challenges'] = confirmation_records(store, db, contract)
    return state


def _fingerprint(state):
    """Bind admission state, excluding only a running worker's elapsed telemetry."""
    projected = {**state, 'runs': [
        {k: v for k, v in run.items() if k != 'observed_wall_seconds'}
        if run['status'] == 'RUNNING' else run for run in state['runs']]}
    return digest(projected)


def _telemetry(state):
    now = time.time()
    return {'snapshot_at': now, 'checked_at': now, 'running': [
        {'run_id': run['id'], 'attempt_id': run['attempt_id'],
         'lower_bound_seconds': run['observed_wall_seconds'],
         'latest_seconds': run['observed_wall_seconds']}
        for run in state['runs'] if run['status'] == 'RUNNING']}


def _reconcile(state, fingerprint, telemetry, reason):
    """Accept monotonic elapsed updates only; caller owns the coherent read/lock."""
    require(_fingerprint(state) == fingerprint, reason)
    require(isinstance(telemetry, dict) and isinstance(telemetry.get('running'), list),
            'Owned admission telemetry is missing or malformed')
    active = {run['id']: run for run in state['runs'] if run['status'] == 'RUNNING'}
    require(len(telemetry['running']) == len(active), 'Owned admission telemetry coverage differs')
    seen = set()
    for window in telemetry['running']:
        require(isinstance(window, dict) and window.get('run_id') in active
                and window['run_id'] not in seen, 'Owned admission telemetry identity differs')
        seen.add(window['run_id'])
        run = active[window['run_id']]
        require(window.get('attempt_id') == run['attempt_id'], 'Owned admission telemetry attempt differs')
        lower = number(window.get('lower_bound_seconds'), 'telemetry lower bound')
        latest = number(window.get('latest_seconds'), 'telemetry latest bound')
        require(lower <= latest <= run['observed_wall_seconds'],
                'Owned running telemetry regressed; inspect retained state')
        window['latest_seconds'] = run['observed_wall_seconds']
    telemetry['checked_at'] = time.time()


def _original(store, relative):
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
            and not Path(relative).drive and ':' not in relative, 'Invalid receipt artifact path')
    path = (store.root / relative).resolve()
    require(path.is_relative_to(store.root) and path != store.root, 'Receipt artifact escapes project root')
    return path


def _read_original(store, item, *, keep=False):
    path = _original(store, item['path'])
    size = path.stat().st_size
    require(type(item.get('size')) is int and size == item['size'], 'Artifact size changed: ' + item['path'])
    hasher, read_size = hashlib.sha256(), 0
    chunks = [] if keep and size <= MAX_JSON_BYTES else None
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            read_size += len(chunk)
            require(read_size <= size, 'Artifact grew during read')
            hasher.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
    require(read_size == size, 'Artifact changed during read')
    require(hasher.hexdigest() == item['sha256'], 'Artifact hash mismatch: ' + item['path'])
    return b''.join(chunks) if chunks is not None else None


def _node(ident, status, source, **metadata):
    source = deepcopy(source)
    # Importer sources use ``path``; the existing TMS audit uses ``file``.
    # Retain both physical locators without minting a new fact identity.
    if isinstance(source, dict) and 'sha256' in source and 'path' in source:
        source['file'] = source['path']
    return {'id': OWNED_PREFIX + ident, 'status': status, 'source': source, **metadata}


def _collect(store, state):
    policy = state['contract'].get('advisor_policy')
    runs = {r['id']: r for r in state['runs']}
    receipts = {r['run_id']: r for r in state['receipts']}
    require(set(receipts) <= set(runs), 'Receipt has no owned run')
    nodes, edges, files, originals, errors = [], [], [], {}, []
    oversized_json = {}
    coverage = {'runs': len(runs), 'receipts': len(receipts), 'artifacts': 0,
                'parsed_observations': 0, 'declared_outputs': [], 'unparsed_outputs': [], 'gaps': [], 'errors': errors}
    requested = {(o['run_id'], o['path']) for o in policy['observations']} if policy else set()
    contract_source = {'locator': 'project contract ' + digest(state['contract'])}
    nodes.append(_node('contract', 'SUPPORTED', contract_source, claim='The frozen project contract was read'))
    for rid, run in runs.items():
        receipt = receipts.get(rid)
        require(run['status'] not in TERMINAL or receipt is not None, 'Terminal owned run has no receipt: ' + rid)
        nodes.append(_node('run:' + rid, 'SUPPORTED', {'locator': 'owned run ' + rid},
                           lifecycle_status=run['status'], manifest_sha256=run['manifest_sha256']))
        if receipt is None:
            for relative in run['manifest']['outpaths']:
                coverage['declared_outputs'].append({'run_id': rid, 'path': relative, 'status': 'PENDING'})
                nodes.append(_node('output:' + digest((rid, relative)), 'UNKNOWN',
                                   {'locator': 'pending declared output ' + rid + ':' + relative},
                                   interpretation='PENDING', run_id=rid))
            continue
        require(receipt.get('manifest_sha256') == run['manifest_sha256'] and receipt.get('attempt_id') == run['attempt_id']
                and receipt.get('process_status') == run['status'] and receipt.get('argv') == run['manifest']['argv']
                and receipt.get('executor_sha256') == run['executor_sha256'] and receipt.get('cwd') == str(store.root),
                'Owned receipt/run binding mismatch: ' + rid)
        require(receipt.get('arm') == run['manifest']['arm'] and receipt.get('control_id') == run['manifest'].get('control_id')
                and receipt.get('protocol') == run['protocol'], 'Owned receipt protocol/arm mismatch: ' + rid)
        expected_status = 'SUCCEEDED' if run['status'] == 'COMPLETED' else run['status']
        require(receipt.get('run_status') == expected_status and run['status'] in TERMINAL,
                'Owned receipt lifecycle outcome mismatch: ' + rid)
        require(type(receipt.get('timeout')) is bool and
                (receipt.get('exit_code') is None or type(receipt['exit_code']) is int),
                'Owned receipt timeout/exit code is malformed: ' + rid)
        if expected_status == 'SUCCEEDED':
            require(receipt.get('exit_code') == 0 and receipt.get('process_started') is True
                    and receipt['timeout'] is False and receipt.get('errors') == [],
                    'Successful owned receipt has incompatible process outcome: ' + rid)
            bound_contract = next((h['contract'] for h in state['contract_history']
                                   if h['sha256'] == run.get('effective_contract_sha256', state['contract_history'][0]['sha256'])), None)
            require(bound_contract is not None and receipt.get('bindings_before') == receipt.get('bindings_after') == bound_contract['bindings'],
                    'Successful owned receipt input bindings differ: ' + rid)
        source = {'locator': 'owned receipt ' + receipt['sha256']}
        nodes.append(_node('receipt:' + rid, 'SUPPORTED', source, receipt_sha256=receipt['sha256'],
                           outcome=receipt['run_status'], scientific_support='UNKNOWN'))
        edges.append({'id': OWNED_PREFIX + 'completion:' + rid, 'premises': [OWNED_PREFIX + 'run:' + rid],
                      'conclusion': OWNED_PREFIX + 'receipt:' + rid, 'status': 'SUPPORTED', 'source': source})
        entries = receipt.get('artifacts')
        require(isinstance(entries, list), 'Owned receipt artifacts must be a list')
        seen, output_statuses = set(), {}
        for item in entries:
            require(isinstance(item, dict) and _text(item.get('path'), 2048) and item['path'] not in seen,
                    'Invalid or duplicate owned artifact')
            seen.add(item['path'])
            if item.get('kind') == 'project_output':
                require(item['path'] in run['manifest']['outpaths'], 'Receipt output was not declared by its run')
                store._path(item['path'], True, state['contract'])
            else:
                expected = store.artifact_dir / rid / str(item.get('kind'))
                require(_original(store, item['path']) == expected.resolve()
                        and item.get('kind') in {'manifest.json', 'stdout.bin', 'stderr.bin', 'scheduler.stdout.bin',
                                                 'scheduler.stderr.bin', 'worker.log'}, 'Unexpected receipt artifact')
            coverage['artifacts'] += 1
            require(coverage['artifacts'] <= MAX_ARTIFACTS, 'Owned artifact coverage limit exceeded')
            key = (rid, item['path'])
            source = {'locator': 'owned artifact ' + rid, 'file': item['path'], 'sha256': item['sha256']}
            try:
                raw = _read_original(store, item, keep=key in requested)
                files.append({k: item[k] for k in ('path', 'sha256', 'size')})
                if raw is not None:
                    originals[key] = raw
                elif key in requested:
                    oversized_json[key] = {k: item[k] for k in ('path', 'sha256', 'size')}
                node_status = 'SUPPORTED'
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append({'run_id': rid, 'path': item['path'], 'reason': str(exc)})
                node_status = 'UNKNOWN'
            nodes.append(_node('artifact:' + digest(key), node_status, source, run_id=rid,
                               interpretation='UNPARSED', scientific_support='UNKNOWN'))
            if item.get('kind') == 'project_output':
                output_statuses[item['path']] = 'VERIFIED_BYTES' if node_status == 'SUPPORTED' else 'UNAVAILABLE_OR_CHANGED'
                coverage['unparsed_outputs'].append({'run_id': rid, 'path': item['path']})
        # Coverage is measured against the registered manifest, not merely the
        # subset a receipt happens to enumerate. Failed runs may lack outputs;
        # that missing evidence remains an explicit unknown output node.
        for relative in run['manifest']['outpaths']:
            status = output_statuses.get(relative, 'MISSING')
            coverage['declared_outputs'].append({'run_id': rid, 'path': relative, 'status': status})
            if status == 'MISSING':
                nodes.append(_node('output:' + digest((rid, relative)), 'UNKNOWN',
                                   {'locator': 'missing declared output ' + rid + ':' + relative},
                                   interpretation='MISSING', run_id=rid))
                if expected_status == 'SUCCEEDED':
                    errors.append({'run_id': rid, 'path': relative, 'reason': 'Successful receipt omitted a declared output'})
    # The graph owns lifecycle facts even for a not-yet-registered frozen route.
    all_ids = set(runs) | ({r['manifest']['id'] for r in policy['routes']} if policy else set())
    for rid in sorted(all_ids):
        run, receipt = runs.get(rid), receipts.get(rid)
        status = run['status'] if run else 'NOT_REGISTERED'
        values = {'status': status, 'completed': status in TERMINAL,
                  'succeeded': bool(receipt and receipt['run_status'] == 'SUCCEEDED'),
                  'failed': bool(receipt and receipt['run_status'] != 'SUCCEEDED'),
                  'timed_out': bool(receipt and receipt.get('timeout') is True)}
        for key, value in values.items():
            fid = 'run.' + rid + '.' + key
            fact = {'id': fid, 'value': value, 'kind': 'DERIVED', 'source': {'locator': 'owned lifecycle ' + rid},
                    'reliable': True}
            nodes.append(_node('fact:' + fid, 'SUPPORTED', fact['source'], owned_fact=fact))
    for obs in policy['observations'] if policy else []:
        rid, relative, fid = obs['run_id'], obs['path'], obs['fact']
        receipt = receipts.get(rid)
        fact = {'id': fid, 'value': None, 'kind': 'UNKNOWN', 'reliable': False,
                'source': {'locator': 'pending owned output ' + rid + ':' + relative}, 'reason': 'Run output is pending'}
        if receipt is not None:
            try:
                if (rid, relative) in oversized_json:
                    fact['source'] = {**oversized_json[rid, relative], 'receipt_id': receipt['sha256'],
                                      'locator': 'verified original over JSON parse byte limit'}
                    raise ValueError('Verified original JSON exceeds the ' + str(MAX_JSON_BYTES) + '-byte parse limit')
                require((rid, relative) in originals, 'Declared output missing, changed or over JSON byte limit')
                value, locator, _ = _extract(strict_json(originals[rid, relative].decode('utf-8-sig')), obs['selector'], 'json')
                require(value is None or isinstance(value, (str, bool, int, float)), 'Owned observation must be a JSON scalar')
                require(not isinstance(value, (int, float)) or math.isfinite(value), 'Owned observation must be finite')
                reliable = receipt['run_status'] == 'SUCCEEDED'
                artifact = next(a for a in receipt['artifacts'] if a['path'] == relative)
                fact.update(value=value, kind='OBSERVED' if reliable else 'UNKNOWN', reliable=reliable,
                            source={'path': relative, 'sha256': artifact['sha256'], 'locator': locator,
                                    'receipt_id': receipt['sha256']},
                            reason='Observed original bytes' if reliable else 'Partial output from an unsuccessful run')
                coverage['parsed_observations'] += 1
                unparsed = {'run_id': rid, 'path': relative}
                if unparsed in coverage['unparsed_outputs']:
                    coverage['unparsed_outputs'].remove(unparsed)
                if not reliable:
                    coverage['gaps'].append({'run_id': rid, 'fact': fid, 'reason': fact['reason']})
            except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError) as exc:
                fact['reason'] = str(exc)
                # A failed attempt legitimately may have no measurement. It
                # can still support a declared diagnostic route through its
                # lifecycle facts. Corrupt successful evidence fails closed.
                destination = errors if receipt['run_status'] == 'SUCCEEDED' and (rid, relative) not in oversized_json else coverage['gaps']
                destination.append({'run_id': rid, 'fact': fid, 'reason': str(exc)})
        nodes.append(_node('fact:' + fid, 'SUPPORTED' if fact['reliable'] else 'UNKNOWN', fact['source'], owned_fact=fact))
    if policy and 'autonomy' in policy:
        from rds_autonomy import collect
        collect(store, state, nodes, files)
    if policy and 'confirmation' in policy:
        from rds_domain_confirmation import inspect_confirmation
        confirmation = inspect_confirmation(store, state['contract'], state)
        fid = 'confirmation.task_status'
        fact = {'id': fid, 'kind': 'DERIVED', 'value': confirmation['task_confirmation'], 'reliable': True,
                'source': {'locator': 'program replay of frozen domain evidence ' + digest(confirmation)}}
        nodes.append(_node('fact:' + fid, 'SUPPORTED', fact['source'], owned_fact=fact))
    return nodes, edges, coverage, files


def _facts(spec):
    """Facts are reconstructed from the current owned TMS nodes, not LLM JSON."""
    return {n['owned_fact']['id']: ArtifactFact(deepcopy(n['owned_fact'])) for n in spec['nodes']
            if n['id'].startswith(OWNED_PREFIX) and 'owned_fact' in n}


def _dispatch_graph(policy, runs):
    """Project live dispatch choices before the existing bounded search.

    Completed routes still supply prerequisite nodes and observations. Only
    their dispatch roots/fallback actions are omitted; the frozen graph and
    its predicates, edges, history and search bounds remain unchanged.
    """
    statuses = {run['id']: run['status'] for run in runs}
    terminal = {route['candidate'] for route in policy['routes']
                if statuses.get(route['manifest']['id']) in TERMINAL}
    graph = deepcopy(policy['graph'])
    for node in graph['nodes']:
        config = node.get('executable', {})
        action = config.get('action') or {}
        if action.get('id') in terminal:
            config['decisions'] = []
        for key in ('preconditions', 'satisfied_when'):
            conditions = config.get(key, [])
            if isinstance(conditions, list):
                for condition in conditions:
                    fallback = condition.get('on_false') if isinstance(condition, dict) else None
                    if isinstance(fallback, dict) and fallback.get('id') in terminal:
                        del condition['on_false']
    return graph


def _event(store, body):
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        last = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id DESC LIMIT 1",
                          (body['kind'],)).fetchone()
        if last is None or last['body'] != canonical(body):
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical(body),))


def review(store, persist=True):
    """Collect owned evidence, update its TMS subgraph, and propose one route."""
    from rds_tms_store import current, save
    from rds_quick import cas_json, choice
    from rds_advisor import RDSAdvisor
    from rds_advisor_search import evaluate_condition
    with store._db(True) as db:
        db.execute('BEGIN')
        state = _state(store, db)
    fingerprint = _fingerprint(state)
    telemetry = _telemetry(state)
    policy = validate_policy(store, state['contract'])
    nodes, edges, coverage, files = _collect(store, state)
    saved = current(store.root)
    previous = deepcopy(saved['dependency_map']) if saved else {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}
    spec = {**previous, 'nodes': [n for n in previous['nodes'] if not n['id'].startswith(OWNED_PREFIX)] + nodes,
            'hyperedges': [e for e in previous['hyperedges'] if not e['id'].startswith(OWNED_PREFIX)] + edges,
            'goals': [g for g in previous['goals'] if not g.startswith(OWNED_PREFIX)],
            'limits': {**previous.get('limits', {}), 'max_nodes': 4096, 'max_hyperedges': 4096}}
    if policy:
        facts = _facts(spec)
        from rds_domain_confirmation import goal_conditions as confirmation_goals
        effective_goals = confirmation_goals(policy)
        for index, condition in enumerate(effective_goals):
            evaluated = evaluate_condition(condition, facts)
            ident = 'goal:' + str(index)
            spec['nodes'].append(_node(ident, {'TRUE': 'SUPPORTED', 'FALSE': 'CONTRADICTED', 'UNKNOWN': 'UNKNOWN'}[evaluated['truth']],
                                      {'locator': 'frozen goal predicate ' + str(index)}, predicate=evaluated,
                                      scientific_support='UNKNOWN'))
            spec['goals'].append(OWNED_PREFIX + ident)
            spec['hyperedges'].append({'id': OWNED_PREFIX + 'predicate:' + str(index),
                                      'premises': [OWNED_PREFIX + 'fact:' + condition['fact']],
                                      'conclusion': OWNED_PREFIX + ident, 'status': 'PROPOSED',
                                      'source': {'locator': 'frozen predicate comparison'}})
    snapshot_sha = saved['sha256'] if saved else None
    if persist:
        def validate_current(db):
            _reconcile(_state(store, db), fingerprint, telemetry,
                       'Owned state changed during evidence collection; retry review')
        snapshot_sha = save(store.root, spec, expected=snapshot_sha, source_base=store.root,
                            validate_current=validate_current)
        actual = current(store.root)
        require(actual is not None and actual['sha256'] == snapshot_sha, 'Owned dependency snapshot changed; retry review')
        spec = actual['dependency_map']
    result = {'status': 'REVIEWED' if policy else 'COLLECTION_ONLY', 'assurance': ASSURANCE,
              'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN', 'fingerprint': fingerprint,
              'telemetry': telemetry,
              'coverage': coverage, 'snapshot_sha256': snapshot_sha, 'selected_run': None, 'selected_manifest': None,
              'recommendations': [], 'warnings': [], 'next_move': None, 'evidence_files': files}
    if policy:
        context = deepcopy(policy['context'])
        if 'confirmation' in policy:
            context['decision']['goal_conditions'] = effective_goals
        context['facts'] = _facts(spec)
        context['dependency_map'] = spec
        budget = {r['resource']: max(0, r['cap'] - r['spent'] - r['charged'] - r['reserved']) for r in state['budget']}
        identity = {'resource': 'wall_seconds', 'unit': 'seconds', 'comparison_group': 'owned-project',
                    'source': {'locator': 'owned project budget'}}
        # Existing reservations belong to those active routes. Restoring them
        # for input review prevents a budget-only false block, while each new
        # admission below still uses the actually unreserved resource vector.
        owned_reservations = sum(r['resource_estimates']['wall_seconds'] for r in state['runs']
                                 if r['status'] in {'RESERVED', 'RUNNING'})
        context['budget'] = {**identity, 'value': budget.get('wall_seconds', 0) + owned_reservations}
        context['costs'] = {r['candidate']: {**identity, 'value': r['manifest']['resource_estimates']['wall_seconds']}
                            for r in policy['routes']}
        run_index = {r['id']: r for r in state['runs']}
        priority = tuple(route['candidate'] for route in policy['routes']
                         if run_index.get(route['manifest']['id'], {}).get('status') in {'RESERVED', 'RUNNING'})
        state_for_advisor = {'contract': state['contract'], 'contract_sha256': digest(state['contract']),
                             'advisor_context': context, 'runs': state['runs'], 'receipts': state['receipts']}
        from rds_owned_history import bind_graph
        graph = bind_graph(store, state['contract'], _dispatch_graph(policy, state['runs']))
        tool_reports = []
        if policy.get('tool_bindings'):
            from rds_owned_tools import applicable, gate_graph
            tool_reports = applicable(store, state['contract'], context['facts'])
            graph = gate_graph(graph, tool_reports, context['facts'])
        recommendations = RDSAdvisor(store.root).recommend_next_directions(
            state_for_advisor, graph, priority_action_ids=priority)
        advice = {'advisor_type': 'STRATEGIC_RESEARCH_ADVICE', 'recommendations': recommendations,
                  'recommendations_count': len(recommendations)}
        result.update(advice=advice, recommendations=recommendations, context=context)
        searches = [r['search'] for r in recommendations if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH']
        selection = searches[0]['selection_review'] if searches else {}
        if policy.get('tool_bindings'):
            from rds_owned_tools import consumption
            result['tool_utilization'] = consumption(store, state, context['facts'], tool_reports, selection, searches)
        result['warnings'] = deepcopy(selection.get('flags', []))
        result['next_move'] = deepcopy(selection.get('next_move'))
        ready = {c['action']['id']: c for search in searches for c in search['candidates'] if c['status'] == 'READY'}
        active = [r for r in policy['routes'] if run_index.get(r['manifest']['id'], {}).get('status') in {'RESERVED', 'RUNNING'}
                  and r['candidate'] in ready]
        eligible = []
        for route in policy['routes']:
            manifest = route['manifest']
            if manifest['id'] in run_index or route['candidate'] not in ready:
                continue
            if manifest.get('control_id') and manifest['control_id'] not in run_index:
                continue
            if all(amount <= budget.get(resource, 0) for resource, amount in manifest['resource_estimates'].items()):
                eligible.append(route)
        from rds_feasibility import assess
        feasibility = assess(store, state)
        if feasibility is not None:
            result['feasibility'] = feasibility
            goal_confirmed = selection.get('goal', {}).get('status') == 'TRUE'
            if goal_confirmed:
                feasibility['next_action'] = 'GOAL_PREDICATES_CONFIRMED'
                feasibility['repair_request'] = None
            allowed = set(feasibility['admitted_runs'])
            active = [r for r in active if r['manifest']['id'] in allowed]
            eligible = [r for r in eligible if r['manifest']['id'] in allowed]
            if feasibility['bounded_pilots']:
                # A missing ETA is resolved by bounded measurement before a long route.
                pilot_order = {p: i for i, p in enumerate(feasibility['bounded_pilots'])}
                eligible.sort(key=lambda r: pilot_order.get(r['manifest']['id'], len(pilot_order)))
            if not active and not eligible and not goal_confirmed:
                if allowed:
                    # Cost admission does not establish execution readiness. The
                    # ordinary dependency/method gates above retain authority.
                    feasibility['execution_readiness'] = 'BLOCKED'
                    feasibility['next_action'] = 'RESOLVE_EXECUTION_PREREQUISITES'
                    feasibility['repair_request'] = {
                        'kind': 'RESOLVE_PREMISE', 'authorization': 'UNCHANGED',
                        'reason': 'Forecast-admitted routes have no currently eligible execution step; resolve the ordinary prerequisites without bypassing them',
                        'admitted_runs': sorted(allowed),
                        'prompt': 'Inspect the current candidate prerequisite and method reports; obtain the missing original evidence or propose an authorized method revision, then run project next. Preserve the goal, budget and dependency gates.'}
                    if result['next_move'] is None:
                        result['next_move'] = deepcopy(feasibility['repair_request'])
                elif feasibility['repair_request'] is not None:
                    result['next_move'] = feasibility['repair_request']
                result['warnings'].append({'kind': 'EXECUTION_PREREQUISITE_BLOCK' if allowed
                                          else 'PREDICTIVE_FEASIBILITY_BLOCK', 'plans': feasibility['plans']})
        frontier = [r for r in eligible if not ready[r['candidate']].get('dominated_by')]
        chosen = (active or frontier or eligible)
        if 'graph_ranker' in policy:
            from rds_graph_ranker import rank
            steering = state.get('steering') or {}
            precedence = ('EVIDENCE_COVERAGE_FAILED' if coverage['errors'] else
                          'GOAL_ALREADY_CONFIRMED' if selection.get('goal', {}).get('status') == 'TRUE' else
                          'ACTIVE_RESERVATION' if active else
                          'FEASIBILITY_PILOT_ORDER' if feasibility and feasibility['bounded_pilots'] else
                          'HUMAN_PREFERENCE' if steering.get('preferred_runs') else
                          'HUMAN_PAUSE' if steering.get('paused') else None)
            ranked, result['graph_ranker'] = rank(policy['graph_ranker'], graph, context['facts'],
                                                frontier, precedence=precedence)
            if result['graph_ranker']['selection_applied']:
                chosen = ranked
        if not coverage['errors'] and chosen and selection.get('goal', {}).get('status') != 'TRUE':
            route = chosen[0]
            # Every dispatch, including a reservation, must still satisfy the
            # ordinary method, history and current evidence checks.
            choice(advice, context, ready[route['candidate']]['id'])
            result.update(selected_run=route['manifest']['id'], selected_manifest=deepcopy(route['manifest']),
                          selection_basis='AVAILABLE_FROZEN_PARETO_THEN_DECLARATION_ORDER_NOT_GLOBAL_OPTIMUM')
            if result.get('graph_ranker', {}).get('selection_applied'):
                result['selection_basis'] = 'AVAILABLE_FROZEN_PARETO_THEN_NEURAL_PREFERENCE_NOT_GLOBAL_OPTIMUM'
        if coverage['errors']:
            result['status'] = 'COLLECTION_FAILED'
            result['warnings'].append({'kind': 'OWNED_EVIDENCE_INCOMPLETE', 'errors': deepcopy(coverage['errors'])})
        if 'confirmation' in policy:
            from rds_domain_confirmation import inspect_confirmation
            try:
                result['confirmation'] = inspect_confirmation(store, state['contract'], state)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                result.update(status='COLLECTION_FAILED', selected_run=None, selected_manifest=None)
                result['warnings'].append({'kind': 'DOMAIN_CONFIRMATION_INTEGRITY', 'error': str(exc)})
    if persist:
        ref = cas_json(store.root, result)
        if result.get('tool_utilization'):
            from rds_owned_tools import record_consumption
            record_consumption(store, result['tool_utilization'], ref)
        _event(store, {'kind': 'OWNED_ADVISOR_REVIEW', 'fingerprint': fingerprint, 'status': result['status'],
                       'report': ref, 'selected_run': result['selected_run'], 'snapshot_sha256': snapshot_sha})
    return result


def prepare_admission(store, spec):
    report = review(store)
    require(report['status'] == 'REVIEWED' and report['selected_run'] == spec.get('id')
            and report['selected_manifest'] == spec, 'Owned Advisor did not select this frozen manifest; inspect project next')
    from rds_owned_history import prepare_decision
    with store._db(True) as db:
        contract = store._contract(db)
    return {'fingerprint': report['fingerprint'], 'selected_run': report['selected_run'],
            'manifest_sha256': digest(spec), 'evidence_files': report['evidence_files'],
            'decision': prepare_decision(store, report, contract),
            'snapshot_sha256': report['snapshot_sha256'], 'telemetry': deepcopy(report['telemetry'])}


def check_admission(store, db, spec, token):
    """No nested write connection: called under the caller's BEGIN IMMEDIATE."""
    require(isinstance(token, dict) and token.get('selected_run') == spec.get('id')
            and token.get('manifest_sha256') == digest(spec), 'Owned admission manifest binding mismatch')
    _reconcile(_state(store, db), token['fingerprint'], token.get('telemetry'),
               'Owned state changed after Advisor selection; retry admission')
    snapshot = db.execute('SELECT sha256,body FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
    require(snapshot is not None and snapshot['sha256'] == token['snapshot_sha256'],
            'Owned dependency snapshot changed after Advisor selection; retry admission')
    saved = strict_json(snapshot['body'])
    require(digest(saved) == snapshot['sha256'], 'Owned dependency snapshot integrity failure')
    from rds_math import blob
    blob(store.root, saved['map'])  # Hash-bound CAS read, no nested ledger connection.
    for item in token['evidence_files']:
        _read_original(store, item)
    contract = store._contract(db)
    if contract['advisor_policy'].get('tool_bindings'):
        from rds_owned_tools import applicable
        reports = applicable(store, contract, token['decision']['evidence'])
        binding = next((b for b in contract['advisor_policy']['tool_bindings'] if b['run_id'] == spec['id']), None)
        if binding is not None:
            require(next(r for r in reports if r['candidate'] == binding['candidate'])['status'] == 'APPLICABLE',
                    'Tool applicability changed after Advisor selection; inspect the original qualification')
    from rds_feasibility import check_start
    check_start(store, db, spec['id'])
    return True


def after_finish(store):
    """Receipts are already committed. Collection failure is separately visible."""
    try:
        return review(store)
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        result = {'status': 'COLLECTION_FAILED', 'assurance': ASSURANCE, 'authorization': 'UNCHANGED',
                  'selected_run': None, 'reason': str(exc), 'recovery': 'Run project next to retry collection; do not rerun the experiment'}
        try:
            _event(store, {'kind': 'OWNED_ADVISOR_COLLECTION_FAILED', **result})
        except (ValueError, OSError, sqlite3.Error):
            result['failure_event_persisted'] = False
        return result
