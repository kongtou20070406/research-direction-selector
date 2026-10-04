"""Finite tool consumers over the owned ledger, original receipts and facts."""
from copy import deepcopy
import hashlib
import json
import re

from rds_project import ProjectStore, canonical, digest, number, require


def preparation_costs(store, contract, *, db=None):
    """Retain all local native validation spending, including failed candidates.

    These native jobs measure wall time only. A multidimensional owned budget
    needs a resource-aware qualifier before this optional bridge can be used.
    """
    if 'tool_bindings' not in contract.get('advisor_policy', {}):
        return []
    if db is None:
        with store._db(True) as current:
            current.execute('BEGIN')
            return preparation_costs(store, contract, db=current)
    require(db.in_transaction, 'Preparation inventory needs the owning initialization transaction')
    require(set(contract['budget']) == {'wall_seconds'},
            'Native tool application currently needs a wall-only budget; other preparation resources are UNKNOWN')
    from rds_math import blob, get, records
    from rds_artifacts import strict_json
    base = (store.root / '.rds/rsi/tool-checks').resolve()
    require(base.is_relative_to(store.root), 'Native preparation directory escapes project')
    directories = set()
    if base.exists():
        require(base.is_dir(), 'Native preparation directory is invalid')
        for entry in base.iterdir():
            require(len(directories) < 128 and entry.is_dir()
                    and re.fullmatch('[a-f0-9]{32}', entry.name)
                    and entry.resolve().parent == base, 'Native preparation directories exceed bounds or escape project')
            require(entry.resolve() not in directories, 'Native preparation directory alias')
            directories.add(entry.resolve())
    intents = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
        pending = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_STARTED' LIMIT 129").fetchall()
        require(len(pending) <= 128, 'Native preparation intents exceed 128')
        for row in pending:
            event = strict_json(row['body'])
            request = event['request']
            require(set(event) == {'kind', 'request', 'request_sha256'}
                    and event['request_sha256'] == digest(request)
                    and set(request) == {'token', 'candidate', 'cases_sha256', 'driver_sha256',
                                         'timeout_seconds', 'ledger', 'job_root', 'validation_id'}
                    and isinstance(request['token'], str) and re.fullmatch('[a-f0-9]{32}', request['token'])
                    and request['token'] not in intents, 'Native preparation intent identity changed')
            expected = base / request['token'] / '.rds/exec/tool-check'
            require((store.root / request['job_root']).resolve() == expected
                    and request['validation_id'] == 'validation:' + request['token'], 'Native preparation intent path changed')
            intents[request['token']] = request
    rows = []
    published = set()
    for value in records(store.root):
        if value['kind'] != 'tool-validation':
            continue
        require(len(rows) < 128, 'Native preparation coverage exceeds 128 validations')
        job = (store.root / value['data']['job_root']).resolve()
        token = job.parents[2].name
        require(re.fullmatch('[a-f0-9]{32}', token) and job == base / token / '.rds/exec/tool-check'
                and value['id'] == 'validation:' + token and token not in published,
                'Native preparation job escapes project')
        published.add(token)
        if token in intents:
            request = intents[token]
            candidate = get(store.root, request['candidate']['id'])
            require(candidate is not None and candidate['kind'] == 'tool'
                    and digest(candidate) == request['candidate']['sha256']
                    and value['dependencies'] == [request['candidate']]
                    and value['data']['cases_sha256'] == request['cases_sha256']
                    and hashlib.sha256((job.parents[2] / 'driver.py').read_bytes()).hexdigest() == request['driver_sha256'],
                    'Native preparation publication differs from its intent')
        state = ProjectStore(job).snapshot(check_bindings=True)
        require(not state['binding_check']['errors'], 'Native preparation bindings changed')
        receipt = next((r for r in state['receipts'] if r['run_id'] == value['data']['run_id']), None)
        if receipt is not None:
            require(digest(receipt) == value['data']['receipt_sha256']
                    and json.loads(blob(store.root, value['asset'])) == {'receipt': receipt},
                    'Native preparation receipt identity differs')
            resource = receipt['resources']['wall_seconds']
            cost = max(number(resource.get('measured') or 0, 'qualification measured wall'),
                       number(resource.get('charged_estimate') or 0, 'qualification charged wall'))
            status = receipt['run_status']
        else:
            # An unfinished qualification cannot make a reusable candidate;
            # preserve its full reserved allowance rather than forgetting cost.
            resource = state['budget']['wall_seconds']
            cost = number(resource['cap'], 'qualification uncertain wall cap')
            status = 'UNKNOWN_CONSERVATIVE_ALLOWANCE'
        rows.append({'validation': {'id': value['id'], 'sha256': digest(value)},
                     'receipt_sha256': receipt['sha256'] if receipt else None,
                     'status': status, 'wall_seconds': cost})
    require(set(intents) <= published, 'Native preparation is unresolved; reconcile the original qualification before project init')
    require(directories <= {base / token for token in published},
            'Unpublished native preparation job requires reconciliation before project init')
    return rows


