"""Bounded composition of existing owned-tool entries; no new execution state."""
import ast
from copy import deepcopy
import sqlite3
import time

from rds_artifacts import _read, strict_json
from rds_costs import sha256, summarize_costs
from rds_project import ProjectStore, canonical, digest, require
from rds_quick import cas_json

SCHEMA = 'rds-tool-composition-v1'
OPS = ('status', 'collect', 'execute_tool', 'costs')
MAX_STEPS = 16
DETAIL_BYTES = 2048


def _details(store, value):
    if len(canonical(value).encode('utf-8')) <= DETAIL_BYTES:
        return deepcopy(value)
    return {'details_omitted': True, 'original': cas_json(store.root, value),
            'total': len(value) if isinstance(value, (dict, list)) else None}


def _review_view(store, advice):
    selection = next((r['search']['selection_review'] for r in advice.get('recommendations', [])
                      if 'selection_review' in r.get('search', {})), {})
    use = advice.get('tool_utilization', {})
    return {'status': advice['status'], 'selected_run': advice.get('selected_run'),
            'reason': _details(store, advice.get('reason')),
            'goal': _details(store, selection.get('goal')),
            'coverage': _details(store, advice.get('coverage', {})),
            'warnings': _details(store, advice.get('warnings', [])),
            'next_move': _details(store, advice.get('next_move')),
            'tools': _details(store, use.get('tools', [])),
            'tool_counts': use.get('counts', {}),
            'original': cas_json(store.root, advice)}


def _signature(root, tool):
    from rds_math import blob
    module = ast.parse(blob(root, tool['asset']).decode('utf-8-sig'))
    function = next(node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == tool['data']['entry'])
    args = function.args
    positional = args.posonlyargs + args.args
    optional_from = len(positional) - len(args.defaults)
    parameters = [{'name': arg.arg, 'kind': 'positional-only' if index < len(args.posonlyargs) else 'positional-or-keyword',
                   'required': index < optional_from} for index, arg in enumerate(positional)]
    parameters += [{'name': arg.arg, 'kind': 'keyword-only', 'required': default is None}
                   for arg, default in zip(args.kwonlyargs, args.kw_defaults)]
    if args.vararg:
        parameters.append({'name': args.vararg.arg, 'kind': 'var-positional', 'required': False})
    if args.kwarg:
        parameters.append({'name': args.kwarg.arg, 'kind': 'var-keyword', 'required': False})
    return parameters


def discover(root, *, name=None, obligation=None, limit=8):
    """Inspect exactly selected local records; no scan, installation or execution."""
    from rds_math import get, records
    from rds_tools import _name
    require(type(limit) is int and 1 <= limit <= 32, 'discovery limit must be 1..32')
    require(obligation is None or isinstance(obligation, str) and 0 < len(obligation) <= 512,
            'obligation must be bounded text')
    if name is not None:
        value = get(root, 'tool:' + _name(name))
        catalog = [value] if value else []
    else:
        catalog = [item for item in records(root) if item['kind'] == 'tool']
    store = ProjectStore(root)
    contract, bindings, state = None, [], None
    if store.path.is_file():
        with store._db(True) as db:
            initialized = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
            initialized = initialized and db.execute('SELECT 1 FROM contract').fetchone()
        if initialized:
            state = store.snapshot()
            contract = state['contract']
            bindings = contract.get('advisor_policy', {}).get('tool_bindings', [])
    selected = []
    for row in sorted(catalog, key=lambda item: item['id']):
        matches = [binding for binding in bindings if binding['tool']['id'] == row['id']
                   and (obligation is None or binding['obligation'] == obligation)]
        if obligation is not None and not matches:
            continue
        selected.append((row, matches))
    items = []
    for row, matches in selected[:limit]:
        tool = get(root, row['id'])  # Verify original record/blob, not just catalog text.
        require(tool is not None and tool['kind'] == 'tool', 'tool record changed during discovery')
        routes = {r['manifest']['id']: r['manifest'] for r in (contract or {}).get('advisor_policy', {}).get('routes', [])}
        items.append({'name': tool['data']['name'], 'entry': tool['data']['entry'],
                      'tool_record_sha256': digest(tool), 'code_sha256': tool['asset']['sha256'],
                      'parameters': _details(store, _signature(root, tool)), 'purity': 'NOT_PROVED',
                      'applicability': 'NOT_CHECKED', 'assurance': 'LOCAL_CATALOG_ONLY',
                      'routes': [{'run_id': b['run_id'], 'obligation': b['obligation'],
                                  'candidate': b['candidate'],
                                  'qualification': _details(store, b.get('qualification')),
                                  'resource_estimates': routes.get(b['run_id'], {}).get('resource_estimates'),
                                  'timeout_seconds': routes.get(b['run_id'], {}).get('timeout_seconds'),
                                  'status': next((r['status'] for r in state['runs'] if r['id'] == b['run_id']), 'UNREGISTERED')}
                                 for b in matches[:8]],
                      'route_count': len(matches), 'routes_omitted': max(0, len(matches) - 8),
                      'routes_original': cas_json(store.root, matches) if len(matches) > 8 else None})
    return {'status': 'CAPABILITIES_FOUND' if selected else 'CAPABILITY_GAP', 'tools': items,
            'matched_count': len(selected), 'omitted_count': max(0, len(selected) - limit),
            'filters': {'name': name, 'obligation': obligation},
            'contract_sha256': digest(contract) if contract else None,
            'execution_started': False, 'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN',
            'next_action': 'Compose only frozen run identities after live collection; otherwise qualify and prepare through existing rsi entries',
            'composition': {'schema': SCHEMA, 'command': 'project compose-tools --request <project-relative JSON>',
                            'required': ['contract_sha256', 'max_wall_seconds', 'steps'],
                            'steps': [{'op': op, 'required': ['run_id'] if op == 'execute_tool' else []} for op in OPS],
                            'max_steps': MAX_STEPS, 'max_executions': 8, 'max_wall_seconds': 300,
                            'failure_semantics': 'Stop on failure, unknown dispatch, stale contract, missing evidence or non-selected route; repeat observes original attempts'}}


