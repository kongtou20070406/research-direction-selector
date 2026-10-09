"""Extract conservative local function candidates; validate with the native runner.

AST admission is a supported subset, not a purity proof or an OS sandbox.
Finite cases qualify local reuse only; no rule, checker or policy auto-promotion.
"""
import ast
import builtins
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
from types import SimpleNamespace

from rds_math import blob, get, put, read_bytes, records
from rds_project import ProjectStore, canonical, digest, file_sha, number, require
from rds_quick import cas_bytes, execute, _charge_ledger
from rds_mutation import mutation

BUILTINS = set('abs all any bool dict enumerate filter float frozenset int isinstance len list map max min next range reversed round set sorted str sum tuple zip ArithmeticError AssertionError IndexError KeyError TypeError ValueError ZeroDivisionError RecursionError UnicodeError'.split())
METHODS = set('append extend insert pop remove clear copy count index reverse sort get items keys values update union intersection difference is_integer as_integer_ratio bit_length conjugate limit_denominator encode split replace isascii isdigit startswith endswith strip upper add'.split())


def _name(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value), 'Tool needs a safe name of at most 64 characters')
    return value


def _immutable(value):
    return type(value) in {int, float, str, bool, type(None)} or isinstance(value, tuple) and all(_immutable(v) for v in value)


def extract_function(raw, entry):
    require(len(raw) <= 512 * 1024, 'Tool source exceeds 512 KiB; extract a smaller explicit module')
    tree = ast.parse(raw.decode('utf-8-sig'))
    require(sum(1 for _ in ast.walk(tree)) <= 30000, 'Tool AST exceeds its bounded extraction limit')
    require(isinstance(entry, str) and entry.isidentifier(), 'Entry must be a Python function name')
    definitions = {}
    for node in tree.body:
        names = ([node.name] if isinstance(node, ast.FunctionDef) else
                 [a.asname or a.name.split('.')[0] for a in node.names] if isinstance(node, (ast.Import, ast.ImportFrom)) else
                 [node.targets[0].id] if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) else [])
        for name in names:
            require(name not in definitions, 'Duplicate top-level binding: ' + name)
            definitions[name] = node
    require(entry in definitions and isinstance(definitions[entry], ast.FunctionDef), 'Entry is not a top-level function')
    selected, pending = {}, [entry]
    while pending:
        name = pending.pop()
        if name in selected or name in BUILTINS and name not in definitions:
            continue
        require(name in definitions, 'Unsupported external dependency: ' + name)
        node = definitions[name]
        selected[name] = node
        require(len(selected) <= 64, 'Tool dependency closure exceeds 64 bindings')
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            json_loads = (isinstance(node, ast.ImportFrom) and node.module == 'json'
                          and all(a.name == 'loads' for a in node.names))
            require((all(m in {'math', 'fractions'} for m in modules) or json_loads)
                    and not getattr(node, 'level', 0) and all(a.name != '*' for a in node.names),
                    'Only explicit math/fractions imports and from json import loads are supported')
            if isinstance(node, ast.ImportFrom):
                require(all(not a.name.startswith('_') for a in node.names), 'Private imports are unsupported')
            continue
        if isinstance(node, ast.Assign):
            value = ast.literal_eval(node.value)
            require(_immutable(value), 'Only literal immutable global constants are supported')
            continue
        require(not node.decorator_list, 'Decorated functions need explicit manual extraction')
        for default in node.args.defaults + [v for v in node.args.kw_defaults if v is not None]:
            require(_immutable(ast.literal_eval(default)), 'Mutable defaults need explicit manual extraction')
        local = {arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs}
        local.update(a.arg for a in (node.args.vararg, node.args.kwarg) if a)
        for child in ast.walk(node):
            require(not isinstance(child, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal, ast.Import,
                                           ast.ImportFrom, ast.With, ast.AsyncWith, ast.Yield, ast.YieldFrom)),
                    'Unsupported dynamic or stateful function construct')
            require(not isinstance(child, (ast.FunctionDef, ast.Lambda)) or child is node,
                    'Nested functions need explicit manual extraction')
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                local.add(child.id)
            if isinstance(child, ast.ExceptHandler) and child.name:
                local.add(child.name)
            if isinstance(child, ast.Attribute):
                require(not child.attr.startswith('_'), 'Private attribute access is unsupported')
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                owner = child.func.value
                imported = definitions.get(owner.id) if isinstance(owner, ast.Name) else None
                require(isinstance(imported, (ast.Import, ast.ImportFrom)) or child.func.attr in METHODS,
                        'Unsupported method call: ' + child.func.attr)
        # Annotations are declarations, and can introduce unrelated imports; omit them.
        node.returns = None
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs + [a for a in (node.args.vararg, node.args.kwarg) if a]:
            arg.annotation = None
        pending.extend(n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                       and isinstance(n.ctx, ast.Load) and n.id not in local
                       and (n.id not in BUILTINS or n.id in definitions))
    ordered = sorted(set(selected.values()), key=lambda n: n.lineno)
    code = ('"""RDS extracted candidate; purity and general correctness are unproved."""\n' +
            ast.unparse(ast.Module(body=ordered, type_ignores=[])) + '\n').encode('utf-8')
    return code, sorted(selected)