def charge_preparation(store, db, contract, costs):
    if not costs:
        return
    total = sum(row['wall_seconds'] for row in costs)
    require(total <= contract['budget']['wall_seconds'], 'Native preparation already exhausted the frozen wall budget')
    db.execute("UPDATE budget SET charged=charged+? WHERE resource='wall_seconds'", (total,))
    db.execute('INSERT INTO events(body) VALUES (?)', (canonical({
        'kind': 'TOOL_PREPARATION_COST', 'accounting': 'RETAINED_NATIVE_VALIDATION_WALL',
        'wall_seconds': total, 'validations': costs, 'scientific_support': 'UNKNOWN'}),))


def applicable(store, contract, facts):
    from rds_tool_applicability import inspect
    from rds_tool_application import driver_sha256
    actions = {n['executable']['action']['id']: n['executable']['action']
               for n in contract['advisor_policy']['graph']['nodes']
               if n.get('executable', {}).get('action')}
    reports = [inspect(store, binding, contract=contract, action=actions[binding['candidate']],
                       facts=facts, expected_driver_sha256=driver_sha256())
               for binding in contract['advisor_policy'].get('tool_bindings', [])]
    with store._db(True) as db:
        costs = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_COST'").fetchall()
    charged = {row['validation']['sha256'] for event in costs
               for row in json.loads(event['body'])['validations']}
    for report in reports:
        if 'qualification_cost' in report:
            report['qualification_cost']['parent_budget_accounted'] = report['validation']['sha256'] in charged
            if report['status'] == 'APPLICABLE' and not report['qualification_cost']['parent_budget_accounted']:
                report.update(status='UNKNOWN', reason='Native preparation cost is not accounted in this project; freeze the consumer interface at project init')
    return reports


def gate_graph(graph, reports, facts):
    """A local reuse eligibility gate creates no goal evidence or authority."""
    gated = deepcopy(graph)
    by_candidate = {report['candidate']: report for report in reports}
    for node in gated['nodes']:
        executable = node.get('executable', {})
        candidate = executable.get('action', {}).get('id')
        if candidate not in by_candidate:
            continue
        fid = 'owned-tool-gate.' + digest(candidate)[:24]
        facts[fid] = {'value': by_candidate[candidate]['status'] == 'APPLICABLE',
                      'reliable': True, 'source': {'locator': 'program checked finite tool eligibility'},
                      'assurance': 'LOCAL_CASES_ONLY', 'scientific_support': 'UNKNOWN'}
        executable.setdefault('preconditions', []).append({'fact': fid, 'op': 'eq', 'value': True})
    return gated


