"""Goal-bound closed-loop tool synthesis (RSI) for Compass campaigns.

When counterexample-filtered synthesis exhausts its grammar, Compass treats the
gap as a missing capability: it extracts candidate feature tools (library
first, then bounded generated families), validates each through the existing
source allowlist, qualifies it against an independent oracle, registers it,
and uses it as a new grammar primitive.

A tool never certifies itself. Qualification requires an oracle triple whose
expected values come only from original receipted measurements:

* known positive   - a law fitted WITHOUT the held-out receipts, using the tool,
                     reproduces every held-out measurement;
* discriminating   - at least one mutant of the tool, substituted into that same
  negative           law, is contradicted by a receipted measurement;
* counterexample   - that killing measurement is retained with its receipt.
  witness

Candidates come from the cross-session library, from offers recorded as
``TOOL_PROPOSED`` (``compass tools --propose`` or a jump response's ``tools``),
and from the built-in base catalogue (``compass tools --catalogue``). An offer
is never evidence: each one meets the same oracle triple.

Status is ``QUALIFIED_FOR_SCOPE`` (goal, observable and input domain bound);
``scientific_support`` stays ``UNKNOWN``. A later receipt that contradicts the
qualifying law retracts the tool's goal binding and every hypothesis built on it.
"""
import ast
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import rds_compass_model as model
import rds_compass_store as cstore
from rds_project import canonical, digest, require
from rds_tools import extract_function

TOOL_SCHEMA = 'rds-compass-tool-v1'
TOOL_DIR = '.rds/tools/compass'
MAX_POOL = 48
STEP_LIMIT = 200000
RUNNER_SECONDS = 60
RUNNER = r'''
import json, sys
from fractions import Fraction
request = json.loads(sys.stdin.read())
out = {}
class Budget(Exception):
    pass
for item in request["tools"]:
    namespace = {"__builtins__": __builtins__}
    try:
        exec(compile(item["source"], "<rds-tool>", "exec"), namespace)
        function = namespace[item["entry"]]
    except Exception as exc:
        out[item["sha256"]] = {"error": type(exc).__name__}
        continue
    values = []
    for value in request["inputs"]:
        steps = [0]
        def tracer(frame, event, arg):
            steps[0] += 1
            if steps[0] > request["steps"]:
                raise Budget()
            return tracer
        sys.settrace(tracer)
        try:
            result = function(value)
            sys.settrace(None)
            if isinstance(result, bool) or not isinstance(result, (int, Fraction)):
                values.append(None)
            else:
                values.append(str(Fraction(result)))
        except BaseException:
            sys.settrace(None)
            values.append(None)
    out[item["sha256"]] = {"values": values}
sys.stdout.write(json.dumps(out, sort_keys=True))
'''


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


# --- Candidate extraction -----------------------------------------------------

def _fold(name, base, step):
    return (f'def {name}(n):\n    n = abs(int(n))\n    acc = 0\n    while n:\n'
            f'        acc = {step}\n        n = n // {base}\n    return acc\n')


def _factored(name, start, step, small):
    """Fold over the prime factorization of abs(n): ``step`` sees each prime p with multiplicity k."""
    return (f'def {name}(n):\n    n = abs(int(n))\n    if n < 2:\n        return {small}\n    acc, p = {start}, 2\n'
            f'    while n > 1:\n        if p * p > n:\n            p = n\n        k = 0\n'
            f'        while n % p == 0:\n            n = n // p\n            k = k + 1\n'
            f'        if k:\n            acc = {step}\n        p = p + 1\n    return acc\n')


