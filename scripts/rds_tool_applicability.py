"""Read bounded, exact-task local qualifications without dispatch or adoption.

A local function passing finite cases qualifies only those exact inputs and the
frozen oracle. This adapter does not infer broad domains from names, tags or PASS.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import sys

import rds_math as assets
import rds_tools as tools
from rds_project import ProjectStore, canonical, digest, require

MAX_BINDINGS = 16
MAX_DECLARATION_BYTES = 128 * 1024
FIELDS = {'candidate', 'run_id', 'obligation', 'goal_sha256', 'action_sha256',
          'tool', 'validation', 'adoption', 'code_path', 'qualification',
          'observation_facts', 'application'}
ASSURANCE = 'EXACT_TASK_LOCAL_CASES_NOT_GENERAL_APPLICABILITY_OR_SCIENTIFIC_PROOF'


def _text(value, maximum=128):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _ref(value):
    require(isinstance(value, dict) and set(value) == {'id', 'sha256'}
            and _text(value['id']) and _sha(value['sha256']), 'Invalid native tool record reference')


def _file_ref(store, contract, value, role):
    require(isinstance(value, dict) and set(value) == {'path', 'sha256'}
            and _sha(value['sha256']), 'Invalid task file reference')
    path = store._path(value['path'])
    bindings = [b for b in contract['bindings'] if store._path(b['path']) == path]
    require(bindings and {b['role'] for b in bindings} == {role}
            and all(b['sha256'] == value['sha256'] for b in bindings),
            'Task file must be exclusively bound as ' + role)
    return path


def _action(policy, candidate):
    found = [node['executable']['action'] for node in policy['graph']['nodes']
             if node.get('executable', {}).get('action', {}).get('id') == candidate]
    require(len(found) == 1, 'Tool mapping must name one frozen candidate action')
    return found[0]


def validate_bindings(store, contract, bindings):
    """Check at most 16 frozen declarations; do not execute or write records."""
    require(isinstance(bindings, list) and len(bindings) <= MAX_BINDINGS,
            'Tool mappings must contain at most 16 bindings')
    require(len(canonical(bindings).encode('utf-8')) <= MAX_DECLARATION_BYTES,
            'Tool mappings exceed declaration byte limit')
    policy = contract.get('advisor_policy')
    require(isinstance(policy, dict), 'Tool mappings require a frozen owned policy')
    routes = {r['manifest']['id']: r for r in policy['routes']}
    obligations = {g['fact'] for g in policy['context']['decision']['goal_conditions']}
    obligations.update(n['id'] for n in policy['graph']['nodes'])
    seen = set()
    for value in bindings:
        require(isinstance(value, dict) and set(value) == FIELDS, 'Invalid frozen tool mapping fields')
        require(all(_text(value[k]) for k in ('candidate', 'run_id', 'obligation')),
                'Tool mapping needs bounded candidate, route and obligation identities')
        require((value['candidate'], value['obligation']) not in seen, 'Duplicate candidate/obligation tool mapping')
        seen.add((value['candidate'], value['obligation']))
        require(value['run_id'] in routes and routes[value['run_id']]['candidate'] == value['candidate'],
                'Tool mapping route/candidate mismatch')
        require(value['obligation'] in obligations, 'Tool mapping obligation is outside the frozen goal graph')
        require(value['goal_sha256'] == digest(policy['context']['decision'])
                and value['action_sha256'] == digest(_action(policy, value['candidate'])),
                'Tool mapping goal/action identity mismatch')
        require(value['obligation'] == _action(policy, value['candidate']).get('target'),
                'Tool mapping obligation differs from its action target')
        _ref(value['tool'])
        _ref(value['validation'])
        if value['adoption'] is not None:
            _ref(value['adoption'])
        target = store._path(value['code_path'])
        require({b['role'] for b in contract['bindings'] if store._path(b['path']) == target} == {'code'},
                'Tool export path must be exclusively bound code')
        observations = value['observation_facts']
        require(isinstance(observations, list) and 1 <= len(observations) <= 16
                and len(set(observations)) == len(observations) and all(_text(f) for f in observations),
                'Tool mapping needs bounded distinct observation facts')
        declared = {o['fact'] for o in policy['observations'] if o['run_id'] == value['run_id']}
        require(set(observations) <= declared, 'Tool observations must belong to the frozen application route')
        qualification = value['qualification']
        if qualification is not None:
            require(isinstance(qualification, dict) and set(qualification) ==
                    {'domain', 'task_cases', 'task_inputs', 'premises'}
                    and _text(qualification['domain'], 512), 'Invalid finite task qualification')
            _file_ref(store, contract, qualification['task_cases'], 'evaluator')
            _file_ref(store, contract, qualification['task_inputs'], 'data')
            premises = qualification['premises']
            require(isinstance(premises, list) and len(premises) <= 16,
                    'Task qualification premises exceed bound')
            for condition in premises:
                require(isinstance(condition, dict) and set(condition) == {'fact', 'op', 'value'}
                        and _text(condition['fact']) and condition['op'] in {'eq', 'ne', 'in', 'lt', 'lte', 'gt', 'gte'},
                        'Invalid task qualification premise')
        application = value['application']
        if application is not None:
            require(qualification is not None and isinstance(application, dict)
                    and set(application) == {'driver', 'request', 'output'}, 'Invalid tool application binding')
            _file_ref(store, contract, application['driver'], 'code')
            _file_ref(store, contract, application['request'], 'config')
            require(application['output'] in routes[value['run_id']]['manifest']['outpaths'],
                    'Tool application output is outside its frozen route')
    return deepcopy(bindings)


def application_request(binding, entry, source_sha256):
    """Exact request identity consumed by the fixed host-owned application driver."""
    qualification, application = binding['qualification'], binding['application']
    return {'schema': 1, 'entry': entry,
            'tool': {'path': binding['code_path'], 'sha256': source_sha256},
            'inputs': deepcopy(qualification['task_inputs']),
            'cases': deepcopy(qualification['task_cases']), 'output': application['output']}


def _record(root, ref, kind):
    value = assets.get(root, ref['id'])
    require(value is not None and value['kind'] == kind and digest(value) == ref['sha256'],
            'Native tool record missing, wrong kind or stale identity')
    return value


def _bytes(store, contract, ref, role):
    path = _file_ref(store, contract, ref, role)
    raw = assets.read_bytes(path)
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'Current task binding changed')
    return raw


def _premises(store, contract, conditions, supplied):
    if not conditions:
        return []
    # Reread program-owned observations; serialized fact dictionaries cannot mint evidence.
    from rds_owned_advisor import _state, _collect, _facts
    from rds_advisor_search import evaluate_condition
    with store._db(True) as db:
        state = _state(store, db)
    require(state['contract'] == contract, 'Tool qualification contract is stale')
    nodes, _, coverage, _ = _collect(store, state)
    require(not coverage['errors'], 'Task premise collection is incomplete')
    facts = _facts({'nodes': nodes})
    if supplied is not None:
        require(all(supplied.get(c['fact']) == facts.get(c['fact']) for c in conditions),
                'Task premise override differs from current owned evidence')
    return [evaluate_condition(c, facts) for c in conditions]


def inspect(store, binding, *, contract, action=None, facts=None,
            expected_driver_sha256=None):
    """Return finite applicability and verified refs, never inferred actual use."""
    validate_bindings(store, contract, [binding])
    frozen_action = _action(contract['advisor_policy'], binding['candidate'])
    require(action is None or action == frozen_action, 'Tool inspection action differs from frozen action')
    result = {'candidate': binding['candidate'], 'run_id': binding['run_id'],
              'obligation': binding['obligation'], 'status': 'UNKNOWN', 'assurance': ASSURANCE,
              'reason': None, 'tool': deepcopy(binding['tool']), 'validation': deepcopy(binding['validation']),
              'adoption': deepcopy(binding['adoption']), 'observation_facts': deepcopy(binding['observation_facts']),
              'used': False, 'result_consumed': False, 'scientific_support': 'UNKNOWN',
              'research_policy_gain_measured': False}
    tool = _record(store.root, binding['tool'], 'tool')
    validation = _record(store.root, binding['validation'], 'tool-validation')
    targets = {tool['id'], validation['id'], 'tool-adoption:' + tool['data']['name'], 'objective'}
    if binding['adoption'] is not None:
        targets.add(binding['adoption']['id'])
    for record in assets.records(store.root):
        if record['kind'] == 'refutation' and record['data'].get('target') in targets:
            assets.get(store.root, record['id'])  # Original refutation bytes must still verify.
            return {**result, 'status': 'INAPPLICABLE', 'reason': 'Declared refutation requires tool review'}
    require(validation['dependencies'] == [{'id': tool['id'], 'sha256': digest(tool)}],
            'Tool validation is for a different source')
    if validation['data']['status'] != 'LOCAL_CASES_PASSED':
        return {**result, 'status': 'UNKNOWN', 'reason': 'No successful native local qualification'}
    tools._check_validation(store.root, validation, tool)
    workspace = (store.root / validation['data']['job_root']).resolve()
    native = ProjectStore(workspace).snapshot(check_bindings=True)
    receipt = next(r for r in native['receipts'] if r['run_id'] == validation['data']['run_id'])
    from rds_artifacts import strict_json
    require(strict_json(assets.blob(store.root, validation['asset']).decode('utf-8')) == {'receipt': receipt},
            'Native validation original receipt asset differs from the verified run')
    result['qualification_cost'] = {'receipt_sha256': receipt['sha256'],
                                    'resources': deepcopy(receipt['resources']),
                                    'parent_budget_accounted': False}
    if binding['adoption'] is not None:
        adopted = _record(store.root, binding['adoption'], 'tool-adoption')
        expected = sorted([{'id': v['id'], 'sha256': digest(v)} for v in (tool, validation)], key=lambda v: v['id'])
        require(adopted['dependencies'] == expected and adopted['data']['name'] == tool['data']['name']
                and adopted['data']['validation'] == validation['id']
                and assets.blob(store.root, adopted['asset']) == assets.blob(store.root, tool['asset']),
                'Native adoption differs from the selected tool/validation')
        module = (store.root / adopted['data']['module']).resolve()
        require(module.parent == (store.root / '.rds/tools').resolve()
                and module.is_relative_to(store.root)
                and assets.read_bytes(module) == assets.blob(store.root, tool['asset']),
                'Native adopted module changed')
    if contract.get('objective_sha256') is not None:
        require(tool['objective_sha256'] == validation['objective_sha256'] == contract['objective_sha256'],
                'Native tool qualification belongs to a different objective')
    qualification = binding['qualification']
    if qualification is None or qualification['domain'] != 'EXACT_TASK_CASES':
        return {**result, 'reason': 'Tags, catalogue matches and broad domain declarations are not task qualification'}
    cases_raw = _bytes(store, contract, qualification['task_cases'], 'evaluator')
    inputs_raw = _bytes(store, contract, qualification['task_inputs'], 'data')
    if hashlib.sha256(cases_raw).hexdigest() != validation['data']['cases_sha256']:
        return {**result, 'status': 'INAPPLICABLE', 'reason': 'Local validation covers different task cases'}
    cases = tools._cases(cases_raw)
    inputs = strict_json(inputs_raw.decode('utf-8-sig'))
    require(isinstance(inputs, list) and 1 <= len(inputs) <= 64 and
            all(isinstance(item, dict) and set(item) == {'args', 'kwargs'}
                and isinstance(item['args'], list) and isinstance(item['kwargs'], dict) for item in inputs),
            'Task inputs must contain 1..64 explicit args/kwargs records')
    from rds_tool_application import task_input_identity
    if task_input_identity(inputs) != task_input_identity([{'args': c.get('args', []), 'kwargs': c.get('kwargs', {})} for c in cases]):
        return {**result, 'status': 'INAPPLICABLE', 'reason': 'Current task inputs differ from exact validated cases'}
    code = assets.read_bytes(store._path(binding['code_path']))
    tool_code = assets.blob(store.root, tool['asset'])
    require(code == tool_code,
            'Bound application module differs from qualified native tool')
    require(any(b['path'] == binding['code_path'] and b['role'] == 'code'
                and b['sha256'] == hashlib.sha256(code).hexdigest() for b in contract['bindings']),
            'Bound application code identity changed')
    reports = _premises(store, contract, qualification['premises'], facts)
    result['premises'] = reports
    if any(r['truth'] == 'FALSE' for r in reports):
        return {**result, 'status': 'INAPPLICABLE', 'reason': 'Current owned evidence contradicts a required premise'}
    if any(r['truth'] != 'TRUE' for r in reports):
        return {**result, 'reason': 'Required current-task premise is unknown'}
    application = binding['application']
    if application is not None:
        from rds_tool_application import driver_sha256
        fixed_driver_sha256 = driver_sha256()
        require(expected_driver_sha256 is None or expected_driver_sha256 == fixed_driver_sha256,
                'Application driver identity override differs from fixed implementation')
        expected_driver_sha256 = fixed_driver_sha256
    if application is None or not _sha(expected_driver_sha256):
        return {**result, 'reason': 'No verified fixed application driver/use path'}
    driver = _bytes(store, contract, application['driver'], 'code')
    require(hashlib.sha256(driver).hexdigest() == expected_driver_sha256,
            'Application driver is not the fixed program-owned implementation')
    raw_request = _bytes(store, contract, application['request'], 'config')
    request = strict_json(raw_request.decode('utf-8-sig'))
    expected_request = application_request(binding, tool['data']['entry'], tool['asset']['sha256'])
    require(request == expected_request,
            'Frozen application request differs from tool/task/goal identity')
    route = next(r['manifest'] for r in contract['advisor_policy']['routes'] if r['manifest']['id'] == binding['run_id'])
    expected = [sys.executable, '-B', application['driver']['path'], application['request']['path']]
    require(route['argv'] == expected and expected in contract['allowed_commands'],
            'Application driver argv differs from the exact authorized route')
    return {**result, 'status': 'APPLICABLE', 'reason': 'Exact current-task cases and frozen oracle passed native qualification',
            'case_count': len(cases), 'domain': 'EXACT_TASK_CASES',
            'code_sha256': hashlib.sha256(code).hexdigest(), 'request_sha256': hashlib.sha256(raw_request).hexdigest(),
            'qualification_receipt_sha256': receipt['sha256']}