@mutation()
def extract(root, source, entry, name):
    from rds_campaign import enforce
    enforce(root)
    name = _name(name)
    original = read_bytes(source)
    code, included = extract_function(original, entry)
    origin = cas_bytes(root, original)
    origin['path'] = Path(origin['path']).relative_to(Path(root).resolve()).as_posix()
    value = put(root, 'tool:' + name, 'tool', code, data={'name': name, 'entry': entry,
                'origin': origin, 'included_globals': included, 'purity': 'NOT_PROVED'})
    return {'status': 'CANDIDATE_ONLY', 'id': value['id'], 'name': name,
            'source_sha256': value['asset']['sha256'], 'purity': 'NOT_PROVED', 'research_policy_gain_measured': False}


def _cases(raw):
    from rds_cli import strict_json
    cases = strict_json(raw.decode('utf-8-sig'))
    require(isinstance(cases, list) and 1 <= len(cases) <= 64, 'Supply 1–64 explicit local cases')
    for case in cases:
        require(isinstance(case, dict) and set(case) <= {'args', 'kwargs', 'expected', 'expected_error'}
                and isinstance(case.get('args', []), list) and isinstance(case.get('kwargs', {}), dict)
                and ('expected' in case) != ('expected_error' in case), 'Invalid tool case')
        if 'expected_error' in case:
            error = getattr(builtins, case['expected_error'], None) if isinstance(case['expected_error'], str) else None
            require(isinstance(error, type) and issubclass(error, Exception), 'Expected error must name a built-in exception')
    return cases


DRIVER = '''import json
from candidate import ENTRY
cases = json.loads(open('cases.json', encoding='utf-8-sig').read())
rows = []
for i, case in enumerate(cases):
    try:
        result = ENTRY(*case.get('args', []), **case.get('kwargs', {}))
        passed = 'expected' in case and json.dumps(result, sort_keys=True, allow_nan=False) == json.dumps(case['expected'], sort_keys=True, allow_nan=False)
    except Exception as exc:
        passed = case.get('expected_error') == type(exc).__name__
    rows.append({'case': i, 'passed': passed})
report = {'status': 'PASS' if all(r['passed'] for r in rows) else 'FAIL', 'case_count': len(rows), 'cases': rows}
with open('outputs/result.json', 'w', encoding='utf-8') as stream:
    json.dump(report, stream, sort_keys=True, allow_nan=False)
raise SystemExit(0 if report['status'] == 'PASS' else 1)
'''


def _preparation_contract(store, db):
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
    if table and db.execute('SELECT 1 FROM contract WHERE id=1').fetchone():
        contract = store._contract(db)
        require('advisor_policy' not in contract,
                'Qualify finite tool inputs before this owned project init; live work must use its frozen budget and routes')
        return True  # Initialized legacy flow retains its existing native entry.
    return False


def _begin_preparation(store, request):
    """Freeze qualification intent against init in the existing root ledger."""
    _qualification_root(store.root)
    from rds_artifacts import strict_json
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        from rds_campaign import detached_admission
        detached_admission(store.root, db)
        if _preparation_contract(store, db):
            return
        db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS events_no_{operation.lower()} BEFORE {operation} ON events "
                       "BEGIN SELECT RAISE(ABORT,'events are append-only'); END")
        rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_STARTED' LIMIT 129").fetchall()
        require(len(rows) <= 128, 'Native preparation intents exceed 128')
        existing = [strict_json(row['body']) for row in rows
                    if strict_json(row['body'])['request']['token'] == request['token']]
        event = {'kind': 'TOOL_PREPARATION_STARTED', 'request': request, 'request_sha256': digest(request)}
        if existing:
            require(len(existing) == 1 and existing[0] == event, 'Native preparation identity changed')
        else:
            require(len(rows) < 128, 'Native preparation intents exceed 128')
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))