def families():
    """The base catalogue: digit folds, valuations, divisor and prime-factor functions, primes, sequences."""
    out = []
    for base in (2, 3, 10):
        out.append(_fold(f'digitsum{base}', base, f'acc + n % {base}'))
        out.append(_fold(f'digitcount{base}', base, 'acc + 1'))
        out.append(_fold(f'nonzero{base}', base, f'acc + (1 if n % {base} else 0)'))
        out.append(_fold(f'digitmax{base}', base, f'max(acc, n % {base})'))
    for base in (2, 3, 5):
        out.append(f'def val{base}(n):\n    n = abs(int(n))\n    if n == 0:\n        return 0\n    acc = 0\n'
                   f'    while n % {base} == 0:\n        acc = acc + 1\n        n = n // {base}\n    return acc\n')
    out.append('def divisors(n):\n    n = abs(int(n))\n    return sum(1 for d in range(1, n + 1) if n % d == 0)\n')
    out.append('def sigma(n):\n    n = abs(int(n))\n    return sum(d for d in range(1, n + 1) if n % d == 0)\n')
    out.append('def isprime(n):\n    n = int(n)\n    if n < 2:\n        return 0\n'
               '    return 1 if all(n % d for d in range(2, int(n ** 0.5) + 1)) else 0\n')
    out.append('def fib(n):\n    a, b = 0, 1\n    for _ in range(abs(int(n))):\n        a, b = b, a + b\n    return a\n')
    # Appended after the original families, so a gap those already close is closed by the same tool.
    for name, start, step, small in (('totient', 1, 'acc * (p - 1) * p ** (k - 1)', 'n'), ('omega', 0, 'acc + 1', 0),
                                     ('bigomega', 0, 'acc + k', 0), ('mobius', 1, '0 if k > 1 else -acc', 'n'),
                                     ('lpf', 0, 'acc or p', 'n'), ('gpf', 0, 'p', 'n'), ('rad', 1, 'acc * p', 'n')):
        out.append(_factored(name, start, step, small))
    out.append(_fold('digitrev10', 10, 'acc * 10 + n % 10'))
    out.append('import math\n\n\ndef issquare(n):\n    n = int(n)\n    return 1 if n >= 0 and math.isqrt(n) ** 2 == n else 0\n')
    out.append('import math\n\n\ndef primepi(n):\n    n = int(n)\n'
               '    return sum(1 for k in range(2, n + 1) if all(k % d for d in range(2, math.isqrt(k) + 1)))\n')
    out.append('def collatz(n):\n    n = abs(int(n))\n    acc = 0\n    while n > 1:\n'
               '        n = n // 2 if n % 2 == 0 else 3 * n + 1\n        acc = acc + 1\n    return acc\n')
    return out


def catalogue():
    """The base tools as Compass offers them at a capability gap: unqualified until the oracle triple."""
    return [{'name': name, 'sha256': sha, 'domain': 'integer', 'status': 'OFFER_NOT_QUALIFIED'}
            for name, _, _, sha in map(extract, families())]


def extract(source):
    """Validate a tool through the existing RSI allowlist; returns (name, entry, code, sha)."""
    tree = ast.parse(source)
    defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    require(len(defs) == 1 and len(defs[0].args.args) == 1, 'Compass tools are one-argument functions')
    entry = defs[0].name
    require(entry.isidentifier() and entry.isalnum() and entry.islower() and len(entry) <= 24,
            'Compass tool names are short lowercase alphanumerics')
    code, _ = extract_function(source.encode('utf-8'), entry)
    return entry, entry, code.decode('utf-8'), _sha(code)


def propose(store, source, origin):
    """Record a tool offer for the next capability gap; it qualifies only through the oracle triple."""
    require(origin in ('USER_PROPOSED', 'AGENT_PROPOSED'), 'Unknown tool offer origin')
    first = cstore.events(store, load=False)[:1]
    require(first and first[0]['kind'] == cstore.PREFIX + 'DECLARED', 'Run compass init before offering tools')
    try:
        name, entry, code, sha = extract(source)
    except SyntaxError as exc:
        raise ValueError('Tool source does not parse: ' + str(exc)[:200]) from None
    event = cstore.append(store, 'TOOL_PROPOSED', f'{sha[:16]}-{origin.lower()}', {
        'name': name, 'entry': entry, 'source': code, 'sha256': sha, 'origin': origin,
        'status': 'OFFER_NOT_QUALIFIED', 'self_certified': False, 'scientific_support': 'UNKNOWN'})
    return {'status': 'OFFER_NOT_QUALIFIED', 'name': name, 'sha256': sha, 'origin': origin,
            'recorded': not event['reused']}