def consumption(store, state, facts, reports, selection, searches=()):
    """Compute real application and precise decision consumers from originals."""
    from rds_artifacts import strict_json
    from rds_owned_advisor import _read_original
    policy = state['contract']['advisor_policy']
    receipts = {r['run_id']: r for r in state['receipts']}
    bindings = {b['candidate']: b for b in policy.get('tool_bindings', [])}
    consumers = {}
    for i, condition in enumerate(selection.get('goal', {}).get('conditions', [])):
        consumers.setdefault(condition['fact'], []).append('goal_conditions[' + str(i) + ']')
    for search in searches:
        for candidate in search.get('candidates', []) + search.get('blocked_candidates', []):
            for step in candidate.get('derivation', []):
                if step.get('step') == 'fact_to_rule':
                    consumers.setdefault(step['fact'], []).append('action:' + step['rule_id'] + '.' + step['role'])
            for condition in candidate.get('discrimination', {}).get('conditions', []):
                consumers.setdefault(condition['fact'], []).append('action:' + candidate['action']['id'] + '.discrimination')
    results = []
    for report in reports:
        binding = bindings[report['candidate']]
        row = {**report, 'used': False, 'use_status': 'NOT_EXECUTED', 'consumed': False,
               'result_consumed': False,
               'consumption_status': 'NO_ORIGINAL_RESULT', 'consumers': [], 'consumed_facts': []}
        receipt = receipts.get(binding['run_id'])
        if receipt is not None:
            row['receipt_sha256'] = receipt['sha256']
            row['run_status'] = receipt['run_status']
            row['use_status'] = 'UNKNOWN_NO_CALL_EVIDENCE'
            application = binding.get('application')
            artifact = next((a for a in receipt['artifacts'] if application and
                             a['path'] == application['output'] and a['kind'] == 'project_output'), None)
            try:
                require(artifact is not None, 'No original application result')
                route = next(r['manifest'] for r in policy['routes'] if r['manifest']['id'] == binding['run_id'])
                bound_contract = next(h['contract'] for h in state['contract_history']
                                      if h['sha256'] == receipt['effective_contract_sha256'])
                require(receipt.get('process_started') is True and receipt['argv'] == route['argv']
                        and receipt['bindings_before'] == bound_contract['bindings'],
                        'Actual application driver/input provenance is unavailable')
                raw = _read_original(store, artifact, keep=True)
                result = strict_json(raw.decode('utf-8-sig'))
                from rds_math import get
                tool = get(store.root, binding['tool']['id'])
                require(result['tool_sha256'] == tool['asset']['sha256']
                        and result['inputs_sha256'] == binding['qualification']['task_inputs']['sha256']
                        and result['cases_sha256'] == binding['qualification']['task_cases']['sha256']
                        and type(result['case_count']) is int and result['case_count'] > 0
                        and len(result['cases']) == result['case_count']
                        and all(r['case'] == i and type(r['passed']) is bool for i, r in enumerate(result['cases'])),
                        'Application result does not bind actual finite calls')
                row.update(used=True, use_status='FROZEN_DRIVER_CALLS_OBSERVED',
                           result_sha256=artifact['sha256'], consumption_status='RESULT_NOT_READ_BY_DECISION')
                for fid in binding['observation_facts']:
                    fact = facts.get(fid, {})
                    observation = next(o for o in policy['observations'] if o['fact'] == fid)
                    source = fact.get('source', {})
                    if (fid in consumers and fact.get('reliable') is True
                            and observation['path'] == application['output']
                            and source.get('sha256') == artifact['sha256']
                            and source.get('receipt_id') == receipt['sha256']
                            and source.get('path', source.get('file')) == application['output']):
                        row['consumed_facts'].append(fid)
                        row['consumers'].extend(consumers[fid])
                if row['consumed_facts']:
                    row.update(consumed=True, result_consumed=True, consumption_status='READ_BY_CURRENT_DECISION',
                               decision_outcome=selection.get('goal', {}).get('status', 'UNKNOWN'))
            except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
                row['use_reason'] = str(exc)
        results.append(row)
    counts = {status.lower(): sum(r['status'] == status for r in results)
              for status in ('APPLICABLE', 'INAPPLICABLE', 'UNKNOWN')}
    counts.update(used=sum(r['used'] for r in results), consumed=sum(r['consumed'] for r in results),
                  applicable_used=sum(r['status'] == 'APPLICABLE' and r['used'] for r in results),
                  applicable_consumed=sum(r['status'] == 'APPLICABLE' and r['consumed'] for r in results))
    denominator = counts['applicable']
    return {'scope': 'DECLARED_CURRENT_OBLIGATIONS_AND_EXACT_TASK_CASES', 'tools': results, 'counts': counts,
            'applicable_use_rate': counts['applicable_used'] / denominator if denominator else None,
            'applicable_consumption_rate': counts['applicable_consumed'] / denominator if denominator else None,
            'zero_denominator': 'NOT_APPLICABLE', 'scientific_support': 'UNKNOWN'}


def record_consumption(store, utilization, report_ref):
    rows = [r for r in utilization['tools'] if r['consumed']]
    if not rows:
        return
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in rows:
            body = {'kind': 'TOOL_RESULT_CONSUMED', 'candidate': row['candidate'],
                    'run_id': row['run_id'], 'receipt_sha256': row['receipt_sha256'],
                    'result_sha256': row['result_sha256'], 'consumed_facts': row['consumed_facts'],
                    'consumers': row['consumers'], 'assurance': 'RECORDED_DECISION_READ_NOT_SCIENTIFIC_PROOF'}
            key = digest(body)
            if not db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='TOOL_RESULT_CONSUMED' "
                              "AND json_extract(body,'$.key')=?", (key,)).fetchone():
                db.execute('INSERT INTO events(body) VALUES (?)', (canonical({**body, 'key': key, 'report': report_ref}),))