def _validation_context():
    """Observed local runtime identity, not a measurement of system load."""
    host = platform.node()
    try:
        executable = file_sha(sys.executable)
    except OSError:
        executable = None
    return {'schema': 'rds-local-runtime-v1',
            'host_sha256': hashlib.sha256(host.encode('utf-8')).hexdigest() if host else None,
            'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
            'processor': platform.processor(), 'logical_cpus': os.cpu_count(),
            'python': sys.version, 'implementation': sys.implementation.name,
            'executable_sha256': executable}


def _charge_validation(ledger, workspace, token, timeout):
    """Retain a previously charged native attempt, including interrupted jobs."""
    parent = ProjectStore(ledger)
    state = parent.snapshot(check_bindings=True)
    require(not state['binding_check']['errors'], 'Parent validation budget bindings changed')
    request = {'tool_validation': token}
    with parent._db(True) as db:
        contract = parent._contract(db)
        require('advisor_policy' not in contract and 'stop_policy' not in contract
                and 'maintenance_allowance' not in contract and 'execution_policy' not in contract
                and set(contract['budget']) == {'wall_seconds'},
                'Tool validation requires an ordinary wall-only parent ledger')
        rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                          "AND json_extract(body,'$.job_root')=? LIMIT 2", (str(workspace),)).fetchall()
    if not rows:
        return _charge_ledger(ledger, workspace, request, timeout)
    prior = json.loads(rows[0]['body'])
    require(len(rows) == 1 and prior['request_sha256'] == digest(request)
            and prior['resource'] == 'wall_seconds' and prior['amount'] == timeout,
            'Retained validation allowance differs; no new charge or launch')
    job_root = workspace / '.rds' / 'exec' / 'tool-check'
    require(job_root.is_dir(), 'Charged validation has no retained native job; inspect it without another launch')
    job = ProjectStore(job_root)
    require(job.path.is_file(), 'Charged validation has no retained native job; inspect it without another launch')
    require(not job.snapshot(check_bindings=True)['binding_check']['errors'], 'Retained validation bindings changed')


def _qualification_root(root):
    from rds_campaign import binding, enforce
    enforce(root)
    require(binding(root) is None,
            'Bound campaign tool qualification requires an existing approved canonical project route; '
            'public validation cannot create another preparation experiment')