def _request(store, request):
    require(isinstance(request, dict) and set(request) == {'schema', 'contract_sha256', 'max_wall_seconds', 'steps'}
            and request['schema'] == SCHEMA, 'invalid tool composition request')
    require(sha256(request['contract_sha256']), 'composition needs the current contract SHA256')
    wall = request['max_wall_seconds']
    require(type(wall) in (int, float) and 0 < wall <= 300, 'composition scheduling window must be 0..300 seconds')
    steps = request['steps']
    require(isinstance(steps, list) and 1 <= len(steps) <= MAX_STEPS, 'composition requires 1..16 steps')
    state = store.snapshot()
    require(state['contract_sha256'] == request['contract_sha256'], 'composition contract changed; rediscover before execution')
    policy = state['contract'].get('advisor_policy', {})
    routes = {r['manifest']['id']: r for r in policy.get('routes', [])}
    tools = {b['run_id']: b for b in policy.get('tool_bindings', [])}
    ids = set()
    for step in steps:
        require(isinstance(step, dict) and step.get('op') in OPS, 'unsupported composition operation')
        if step['op'] == 'execute_tool':
            require(set(step) == {'op', 'run_id'} and isinstance(step['run_id'], str), 'execute_tool needs only run_id')
            rid = step['run_id']
            require(rid not in ids, 'duplicate run identity in composition')
            ids.add(rid)
            require(rid in routes and rid in tools and tools[rid]['candidate'] == routes[rid]['candidate'],
                    'composition can execute only an existing frozen qualified tool route')
        else:
            require(set(step) == {'op'}, 'read/collect operation takes no extra parameters')
    require(len(ids) <= 8, 'at most eight tool executions per composition')
    return routes


def _receipt_view(store, receipt, *, existing):
    return {'status': receipt.get('run_status', receipt.get('status', 'UNKNOWN')),
            'run_id': receipt.get('run_id', receipt.get('id')), 'attempt_id': receipt.get('attempt_id'),
            'receipt_sha256': receipt.get('sha256'), 'observed_existing': existing,
            'errors': _details(store, receipt.get('errors', [])),
            'recovery': receipt.get('recovery'), 'original': cas_json(store.root, receipt)}