def mutants(code, entry, limit):
    """Deterministic first-order mutants: integer constant +-1, operator swaps."""
    swaps = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add, ast.FloorDiv: ast.Mod, ast.Mod: ast.FloorDiv}
    tree = ast.parse(code)
    sites = []
    for index, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            sites += [(index, 'const', node.value + 1), (index, 'const', node.value - 1)]
        elif isinstance(node, ast.BinOp) and type(node.op) in swaps:
            sites.append((index, 'op', swaps[type(node.op)]))
    out, seen = [], {_sha(code.encode('utf-8'))}
    for index, kind, value in sites:
        if len(out) >= limit:
            break
        copy = ast.parse(code)
        node = list(ast.walk(copy))[index]
        if kind == 'const':
            node.value = value
        else:
            node.op = value()
        source = ast.unparse(copy) + '\n'
        sha = _sha(source.encode('utf-8'))
        if sha not in seen:
            seen.add(sha)
            out.append({'source': source, 'entry': entry, 'sha256': sha})
    return out


class ToolRunnerError(ValueError):
    """The bounded tool runner timed out or exited non-zero; ``detail`` is its settlement record."""

    def __init__(self, reason, detail):
        super().__init__(f"{reason}: {detail['stderr_tail']}")
        self.reason, self.detail = reason, detail


def _runner_detail(tools, timeout, started, stdout, stderr, code):
    stdout, stderr = stdout or b'', stderr or b''
    return {'runner_sha256': _sha(RUNNER.encode('utf-8')), 'tools': [t['sha256'] for t in tools],
            'timeout_seconds': timeout, 'wall_seconds': round(time.monotonic() - started, 3), 'exit_code': code,
            'stdout_sha256': _sha(stdout), 'stdout_bytes': len(stdout), 'stderr_sha256': _sha(stderr),
            'stderr_tail': stderr[-300:].decode('utf-8', 'replace'), 'assurance': 'SUBPROCESS_ISOLATED_NOT_OS_SANDBOX'}


def evaluate_tools(tools, inputs, *, timeout=None):
    """Run tool sources over integer inputs in an isolated interpreter (not an OS sandbox)."""
    timeout = RUNNER_SECONDS if timeout is None else timeout
    request = {'tools': [{'source': t['source'], 'entry': t['entry'], 'sha256': t['sha256']} for t in tools],
               'inputs': inputs, 'steps': STEP_LIMIT}
    started = time.monotonic()
    try:
        done = subprocess.run([sys.executable, '-I', '-B', '-c', RUNNER], input=json.dumps(request).encode('utf-8'),
                              capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ToolRunnerError('TOOL_RUNNER_TIMEOUT',
                              _runner_detail(tools, timeout, started, exc.stdout, exc.stderr, None)) from None
    if done.returncode != 0:
        raise ToolRunnerError('TOOL_RUNNER_FAILED',
                              _runner_detail(tools, timeout, started, done.stdout, done.stderr, done.returncode))
    values = json.loads(done.stdout.decode('utf-8'))
    evidence = {'runner_sha256': _sha(RUNNER.encode('utf-8')), 'output_sha256': _sha(done.stdout),
                'assurance': 'SUBPROCESS_ISOLATED_NOT_OS_SANDBOX'}
    return values, evidence


# --- Library (cross-session reuse within scope) ------------------------------
#
# A library record is an offer, never a certificate: every reused tool is
# re-qualified against the reusing campaign's own receipts. Provenance only
# orders the offer (verified origins first) and withholds tools whose origin
# ledger or retraction marker says their qualifying law was later contradicted.

def library_path(store, plan):
    value = plan.get('library')
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else store.root / path


def _create(target, record):
    try:
        with target.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(record, sort_keys=True, indent=1))
    except FileExistsError:
        pass  # Content-addressed and create-only: an existing record is never rewritten.