def validate(root, name, case_file, timeout=10, ledger=None, *, comparison=None):
    _qualification_root(root)
    store = ProjectStore(root)
    if store.path.is_file():
        with store._db(True) as db:
            _preparation_contract(store, db)  # Repeat atomically before launch.
    name = _name(name)
    candidate = get(root, 'tool:' + name)
    require(candidate is not None, 'Extract a named tool candidate first')
    code, cases = blob(root, candidate['asset']), read_bytes(case_file)
    _cases(cases)
    timeout = number(timeout, 'tool timeout', True)
    require(timeout <= 60, 'Tool validation is bounded to 60 seconds')
    driver = DRIVER.replace('ENTRY', candidate['data']['entry']).encode('utf-8')
    context = _validation_context()
    scope = None
    if comparison is not None:
        plan = get(root, comparison['id'])
        require(plan == comparison and plan['kind'] == 'note'
                and plan['data'].get('type') == 'tool-comparison-plan'
                and {'id': candidate['id'], 'sha256': digest(candidate)} in plan['dependencies'],
                'Tool comparison needs its declared bound plan')
        scope = {'id': plan['id'], 'sha256': digest(plan)}
    token = digest({'candidate': digest(candidate), 'cases': hashlib.sha256(cases).hexdigest(),
                    'driver': hashlib.sha256(driver).hexdigest(), 'controller': file_sha(__file__), 'timeout': timeout,
                    'measurement_context': context, 'comparison': scope,
                    'ledger': str(Path(ledger).resolve()) if ledger else None})[:32]
    workspace = Path(root).resolve() / '.rds' / 'rsi' / 'tool-checks' / token
    require(workspace.resolve().is_relative_to(Path(root).resolve()), 'Tool workspace escapes project')
    _begin_preparation(store, {'token': token, 'candidate': {'id': candidate['id'], 'sha256': digest(candidate)},
                             'cases_sha256': hashlib.sha256(cases).hexdigest(),
                             'driver_sha256': hashlib.sha256(driver).hexdigest(), 'timeout_seconds': timeout,
                             'ledger': str(Path(ledger).resolve()) if ledger else None,
                             'job_root': (workspace / '.rds/exec/tool-check').relative_to(store.root).as_posix(),
                             'validation_id': 'validation:' + token})
    from rds_mutation import mutation
    with mutation():
        _qualification_root(root)
        workspace.mkdir(parents=True, exist_ok=True)
        for filename, raw in [('candidate.py', code), ('cases.json', cases), ('driver.py', driver)]:
            path = workspace / filename
            if path.exists():
                require(read_bytes(path) == raw, 'Frozen validation input changed')
            else:
                with path.open('xb') as stream:
                    stream.write(raw)
    if ledger:
        _charge_validation(ledger, workspace, token, timeout)
    args = SimpleNamespace(root=str(workspace), name='tool-check', timeout=timeout, argv=['driver.py'],
                           bind=['data=cases.json'], output=['outputs/result.json'], background=False,
                           ledger=None, choose=None)
    run = execute(args, _native_preparation_root=store.root)
    receipt = run.get('receipt')
    if receipt is None:
        # A retained RESERVED/RUNNING native job has no final evidence yet.
        # Do not freeze UNKNOWN under the eventual terminal validation identity.
        return {'status': 'UNKNOWN', 'id': None, 'name': name, 'job_root': run['job_root'],
                'execution_started': run['execution_started'], 'mathematical_status': 'UNKNOWN',
                'research_policy_gain_measured': False}
    status = ('LOCAL_CASES_PASSED' if receipt and receipt['run_status'] == 'SUCCEEDED' else
              'FAILED' if receipt and receipt['run_status'] == 'FAILED' else 'UNKNOWN')
    record = put(root, 'validation:' + token, 'tool-validation', canonical({'receipt': receipt}).encode('utf-8'),
                 dependencies=[candidate['id']], data={'name': name, 'status': status,
                     'job_root': str(Path(run['job_root']).relative_to(Path(root).resolve())),
                     'run_id': 'tool-check', 'receipt_sha256': digest(receipt) if receipt else None,
                     'cases_sha256': hashlib.sha256(cases).hexdigest(), 'confirmation': 'REUSED_DEVELOPMENT_CASES',
                     'measurement_context': context, 'comparison': scope,
                     'total_budget': 'CHARGED_PARENT_LEDGER' if ledger else 'UNKNOWN'})
    return {'status': status, 'id': record['id'], 'name': name, 'job_root': run['job_root'],
            'execution_started': run['execution_started'], 'mathematical_status': 'UNKNOWN', 'research_policy_gain_measured': False}


def _validation_receipt(root, value, candidate):
    """Recheck native provenance before interpreting either success or failure."""
    require(value and value['kind'] == 'tool-validation'
            and value['dependencies'] == [{'id': candidate['id'], 'sha256': digest(candidate)}], 'No bound passing local validation')
    workspace = (Path(root).resolve() / value['data']['job_root']).resolve()
    require(workspace.is_relative_to((Path(root).resolve() / '.rds' / 'rsi' / 'tool-checks').resolve()), 'Validation job escapes project')
    state = ProjectStore(workspace).snapshot(check_bindings=True)
    require(not state['binding_check']['errors'], 'Validation bindings changed')
    job = workspace
    # Recheck the actual frozen evaluator and candidate, not a supplied PASS flag.
    require(read_bytes(job / 'candidate.py') == blob(root, candidate['asset'])
            and read_bytes(job / 'driver.py') == DRIVER.replace('ENTRY', candidate['data']['entry']).encode('utf-8')
            and file_sha(job / 'cases.json') == value['data']['cases_sha256'], 'Validation code or cases do not match')
    _cases(read_bytes(job / 'cases.json'))
    receipt = next((r for r in state['receipts'] if r['run_id'] == value['data']['run_id']), None)
    require(receipt and digest(receipt) == value['data']['receipt_sha256'],
            'Validation receipt is not the bound native run')
    for item in receipt['artifacts']:
        path = workspace / item['path']
        require(path.is_file() and file_sha(path) == item['sha256'], 'Validation artifact changed')
    return receipt


