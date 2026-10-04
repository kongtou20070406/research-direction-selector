"""Prepare a finite, frozen native-tool application; execution remains owned.

The driver calls the verified function on exactly the declared task inputs and
compares original finite expectations. It is ordinary trusted project code,
not an OS sandbox, a general domain checker or scientific proof.
"""
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

from rds_project import ProjectStore, canonical, digest, require


APPLICATION_DRIVER = '''import hashlib, importlib.util, json, pathlib, sys
def raw(ref):
    path = pathlib.Path(ref['path']).resolve()
    if not path.is_relative_to(pathlib.Path.cwd().resolve()):
        raise ValueError('Application input escapes project')
    value = path.read_bytes()
    if hashlib.sha256(value).hexdigest() != ref['sha256']:
        raise ValueError('Application input changed')
    return value
request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
if request['schema'] != 1:
    raise ValueError('Unsupported application request')
code = raw(request['tool'])
inputs = json.loads(raw(request['inputs']).decode('utf-8-sig'))
cases = json.loads(raw(request['cases']).decode('utf-8-sig'))
if not inputs or len(inputs) != len(cases) or len(inputs) > 64:
    raise ValueError('Application needs exact finite task cases')
namespace = {'__name__': 'rds_applied_tool'}
exec(compile(code, request['tool']['path'], 'exec'), namespace)
entry = namespace[request['entry']]
rows = []
for i, (item, case) in enumerate(zip(inputs, cases)):
    if item != {'args': case.get('args', []), 'kwargs': case.get('kwargs', {})}:
        raise ValueError('Task inputs differ from finite qualification')
    try:
        result = entry(*item['args'], **item['kwargs'])
        passed = 'expected' in case and json.dumps(result, sort_keys=True, allow_nan=False) == json.dumps(case['expected'], sort_keys=True, allow_nan=False)
        row = {'case': i, 'passed': passed, 'value': result}
    except Exception as exc:
        passed = case.get('expected_error') == type(exc).__name__
        row = {'case': i, 'passed': passed, 'error': type(exc).__name__}
    rows.append(row)
output = pathlib.Path(request['output']).resolve()
if not output.is_relative_to(pathlib.Path.cwd().resolve()) or output.exists():
    raise ValueError('Application output escapes project or already exists')
report = {'status': 'PASS' if all(r['passed'] for r in rows) else 'FAIL', 'case_count': len(rows),
          'cases': rows, 'tool_sha256': request['tool']['sha256'],
          'inputs_sha256': request['inputs']['sha256'], 'cases_sha256': request['cases']['sha256'],
          'assurance': 'EXACT_TASK_CASES_ONLY', 'scientific_support': 'UNKNOWN'}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, sort_keys=True, allow_nan=False), encoding='utf-8')
raise SystemExit(0 if report['status'] == 'PASS' else 1)
'''


def driver_sha256():
    return hashlib.sha256(APPLICATION_DRIVER.encode('utf-8')).hexdigest()


