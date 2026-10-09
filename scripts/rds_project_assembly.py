"""Compile explicit research declarations into the existing owned contract.

This module assembles identities and associations. It does not invent research
semantics, qualify tools, execute commands, or maintain a second project state.
"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path

from rds_artifacts import strict_json
from rds_project import ROLES, canonical, digest, file_sha, number, require
from rds_mutation import mutation

MAX_BYTES = 2 * 1024 * 1024
FILE_ROLES = ROLES - {'protocol'}


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, 'Research declaration exceeds 2 MiB')
    return raw


def _object(value, required, optional, label):
    require(isinstance(value, dict) and required <= set(value)
            and set(value) <= required | optional, 'Invalid ' + label + ' fields')


def _condition(value):
    _object(value, {'fact', 'value'}, {'op'}, 'recipe condition')
    op = value.get('op', 'eq')
    require(isinstance(value['fact'], str) and isinstance(op, str)
            and op in {'eq', 'ne', 'in', 'lt', 'lte', 'gt', 'gte'}, 'Invalid recipe condition operator or fact')


def compile_recipe(store, recipe):
    """Return (original contract, generated protocol bytes, assembly metadata).

    No writes. Hash a unique source once here; original initialization and
    admission still perform their own independent checks.
    """
    _object(recipe, {'schema', 'files', 'protocol', 'context', 'routes', 'output_roots', 'budget'},
            {'description', 'output_files'}, 'research recipe')
    require(type(recipe['schema']) is int and recipe['schema'] == 1, 'Recipe schema must be 1')
    require(len(canonical(recipe).encode('utf-8')) <= MAX_BYTES, 'Research recipe exceeds 2 MiB')
    files = recipe['files']
    require(isinstance(files, dict) and not set(files) - FILE_ROLES, 'Invalid recipe file roles')
    budget = recipe['budget']
    require(isinstance(budget, dict) and 'wall_seconds' in budget, 'Recipe budget requires wall_seconds')
    for unit, cap in budget.items():
        require(isinstance(unit, str) and 1 <= len(unit) <= 64, 'Invalid resource name')
        number(cap, 'budget.' + unit)
    contract = {key: deepcopy(recipe[key]) for key in
                ('schema', 'output_roots', 'output_files', 'budget', 'description') if key in recipe}
    require(isinstance(contract['output_roots'], list) and contract['output_roots'], 'Output roots required')
    require(isinstance(contract.get('output_files', []), list), 'Output files must be a list')
    for path in contract['output_roots'] + contract.get('output_files', []):
        store._path(path)
    protocol = recipe['protocol']
    _object(protocol, {'path', 'metadata'}, set(), 'recipe protocol')
    protocol_path = store._path(protocol['path'])
    if os.name == 'nt':
        require(all(not part.endswith(('.', ' ')) for part in Path(protocol['path']).parts),
                'Generated protocol cannot use Windows path aliases')
    metadata = deepcopy(protocol['metadata'])
    require(isinstance(metadata, dict) and not set(metadata).intersection(
        {'path', 'sha256', 'code_sha256', 'config_sha256', 'data_sha256'}),
        'Protocol metadata must omit generated paths and hashes')
    bindings, hashed, seen = [], {}, {}

    def bind(role, path, expected=None, *, merge=False):
        require(role in FILE_ROLES, 'Invalid recipe binding role')
        resolved = store._path(path)
        require(resolved != protocol_path, 'Generated protocol collides with an input')
        require(resolved.is_file(), 'Missing recipe input: ' + path)
        require(len(bindings) < 512, 'Recipe binding limit exceeded')
        key = (resolved, role)
        if resolved not in hashed:
            hashed[resolved] = file_sha(resolved)
        require(expected is None or expected == hashed[resolved], 'Prepared application input changed: ' + path)
        value = {'role': role, 'path': path, 'sha256': hashed[resolved]}
        if key in seen:
            require(merge and seen[key] == value, 'Duplicate or aliased recipe binding')
            return
        seen[key] = value
        bindings.append(value)

    for role, paths in files.items():
        require(isinstance(paths, list) and len(paths) <= 512, 'Recipe files must be bounded path lists')
        for path in paths:
            bind(role, path)
    declarations = recipe['routes']
    require(isinstance(declarations, list) and 1 <= len(declarations) <= 64, 'Recipe needs 1..64 routes')
    context = deepcopy(recipe['context'])
    require(isinstance(context, dict) and isinstance(context.get('decision'), dict)
            and isinstance(context['decision'].get('id'), str), 'Recipe needs an explicit owned decision')
    goals = context['decision'].get('goal_conditions')
    require(isinstance(goals, list) and goals, 'Recipe needs explicit goal conditions')
    for goal in goals:
        _condition(goal)
    nodes, routes, observations, commands, tool_bindings, reports = [], [], [], [], [], []
    for declaration in declarations:
        _object(declaration, {'action', 'run', 'observations'}, {'preconditions', 'prepared_application'}, 'recipe route')
        action, run = deepcopy(declaration['action']), deepcopy(declaration['run'])
        require(isinstance(action, dict) and isinstance(action.get('id'), str), 'Recipe needs an explicit action')
        if 'prepared_application' in declaration:
            report_path = store._path(declaration['prepared_application'])
            require(report_path != protocol_path, 'Generated protocol collides with a preparation report')
            report_raw = _read(report_path)
            report = strict_json(report_raw.decode('utf-8-sig'))
            _object(report, {'status', 'binding', 'argv', 'required_bindings', 'execution_started',
                             'authorization', 'assurance', 'scientific_support'}, set(), 'prepared application')
            require(report['status'] == 'PREPARED_CANDIDATE' and report['execution_started'] is False,
                    'Expected a prepared, unexecuted application report')
            binding = report['binding']
            require(isinstance(binding, dict) and isinstance(binding.get('application'), dict)
                    and isinstance(binding.get('qualification'), dict), 'Missing prepared application identities')
            require(binding['qualification'].get('premises') == [],
                    'Recipe preparation requires the original exact-task adapter without extra premises')
            require(isinstance(report['required_bindings'], list) and len(report['required_bindings']) == 5,
                    'Prepared application requires its five original file bindings')
            for value in report['required_bindings']:
                _object(value, {'role', 'path', 'sha256'}, set(), 'prepared file binding')
                bind(value['role'], value['path'], value['sha256'], merge=True)
            _object(run, {'timeout_seconds', 'resource_estimates'}, {'description'}, 'prepared recipe run')
            require({'run_id', 'candidate'} <= set(binding)
                    and isinstance(binding['application'].get('output'), str), 'Incomplete application route')
            run.update(id=binding['run_id'], arm='tool', control_id=None,
                       argv=deepcopy(report['argv']), outpaths=[binding['application']['output']])
            tool_bindings.append(deepcopy(binding))
            reports.append({'path': declaration['prepared_application'],
                            'sha256': hashlib.sha256(report_raw).hexdigest()})
        else:
            _object(run, {'id', 'arm', 'argv', 'outpaths', 'resource_estimates', 'timeout_seconds'},
                    {'control_id', 'description'}, 'recipe run')
            run.setdefault('control_id', None)
        require(isinstance(run['id'], str), 'Recipe run ID must be a string')
        conditions = deepcopy(declaration.get('preconditions', []))
        require(isinstance(conditions, list), 'Recipe preconditions must be a list')
        for condition in conditions:
            _condition(condition)
        nodes.append({'id': run['id'] + '-recipe', 'executable': {
            'decisions': [context['decision']['id']], 'preconditions': conditions, 'action': action}})
        run['schema'] = 1
        routes.append({'candidate': action['id'], 'manifest': run})
        if run['argv'] not in commands:
            commands.append(deepcopy(run['argv']))
        require(isinstance(declaration['observations'], list), 'Recipe observations must be a list')
        for observation in declaration['observations']:
            _object(observation, {'fact', 'path', 'selector'}, {'format'}, 'recipe observation')
            observations.append({**deepcopy(observation), 'run_id': run['id']})
    require(FILE_ROLES <= {b['role'] for b in bindings}, 'Recipe requires code/config/data/evaluator inputs')
    # JSON object order and the order of a file inventory carry no research
    # meaning. Keep retries stable when an agent reformats either declaration.
    bindings.sort(key=lambda binding: (binding['role'], binding['path']))
    for role in ('code', 'config', 'data'):
        metadata[role + '_sha256'] = store._role_sha({'bindings': bindings}, role)
    protocol_raw = (canonical(metadata) + '\n').encode('utf-8')
    protocol_ref = {'path': protocol['path'], 'sha256': hashlib.sha256(protocol_raw).hexdigest()}
    bindings.append({'role': 'protocol', **protocol_ref})
    for route in routes:
        route['manifest']['protocol'] = deepcopy(protocol_ref)
    policy = {'schema': 1, 'context': context, 'graph': {'nodes': nodes, 'edges': []},
              'routes': routes, 'observations': observations}
    if tool_bindings:
        policy['tool_bindings'] = tool_bindings
    contract.update(bindings=bindings, allowed_commands=commands, advisor_policy=policy)
    from rds_owned_advisor import validate_policy
    validate_policy(store, contract)
    error = store._protocol_error(contract, metadata)
    require(not error, 'Invalid recipe protocol identity: ' + str(error))
    known = {o['fact'] for o in observations} | {f"run.{r['manifest']['id']}.{key}"
        for r in routes for key in ('status', 'completed', 'succeeded', 'failed', 'timed_out')}
    require(all(c['fact'] in known for n in nodes for c in n['executable']['preconditions']),
            'Recipe preconditions must refer to owned observations or lifecycle facts')
    protected = {store._output_key(r['path']) for r in reports}
    require(all(store._output_key(path) not in protected for path in contract.get('output_files', [])
                + [path for r in routes for path in r['manifest']['outpaths']]),
            'Recipe output overwrites a preparation report')
    require(all(store._output_key(path) not in {store._output_key(b['path']) for b in bindings}
                for path in contract.get('output_files', [])), 'Output file aliases a bound input')
    if tool_bindings:
        from rds_tool_applicability import inspect
        from rds_tool_application import driver_sha256
        for binding in tool_bindings:
            result = inspect(store, binding, contract=contract, expected_driver_sha256=driver_sha256())
            require(result['status'] == 'APPLICABLE', 'Prepared application is not currently applicable: ' + str(result['reason']))
    return contract, protocol_raw, {'schema': 'rds-project-assembly-v1', 'protocol': protocol_ref,
        'prepared_applications': reports, 'unique_input_files': len(hashed), 'route_count': len(routes),
        'execution_started': False, 'scientific_support': 'UNKNOWN', 'llm_tokens': 'NOT_MEASURED'}


@mutation()
def initialize(store, recipe_path, *, separate_reason=None):
    """Prepare an immutable protocol, then use the original initializer."""
    from rds_project_lifecycle import check_root
    check_root(store.root, separate_reason=separate_reason)
    source = Path(recipe_path).resolve()
    raw = _read(source)
    recipe = strict_json(raw.decode('utf-8-sig'))
    contract, protocol_raw, summary = compile_recipe(store, recipe)
    protocol = store._path(summary['protocol']['path'])
    require(protocol != source, 'Generated protocol overwrites the recipe')
    outputs = contract.get('output_files', []) + [path for r in contract['advisor_policy']['routes']
                                                for path in r['manifest']['outpaths']]
    require(all(store._output_key(path) != store._output_key(source) for path in outputs),
            'Run output overwrites the recipe')
    # Reject changes before preparing any artifact. initialize independently
    # repeats this check under its existing transaction, including concurrent init.
    if store.path.is_file():
        with store._db(True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone():
                row = db.execute('SELECT sha256 FROM contract WHERE id=1').fetchone()
                require(row is None or row['sha256'] == digest(contract),
                        'Recipe differs from the initialized contract; use the original revision workflow')
    protocol.parent.mkdir(parents=True, exist_ok=True)
    try:
        with protocol.open('xb') as stream:
            stream.write(protocol_raw)
    except FileExistsError:
        require(protocol.is_file() and _read(protocol) == protocol_raw,
                'Generated protocol would overwrite different content')
    from rds_project_lifecycle import initialize as initialize_project
    result = initialize_project(store, contract, mode='full', separate_reason=separate_reason)
    result['assembly'] = {**summary, 'recipe': {'path': str(source), 'sha256': hashlib.sha256(raw).hexdigest()},
                          'contract_sha256': digest(contract)}
    return result