def _check_validation(root, value, candidate):
    require(value and value['kind'] == 'tool-validation' and value['data'].get('status') == 'LOCAL_CASES_PASSED',
            'No bound passing local validation')
    receipt = _validation_receipt(root, value, candidate)
    require(receipt['run_status'] == 'SUCCEEDED', 'Validation receipt is not the bound successful run')
    job = Path(root).resolve() / value['data']['job_root']
    cases = _cases(read_bytes(job / 'cases.json'))
    report = json.loads(read_bytes(job / 'outputs' / 'result.json'))
    require(report == {'status': 'PASS', 'case_count': len(cases),
                       'cases': [{'case': i, 'passed': True} for i in range(len(cases))]}, 'Local cases did not all pass')
    targets = {v['data'].get('target') for v in records(root) if v['kind'] == 'refutation'}
    require(not targets.intersection({candidate['id'], value['id'], 'tool-adoption:' + candidate['data']['name'], 'objective'}),
            'A declared refutation requires review before tool reuse')
    return receipt


@mutation()
def register(root, name, validation_id=None):
    from rds_campaign import enforce
    enforce(root)
    name = _name(name)
    candidate = get(root, 'tool:' + name)
    require(candidate is not None, 'Unknown tool candidate')
    if validation_id is None:
        passed = [v for v in records(root) if v['kind'] == 'tool-validation' and v['data']['name'] == name
                  and v['data']['status'] == 'LOCAL_CASES_PASSED']
        require(len(passed) == 1, 'Supply --validation when there is not exactly one passing local validation')
        validation_id = passed[0]['id']
    validation = get(root, validation_id)
    _check_validation(root, validation, candidate)
    code = blob(root, candidate['asset'])
    directory = Path(root).resolve() / '.rds' / 'tools'
    require(directory.resolve().is_relative_to(Path(root).resolve()), 'Tool library escapes project')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + '.py')
    require(path.resolve().parent == directory.resolve(), 'Tool module escapes library')
    if path.exists():
        require(read_bytes(path) == code, 'Registered module changed; use a new tool name')
    else:
        with path.open('xb') as stream:
            stream.write(code)
    value = put(root, 'tool-adoption:' + name, 'tool-adoption', code,
                dependencies=[candidate['id'], validation['id']], data={'name': name, 'validation': validation_id,
                    'module': path.relative_to(Path(root).resolve()).as_posix(), 'assurance': 'LOCAL_CASES_ONLY'})
    return {'status': 'REGISTERED_LOCAL', 'id': value['id'], 'module': str(path), 'assurance': 'LOCAL_CASES_ONLY',
            'mathematical_status': 'UNKNOWN', 'research_policy_gain_measured': False}


def command(args):
    if args.action == 'discover':
        from rds_tool_calls import discover
        return discover(args.root, name=args.name, obligation=args.obligation, limit=args.limit)
    if args.action == 'extract':
        return extract(args.root, args.source, args.entry, args.name)
    if args.action == 'validate':
        return validate(args.root, args.name, args.cases, args.timeout, args.ledger)
    if args.action == 'compare':
        _qualification_root(args.root)
        from rds_tool_compare import compare
        return compare(args.root, args.baseline, args.candidate, args.cases, args.precision_key,
                       args.precision, args.timeout, args.min_speedup, args.ledger)
    if args.action == 'register':
        return register(args.root, args.name, args.validation)
    if args.action == 'use':
        value = get(args.root, 'tool-adoption:' + _name(args.name))
        require(value is not None, 'Tool is not registered for local reuse')
        result = register(args.root, args.name, value['data']['validation'])
        if getattr(args, 'output', None):
            with mutation():
                from rds_campaign import enforce
                enforce(args.root)
                path = ProjectStore(args.root)._path(args.output)
                require(path.suffix == '.py', 'Export the tool as a project-relative .py module')
                code = blob(args.root, value['asset'])
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    require(read_bytes(path) == code, 'Tool export would overwrite different content')
                else:
                    with path.open('xb') as stream:
                        stream.write(code)
                result['module'] = str(path)
        return result
    name = _name(args.name) if getattr(args, 'name', None) is not None else None
    return {'status': 'LOCAL_CATALOG', 'tools': [{'id': v['id'], 'kind': v['kind'], 'data': v['data']}
            for v in records(args.root) if v['kind'] in {'tool', 'tool-validation', 'tool-adoption'}
            and (name is None or v['data']['name'] == name)],
            'research_policy_gain_measured': False}