def prepare(args):
    """Export existing qualified code and a driver; never authorize or run it."""
    from rds_math import blob, get, read_bytes
    from rds_tools import _cases, _check_validation, _name, command
    store = ProjectStore(args.root)
    if store.path.is_file():
        with store._db(True) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
            require(not exists or db.execute('SELECT 1 FROM contract').fetchone() is None,
                    'Prepare the application before project init; a live project needs its authorized method revision')
    name = _name(args.name)
    tool = get(store.root, 'tool:' + name)
    adoption = get(store.root, 'tool-adoption:' + name)
    require(tool is not None and adoption is not None, 'Register the locally validated tool first')
    validation = get(store.root, adoption['data']['validation'])
    _check_validation(store.root, validation, tool)
    cases_path, inputs_path = store._path(args.cases), store._path(args.inputs)
    cases_raw, inputs_raw = read_bytes(cases_path), read_bytes(inputs_path)
    cases = _cases(cases_raw)
    from rds_artifacts import strict_json
    inputs = strict_json(inputs_raw.decode('utf-8-sig'))
    require(inputs == [{'args': c.get('args', []), 'kwargs': c.get('kwargs', {})} for c in cases],
            'Tool qualification must cover exactly the current task inputs')
    require(hashlib.sha256(cases_raw).hexdigest() == validation['data']['cases_sha256'],
            'Current task cases differ from the verified local validation')
    decision = strict_json(read_bytes(args.decision).decode('utf-8-sig'))
    action = strict_json(read_bytes(args.action_file).decode('utf-8-sig'))
    require(isinstance(decision, dict) and isinstance(action, dict) and args.candidate == action.get('id')
            and args.obligation == action.get('target'), 'Application must name the exact decision action and obligation')
    require(isinstance(decision.get('goal_conditions'), list) and decision['goal_conditions'],
            'Application needs the frozen current goal conditions')
    paths = [store._path(v) for v in (args.code_path, args.driver, args.request)]
    require(len(set(paths)) == 3 and all(p.suffix == '.py' for p in paths[:2])
            and paths[2].suffix == '.json', 'Application needs distinct Python code/driver and JSON request files')
    output = store._path(args.output)
    require(output not in paths and output not in (inputs_path, cases_path), 'Application output conflicts with its inputs')
    binding = {'candidate': args.candidate, 'run_id': args.run_id, 'obligation': args.obligation,
               'goal_sha256': digest(decision), 'action_sha256': digest(action),
               'tool': {'id': tool['id'], 'sha256': digest(tool)},
               'validation': {'id': validation['id'], 'sha256': digest(validation)},
               'adoption': {'id': adoption['id'], 'sha256': digest(adoption)},
               'code_path': args.code_path, 'observation_facts': args.observation_fact,
               'qualification': {'domain': 'EXACT_TASK_CASES',
                                 'task_cases': {'path': args.cases, 'sha256': hashlib.sha256(cases_raw).hexdigest()},
                                 'task_inputs': {'path': args.inputs, 'sha256': hashlib.sha256(inputs_raw).hexdigest()},
                                 'premises': []},
               'application': {'driver': {'path': args.driver, 'sha256': driver_sha256()},
                               'request': {'path': args.request, 'sha256': None}, 'output': args.output}}
    request = {'schema': 1, 'entry': tool['data']['entry'],
               'tool': {'path': args.code_path, 'sha256': tool['asset']['sha256']},
               'inputs': binding['qualification']['task_inputs'], 'cases': binding['qualification']['task_cases'],
               'output': args.output}
    request_raw = canonical(request).encode('utf-8')
    binding['application']['request']['sha256'] = hashlib.sha256(request_raw).hexdigest()
    # Validate all existing destinations before creating any export.
    content = (blob(store.root, adoption['asset']), APPLICATION_DRIVER.encode('utf-8'), request_raw)
    for path, raw in zip(paths, content):
        require(not path.exists() or read_bytes(path) == raw, 'Application export would overwrite different content')
    command(SimpleNamespace(root=str(store.root), action='use', name=name, output=args.code_path))
    for path, raw in zip(paths[1:], content[1:]):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(raw)
    return {'status': 'PREPARED_CANDIDATE', 'binding': binding,
            'argv': [sys.executable, '-B', args.driver, args.request],
            'required_bindings': [{'role': role, 'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}
                                  for role, path, raw in [('code', args.code_path, content[0]),
                                                        ('code', args.driver, content[1]),
                                                        ('config', args.request, content[2]),
                                                        ('data', args.inputs, inputs_raw),
                                                        ('evaluator', args.cases, cases_raw)]],
            'execution_started': False, 'authorization': 'UNCHANGED',
            'assurance': 'EXACT_TASK_CASES_ONLY', 'scientific_support': 'UNKNOWN'}