def origin_status(provenance, sha):
    """Re-read the origin ledger: did it qualify this tool, and has it retracted it since?"""
    try:
        origin = cstore.store_for(provenance['ledger_root'])
        found = retracted = False
        for event in cstore.events(origin):
            value = event['value']
            if (event['kind'] == cstore.PREFIX + 'TOOL_QUALIFIED' and event['sha256'] == provenance['record_sha256']
                    and value.get('sha256') == sha):
                found = True
            elif event['kind'] == cstore.PREFIX + 'TOOL_RETRACTED' and value.get('tool') == sha:
                retracted = True
        return 'ORIGIN_RETRACTED' if retracted else 'ORIGIN_VERIFIED' if found else 'ORIGIN_UNVERIFIED'
    except (ValueError, KeyError, TypeError, OSError):
        return 'ORIGIN_UNVERIFIED'  # Moved, damaged or unreadable origin: offered, never trusted.


def library_tools(path, scope=None):
    out = []
    if not path or not Path(path).is_dir():
        return out
    folder = Path(path)
    for item in sorted(folder.glob('*.json'))[:256]:
        if item.name.endswith('.retracted.json'):
            continue
        try:
            record = json.loads(item.read_text(encoding='utf-8'))
            require(isinstance(record, dict) and record.get('schema') == TOOL_SCHEMA, 'Not a Compass tool record')
            require(isinstance(record.get('scope'), dict), 'Library tool scope must be an object')
            name, entry, code, sha = extract(record['source'])
            require(sha == record['sha256'] and code == record['source'], 'Library tool hash mismatch')
        except (ValueError, KeyError, TypeError, OSError, SyntaxError):
            continue  # A damaged record is not reused; nothing is repaired.
        if scope is not None and record['scope'].get('domain') != scope['domain']:
            continue  # Outside the verified scope.
        status = origin_status(record.get('provenance') or {}, sha)
        if status == 'ORIGIN_RETRACTED' or (folder / (item.stem + '.retracted.json')).exists():
            continue
        out.append({'name': name, 'entry': entry, 'source': code, 'sha256': sha, 'origin': 'LIBRARY',
                    'library_record': item.name, 'library_provenance': status, 'scope': record['scope']})
    return sorted(out, key=lambda t: t['library_provenance'] != 'ORIGIN_VERIFIED')


def export(path, tool, scope, qualification, provenance):
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    record = {'schema': TOOL_SCHEMA, 'name': tool['name'], 'entry': tool['entry'], 'source': tool['source'],
              'sha256': tool['sha256'], 'scope': {'domain': scope['domain']}, 'qualification': qualification,
              'provenance': provenance, 'scientific_support': 'UNKNOWN'}
    target = folder / f"{tool['name']}-{tool['sha256'][:12]}-{provenance['record_sha256'][:12]}.json"
    _create(target, record)
    return target.name


def mark_retracted(path, export_name, retraction):
    """Create-only marker beside the exported record so later sessions stop offering it."""
    folder = Path(path)
    if export_name and (folder / export_name).is_file():
        _create(folder / (Path(export_name).stem + '.retracted.json'), retraction)


# --- Qualification ------------------------------------------------------------

def _scope(state):
    plan = state['plan']
    return {'goal_sha256': digest(plan['goal']), 'observable': plan['goal']['observable'],
            'inputs': plan['inputs'], 'contract_sha256': plan['contract_sha256'], 'domain': 'integer'}


def _table(state, values, var):
    return {c['id']: v for c, v in zip(state['plan']['candidates'], values)}


def _with_feature(rows, state, name, table):
    out = []
    for row in rows:
        value = table.get(row['id'])
        if value is None:
            return None
        out.append({**row, 'inputs': {**row['inputs'], name: model.exact(value)}})
    return out