def compose(root, request_path):
    store = ProjectStore(root)
    request = strict_json(_read(str(request_path), store.root).decode('utf-8-sig'))
    routes = _request(store, request)  # Validate the complete list before any state mutation.
    started = time.monotonic()
    steps, status, reason = [], 'COMPLETED', None
    result = {'schema': 'rds-tool-composition-report-v1', 'request_sha256': digest(request),
              'contract_sha256': request['contract_sha256'], 'authorization': 'UNCHANGED',
              'scientific_support': 'UNKNOWN', 'steps': steps,
              'model_round_trips': None, 'token_savings': None}
    from rds_owned_advisor import review
    for index, step in enumerate(request['steps']):
        record = {'index': index, 'op': step['op']}
        try:
            state = store.snapshot()
            require(state['contract_sha256'] == request['contract_sha256'], 'contract changed during composition')
            remaining = request['max_wall_seconds'] - (time.monotonic() - started)
            if remaining <= 0:
                status, reason = 'HANDOFF', 'Composition scheduling window exhausted; existing project budget is unchanged'
                break
            if step['op'] == 'status':
                record.update(status='OBSERVED', budget=state['budget'],
                              run_counts={s: sum(r['status'] == s for r in state['runs']) for s in sorted({r['status'] for r in state['runs']})},
                              original=cas_json(store.root, state))
            elif step['op'] == 'costs':
                costs = summarize_costs(state['receipts'])
                record.update(status=costs['status'], totals=_details(store, costs['totals']),
                              missing=_details(store, costs['missing']), conflicts=_details(store, costs['conflicts']),
                              original=cas_json(store.root, costs))
                if costs['status'] == 'CONFLICT':
                    status, reason = 'HANDOFF', 'Conflicting original cost identities require inspection'
            elif step['op'] == 'collect':
                advice = review(store)
                record.update(_review_view(store, advice))
                if advice['status'] == 'COLLECTION_FAILED':
                    status, reason = 'HANDOFF', 'Original evidence collection incomplete'
            else:
                rid = step['run_id']
                record['run_id'] = rid
                current = next((r for r in state['runs'] if r['id'] == rid), None)
                if current and (current['attempt_id'] is not None or current['status'] != 'RESERVED'):
                    receipt = store.recover(rid)  # Never launch a previously dispatched attempt.
                    record.update(_receipt_view(store, receipt, existing=True))
                    advice = review(store)
                else:
                    advice = review(store)
                    record['advice'] = _review_view(store, advice)
                    if advice.get('selected_run') != rid or advice['status'] == 'COLLECTION_FAILED':
                        status, reason = 'HANDOFF', 'Requested tool is not the current admitted selection'
                        record['status'] = 'NOT_DISPATCHED'
                        steps.append(record)
                        break
                    manifest = routes[rid]['manifest']
                    remaining = request['max_wall_seconds'] - (time.monotonic() - started)
                    if manifest['timeout_seconds'] > remaining:
                        status, reason = 'HANDOFF', 'Original tool timeout does not fit remaining scheduling window'
                        record['status'] = 'NOT_DISPATCHED'
                        steps.append(record)
                        break
                    if current is None:
                        store.register(manifest)

                    def guard(db, run):
                        require(digest(store._contract(db)) == request['contract_sha256'], 'composition contract changed at admission')
                        require(time.monotonic() - started + manifest['timeout_seconds'] <= request['max_wall_seconds'],
                                'composition scheduling window expired at admission; reservation retained')

                    receipt = store.execute(rid, admission_guard=guard)
                    record.update(_receipt_view(store, receipt, existing=False))
                    advice = getattr(store, 'last_advisor_review', None) or review(store)
                # Always reread original outputs, including after duplicate/recovery requests.
                record['after'] = _review_view(store, advice)
                if record['status'] != 'SUCCEEDED':
                    status, reason = 'HANDOFF', 'Original attempt failed or is unresolved; inspect it before continuing'
                elif advice['status'] == 'COLLECTION_FAILED':
                    status, reason = 'HANDOFF', 'Attempt completed but collection failed; do not rerun the tool'
            steps.append(record)
            if status != 'COMPLETED':
                break
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            record.update(status='UNKNOWN', error=str(exc), next_action='Inspect original project status/receipts; never blindly redispatch')
            steps.append(record)
            status, reason = 'HANDOFF', 'Existing entry rejected or could not complete this step'
            break
    result.update(status=status, reason=reason, remaining_steps=len(request['steps']) - len(steps),
                  internal_steps_observed=len(steps), elapsed_seconds=time.monotonic() - started,
                  next_action='Read referenced originals and continue original decision' if status == 'COMPLETED'
                  else 'Resolve the reported boundary; repeating this request only observes existing dispatched attempts')
    result['original'] = cas_json(store.root, result)
    return result