def _qualify(state, tool, values, rows, var, run_mutants):
    """Return (record, rejection) for one tool over the current receipted evidence."""
    import rds_compass as compass
    plan = state['plan']
    tolerances = compass._tolerances(plan)
    observable = plan['goal']['observable']
    # A feature name denotes one tool's values for good: a tool sharing another tool's name (or an
    # input's) gets its hash in the name, so env() never overwrites one table with another.
    taken = set(plan['inputs']) | {f for t in state['tools'].values() if t['sha256'] != tool['sha256']
                                   for f in t['features']}
    feature = f"{tool['name']}_{var}"
    if feature in taken:
        feature = f"{tool['name']}{tool['sha256'][:12]}_{var}"
    if feature in taken or len(feature) > 64:
        return None, {'reason': 'FEATURE_NAME_UNAVAILABLE', 'feature': feature}
    table = _table(state, values, var)
    full = _with_feature(rows, state, feature, table)
    if full is None:
        return None, {'reason': 'TOOL_UNDEFINED_ON_MEASURED_INPUT'}
    holdout_size = max(1, len(full) // 4)
    fit_rows, holdout = full[:-holdout_size], full[-holdout_size:]
    names = compass.variables(state) + [feature]
    grammar, _ = compass._grammar(state)
    grammar = {**grammar, 'variables': sorted(set(grammar['variables']) | {feature})}
    search = model.synthesize(fit_rows, names, observable, tolerances, grammar=grammar,
                              max_nodes=min(5, plan['synthesis']['max_nodes']), max_candidates=6000, keep=8)
    fits = [f for f in search['fits'] if feature in model.variables_of(f['expression'])]
    if not fits:
        return None, {'reason': 'NO_FIT_WITH_TOOL', 'attempted': search['attempted']}
    law = model.compile_hypothesis({'id': 'q-' + tool['name'], 'predict': {observable: fits[0]['expression']}},
                                   names, plan['observables'])
    positive = model.replay(law, holdout, {observable: tolerances[observable]})
    if positive['status'] != 'CONSISTENT':
        return None, {'reason': 'HOLDOUT_REFUTED', 'law': fits[0]['text'],
                      'witness': positive['counterexamples'][:1]}
    mutant_list, mutant_values = run_mutants()
    killed, equivalent, invalid = None, 0, 0
    for mutant in mutant_list:
        result = mutant_values.get(mutant['sha256'], {})
        mtable = _table(state, result.get('values') or [None] * len(plan['candidates']), var)
        mrows = _with_feature(rows, state, feature, mtable)
        if mrows is None:
            invalid += 1  # A crashing or non-terminating mutant is not an oracle verdict.
            continue
        verdict = model.replay(law, mrows, {observable: tolerances[observable]})
        if verdict['status'] == 'REFUTED':
            witness = verdict['counterexamples'][0]
            witness['receipt_sha256'] = next(r['receipt_sha256'] for r in rows if r['id'] == witness['id'])
            killed = {'mutant_sha256': mutant['sha256'], 'kind': 'MUTANT_CONTRADICTED_BY_RECEIPT',
                      'witness': witness}
            break
        equivalent += 1
    if killed is None:
        return None, {'reason': 'ORACLE_NON_DISCRIMINATING', 'mutants': len(mutant_list), 'invalid_mutants': invalid,
                      'law': fits[0]['text']}
    oracle = {'kind': 'RECEIPTED_HOLDOUT', 'expected_values_from': 'ORIGINAL_RUN_RECEIPTS',
              'known_positive': {'law': fits[0]['text'], 'law_ast': fits[0]['expression'],
                                 'fitted_on': [r['id'] for r in fit_rows],
                                 'holdout': [{'id': r['id'], 'receipt_sha256': r['receipt_sha256']} for r in holdout]},
              'discriminating_negative': killed, 'equivalent_mutants_before_kill': equivalent,
              'invalid_mutants_skipped': invalid, 'counterexample_witness': killed['witness']}
    record = {'name': tool['name'], 'entry': tool['entry'], 'sha256': tool['sha256'], 'origin': tool['origin'],
              'features': {feature: table}, 'oracle': oracle, 'scope': _scope(state),
              'status_label': 'QUALIFIED_FOR_SCOPE', 'assurance': 'RECEIPTED_HOLDOUT_ORACLE_TRIPLE',
              'self_certified': False, 'scientific_support': 'UNKNOWN'}
    return record, None


def escalate(store, state, gap):
    """Close one capability gap in-campaign: extract -> validate -> qualify -> register -> use."""
    import rds_compass as compass
    plan = state['plan']
    rsi = plan['rsi']
    integer = [n for n, spec in plan['inputs'].items() if spec.get('integer')]
    qualified_now = sum(1 for t in state['tools'].values() if t['status'] == 'QUALIFIED')
    report = {'gap': gap, 'qualified': [], 'tried': 0, 'rejected': [], 'library_offered': 0}
    if not integer:
        return {**report, 'status': 'NO_TOOL_FAMILY_FOR_DOMAIN'}
    if qualified_now >= rsi['max_tools']:
        return {**report, 'status': 'TOOL_LIMIT_REACHED'}
    rows = [r for r in compass.measured(state) if plan['goal']['observable'] in r['outputs']]
    if len(rows) < 4:
        return {**report, 'status': 'INSUFFICIENT_EVIDENCE'}
    # Domain gaps never close with more data; other rejections are re-tried once evidence grows.
    structural = {r['sha256'] for r in state['tool_rejections']
                  if r['reason'] == 'UNDEFINED_ON_DECLARED_DOMAIN' or r.get('measurements') == len(rows)}
    known = {sha for sha, t in state['tools'].items() if t['status'] == 'QUALIFIED'}
    pool, seen = [], set()
    folder = library_path(store, plan)
    library = library_tools(folder, _scope(state))
    report['library_offered'] = len(library)
    offers = [dict(zip(('name', 'entry', 'source', 'sha256'), extract(t['source'])), origin=t['origin'])
              for t in sorted(state['proposed_tools'].values(), key=lambda t: t['seq'])]
    for tool in library + offers + [dict(zip(('name', 'entry', 'source', 'sha256'), extract(s)), origin='SYNTHESIZED')
                                    for s in families()]:
        if tool['sha256'] in seen or tool['sha256'] in structural or tool['sha256'] in known:
            continue
        seen.add(tool['sha256'])
        pool.append(tool)
    pool = pool[:MAX_POOL]
    if not pool:
        return {**report, 'status': 'NO_TOOL_CANDIDATES'}
    inputs = [int(model.exact(c['inputs'][integer[0]])) for c in plan['candidates']]
    var = integer[0]

    def settle(tool, failure, phase):
        # A runner failure is recorded per tool at this evidence count, so a resumed drive does not
        # pay for the same qualification again; it is re-tried only once evidence grows.
        cstore.append(store, 'TOOL_REJECTED', f"{tool['sha256'][:16]}-{len(rows)}", {
            'name': tool['name'], 'sha256': tool['sha256'], 'origin': tool['origin'], 'measurements': len(rows),
            'reason': failure.reason, 'phase': phase, 'runner': failure.detail})
        report['rejected'].append({'name': tool['name'], 'reason': failure.reason, 'phase': phase})

    try:
        values, evidence = evaluate_tools(pool, inputs)
    except ToolRunnerError as failure:
        for tool in pool:
            settle(tool, failure, 'POOL')
        return {**report, 'tried': len(pool), 'status': failure.reason, 'runner': failure.detail}
    for tool in pool:
        report['tried'] += 1
        result = values.get(tool['sha256'], {})
        if 'values' not in result:
            continue
        gap = next((c['id'] for c, v in zip(plan['candidates'], result['values']) if v is None), None)
        if gap is not None:
            # Boundary witness: a tool that is undefined on a declared input cannot be a law primitive there.
            cstore.append(store, 'TOOL_REJECTED', f"{tool['sha256'][:16]}-{len(rows)}", {
                'name': tool['name'], 'sha256': tool['sha256'], 'origin': tool['origin'], 'measurements': len(rows),
                'reason': 'UNDEFINED_ON_DECLARED_DOMAIN', 'witness': {'candidate': gap}})
            report['rejected'].append({'name': tool['name'], 'reason': 'UNDEFINED_ON_DECLARED_DOMAIN'})
            continue
        def run_mutants(tool=tool):
            listed = mutants(tool['source'], tool['entry'], rsi['max_mutants'])
            return listed, (evaluate_tools(listed, inputs)[0] if listed else {})
        try:
            record, rejection = _qualify(state, tool, result['values'], rows, var, run_mutants)
        except ToolRunnerError as failure:
            settle(tool, failure, 'MUTANTS')
            continue
        if rejection is not None:
            if rejection['reason'] in ('HOLDOUT_REFUTED', 'ORACLE_NON_DISCRIMINATING'):
                value = {'name': tool['name'], 'sha256': tool['sha256'], 'origin': tool['origin'],
                         'measurements': len(rows), **rejection}
                cstore.append(store, 'TOOL_REJECTED', f"{tool['sha256'][:16]}-{len(rows)}", value)
                report['rejected'].append({'name': tool['name'], 'reason': rejection['reason']})
            continue
        path = store.root / TOOL_DIR / f"{tool['name']}-{tool['sha256'][:12]}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(tool['source'].encode('utf-8'))
        require(_sha(path.read_bytes()) == tool['sha256'], 'Registered tool bytes differ from qualified source')
        record.update(path=path.relative_to(store.root).as_posix(), source=tool['source'], runner=evidence,
                      boundary={'declared_candidates': len(plan['candidates']), 'defined_on_all': True})
        for key in ('library_record', 'library_provenance'):
            if key in tool:
                record[key] = tool[key]
        count = sum(1 for t in state['tools'].values() if t['sha256'] == tool['sha256'])
        event = cstore.append(store, 'TOOL_QUALIFIED', f"{tool['sha256'][:16]}-q{count + 1}-{len(rows)}", record)
        if folder is not None:
            # Exported after the ledger write so the record names the exact qualifying event.
            export(folder, tool, record['scope'],
                   {'oracle_kind': 'RECEIPTED_HOLDOUT', 'law': record['oracle']['known_positive']['law']},
                   {'ledger_root': str(store.root.resolve()), 'record_sha256': event['sha256'],
                    'seq': event['seq'], 'campaign_head': state['head']})
        report['qualified'].append(tool['name'])
        break
    return {**report, 'status': 'QUALIFIED' if report['qualified'] else 'NO_TOOL_QUALIFIED',
            'runner': evidence}


def retract_unbound(store, state):
    """Retract a tool whose qualifying law a newer receipt contradicts (goal binding defeated)."""
    import rds_compass as compass
    plan = state['plan']
    if plan['goal']['kind'] != 'IDENTIFY_LAW':
        return []
    tolerances = compass._tolerances(plan)
    observable = plan['goal']['observable']
    rows = compass.measured(state)
    retracted = []
    for sha, tool in state['tools'].items():
        if tool['status'] != 'QUALIFIED':
            continue
        law = model.compile_hypothesis({'id': 'q-' + tool['name'],
                                        'predict': {observable: tool['oracle']['known_positive']['law_ast']}},
                                       compass.variables(state), plan['observables'])
        verdict = model.replay(law, [r for r in rows if observable in r['outputs']],
                               {observable: tolerances[observable]})
        if verdict['status'] == 'REFUTED':
            witness = verdict['counterexamples'][0]
            witness['receipt_sha256'] = next(r['receipt_sha256'] for r in rows if r['id'] == witness['id'])
            value = {'tool': sha, 'name': tool['name'], 'reason': 'QUALIFYING_LAW_CONTRADICTED_BY_RECEIPT',
                     'witness': witness, 'dependents': tool['uses'], 'scientific_support': 'UNKNOWN'}
            event = cstore.append(store, 'TOOL_RETRACTED', f"{sha[:16]}-r{tool['seq']}", value)
            folder = library_path(store, plan)
            if folder is not None:
                name = f"{tool['name']}-{sha[:12]}-{tool['record_sha256'][:12]}.json"
                mark_retracted(folder, name, {**value, 'ledger_root': str(store.root.resolve()),
                                              'record_sha256': event['sha256']})
            retracted.append(tool['name'])
    return retracted
