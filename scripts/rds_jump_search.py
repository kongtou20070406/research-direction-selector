"""Finite integer expression discovery; outputs are hypotheses, not authority.

No target expression is built in. Only canonical syntax is deduplicated: equal
training predictions do not imply equivalent explanations. All raw construction
attempts, including rewrites and duplicates, consume the declared search cap.
"""
import json
import re


INTEGER_BOUND = 10**12
MAX_NODES = 9
MAX_CANDIDATES = 10000
MAX_ROWS = 128
MAX_VARIABLES = 8
MAX_CONSTANTS = 16
MAX_SEEDS = 64
OPERATORS = frozenset(('add', 'sub', 'mul', 'mod'))
_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}\Z')


class DomainError(ValueError):
    """A declared integer expression is undefined or exceeds its finite domain."""


def _integer(value):
    if type(value) is not int or abs(value) > INTEGER_BOUND:
        raise DomainError('Expected exact integer within declared bound')
    return value


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError('Invalid variable name')
    return value


def _key(ast):
    return json.dumps(ast, sort_keys=True, separators=(',', ':'))


def _ast(ast, *, limit=MAX_NODES, grammar=None):
    count, active = 0, set()

    def visit(node):
        nonlocal count
        count += 1
        if count > limit or not isinstance(node, dict) or id(node) in active:
            raise ValueError('Invalid, cyclic or oversized expression AST')
        active.add(id(node))
        try:
            if set(node) == {'var'}:
                name = _name(node['var'])
                if grammar and name not in grammar[0]:
                    raise ValueError('Seed variable outside declared grammar')
                return {'var': name}
            if set(node) == {'const'}:
                value = _integer(node['const'])
                if grammar and value not in grammar[1]:
                    raise ValueError('Seed constant outside declared grammar')
                return {'const': value}
            if (set(node) != {'op', 'args'} or not isinstance(node['op'], str)
                    or node['op'] not in OPERATORS):
                raise ValueError('Invalid AST operator or fields')
            op, args = node['op'], node['args']
            if grammar and op not in grammar[2]:
                raise ValueError('Seed operator outside declared grammar')
            if not isinstance(args, list) or len(args) != 2:
                raise ValueError('Binary operator requires two AST arguments')
            left, right = visit(args[0]), visit(args[1])
            # These identities preserve domain errors in the remaining subtree.
            # Do not use x*0=0 or x-x=0: they could erase undefined subexpressions.
            if op == 'add':
                if left == {'const': 0}:
                    return right
                if right == {'const': 0}:
                    return left
            if op == 'sub' and right == {'const': 0}:
                return left
            if op == 'mul':
                if left == {'const': 1}:
                    return right
                if right == {'const': 1}:
                    return left
            if op in {'add', 'mul'} and _key(left) > _key(right):
                left, right = right, left
            return {'op': op, 'args': [left, right]}
        finally:
            active.remove(id(node))

    return visit(ast)


def _environment(env):
    if not isinstance(env, dict) or len(env) > MAX_VARIABLES:
        raise ValueError('Expected bounded input environment')
    return {_name(k): _integer(v) for k, v in env.items()}


def _evaluate(ast, env):
    if 'const' in ast:
        return ast['const']
    if 'var' in ast:
        if ast['var'] not in env:
            raise DomainError('Missing variable: ' + ast['var'])
        return env[ast['var']]
    left, right = (_evaluate(v, env) for v in ast['args'])
    op = ast['op']
    if op == 'add':
        value = left + right
    elif op == 'sub':
        value = left - right
    elif op == 'mul':
        value = left * right
    else:
        if right == 0:
            raise DomainError('Modulo by zero')
        value = left % right  # Python's exact integer, floor-remainder semantics.
    return _integer(value)


def evaluate(ast, env):
    """Evaluate strict AST data, with bounded exact integers at every operation."""
    return _evaluate(_ast(ast), _environment(env))


def _observations(observations):
    if not isinstance(observations, (list, tuple)) or not 1 <= len(observations) <= MAX_ROWS:
        raise ValueError('Expected 1..128 observations')
    rows = []
    for row in observations:
        if not isinstance(row, dict) or set(row) != {'inputs', 'value'}:
            raise ValueError('Observation requires inputs and value only')
        rows.append({'inputs': _environment(row['inputs']), 'value': _integer(row['value'])})
    return rows


def diagnose(observations, baseline):
    """Retain measured residuals and original counterexamples to the baseline."""
    rows = _observations(observations)
    ast = _ast(baseline) if baseline is not None else None
    residuals, counterexamples = [], []
    for row in rows:
        result = {**row, 'predicted': None, 'residual': None, 'status': 'UNKNOWN'}
        if ast is not None:
            try:
                prediction = _evaluate(ast, row['inputs'])
                result.update(predicted=prediction, residual=row['value'] - prediction, status='OBSERVED')
                if result['residual'] != 0:
                    counterexamples.append(dict(result))
            except DomainError as exc:
                result.update(status='DOMAIN_ERROR', reason=str(exc))
                counterexamples.append(dict(result))
        residuals.append(result)
    return {'status': 'DIAGNOSED' if ast is not None else 'NO_BASELINE', 'baseline': ast,
            'residuals': residuals, 'counterexamples': counterexamples,
            'max_abs_residual': max((abs(r['residual']) for r in residuals if r['residual'] is not None), default=None)}


def _size(ast):
    return 1 if 'args' not in ast else 1 + sum(_size(a) for a in ast['args'])


def synthesize(observations, variables, constants, operators, *, seeds=(),
               max_nodes=5, max_candidates=2000, probes=(), baseline=None):
    """Search declared finite grammar against all original observations.

    Seeds are retained branch ASTs, not privileged answers. A fit is preferred
    when a declared probe separates it from the rival. Exhaustion is only about
    this grammar, node bound and construction order; never scientific impossibility.
    """
    if type(max_nodes) is not int or not 1 <= max_nodes <= MAX_NODES:
        raise ValueError('max_nodes must be 1..9')
    if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise ValueError('max_candidates must be 1..10000')
    if not isinstance(variables, (list, tuple)) or not 1 <= len(variables) <= MAX_VARIABLES:
        raise ValueError('Expected 1..8 variables')
    names = [_name(v) for v in variables]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate variables')
    if not isinstance(constants, (list, tuple)) or len(constants) > MAX_CONSTANTS:
        raise ValueError('Expected at most 16 constants')
    numbers = [_integer(v) for v in constants]
    if len(set(numbers)) != len(numbers):
        raise ValueError('Duplicate constants')
    if not isinstance(operators, (list, tuple)) or len(operators) > len(OPERATORS):
        raise ValueError('Invalid operator inventory')
    if any(not isinstance(op, str) or op not in OPERATORS for op in operators) or len(set(operators)) != len(operators):
        raise ValueError('Unsupported or duplicate operator')
    if not isinstance(seeds, (list, tuple)) or len(seeds) > MAX_SEEDS:
        raise ValueError('Expected at most 64 retained seeds')
    if not isinstance(probes, (list, tuple)) or len(probes) > MAX_ROWS:
        raise ValueError('Expected at most 128 declared probes')
    rows, envs = _observations(observations), [_environment(p) for p in probes]
    if any(set(r['inputs']) != set(names) for r in rows) or any(set(p) != set(names) for p in envs):
        raise ValueError('Observation/probe variables differ from declared grammar')
    rival = _ast(baseline) if baseline is not None else None
    grammar = (set(names), set(numbers), set(operators))
    retained = [_ast(s, limit=max_nodes, grammar=grammar) for s in seeds]
    pools = {n: [] for n in range(1, max_nodes + 1)}
    seen, fits = set(), []
    attempted = generated = filtered = domain_filtered = 0
    selected = None
    stopped = False

    def separation(ast):
        if rival is None:
            return None, None
        for env in envs:
            try:
                candidate_value, rival_value = _evaluate(ast, env), _evaluate(rival, env)
            except DomainError:
                continue  # Undefined rivals are not fabricated numeric predictions.
            if candidate_value != rival_value:
                return dict(env), {'candidate': candidate_value, 'rival': rival_value}
        return None, None

    def consider(raw, *, origin='grammar', seed_index=None):
        nonlocal attempted, generated, filtered, domain_filtered, selected, stopped
        if attempted >= max_candidates:
            stopped = True
            return False
        attempted += 1
        ast = _ast(raw, limit=max_nodes)
        key = _key(ast)
        if key in seen:
            return True
        seen.add(key)
        generated += 1
        pools[_size(ast)].append(ast)
        try:
            match = all(_evaluate(ast, r['inputs']) == r['value'] for r in rows)
        except DomainError:
            match = False
            domain_filtered += 1
        if not match:
            filtered += 1
            return True
        probe, predictions = separation(ast)
        fit = {'candidate': ast, 'probe': probe, 'probe_predictions': predictions,
               'candidate_origin': origin, 'seed_index': seed_index}
        fits.append(fit)
        if selected is None or probe is not None:
            selected = fit
        return rival is not None and probe is None

    # Branch history gets a bounded head start but undergoes exactly the same
    # grammar, evidence, domain and probe checks as freshly generated expressions.
    finished = False
    for index, ast in enumerate(retained):
        if not consider(ast, origin='retained_seed', seed_index=index):
            finished = True
            break
    if not finished:
        for ast in [{'var': v} for v in names] + [{'const': v} for v in numbers]:
            if not consider(ast):
                finished = True
                break
    if not finished:
        for size in range(3, max_nodes + 1, 2):
            for op in operators:
                if finished:
                    break
                for left_size in range(1, size - 1):
                    if finished:
                        break
                    right_size = size - 1 - left_size
                    # Snapshot lists because identity rewrites can revisit an
                    # existing smaller-size pool; only unique ASTs enter it.
                    for left in tuple(pools[left_size]):
                        if finished:
                            break
                        for right in tuple(pools[right_size]):
                            if not consider({'op': op, 'args': [left, right]}):
                                finished = True
                                break
            if finished:
                break
    candidate = selected['candidate'] if selected else None
    probe = selected['probe'] if selected else None
    equivalence = ('NO_RIVAL' if rival is None else
                   'EXACT_CANONICAL_EQUIVALENCE' if candidate == rival else
                   'SEPARATED_ON_DECLARED_PROBE' if probe is not None else
                   'OBSERVATIONAL_COINCIDENCE_ONLY' if candidate is not None else 'UNASSESSED')
    return {'status': 'FIT_FOUND' if selected else 'SEARCH_TRUNCATED' if stopped else 'NO_FIT',
            'candidate': candidate, 'rival': rival, 'probe': probe,
            'probe_predictions': selected['probe_predictions'] if selected else None,
            'candidate_origin': selected['candidate_origin'] if selected else None,
            'seed_index': selected['seed_index'] if selected else None,
            'equivalence': equivalence, 'generated': generated, 'attempted': attempted,
            'filtered': filtered, 'domain_filtered': domain_filtered,
            'fit_count': len(fits), 'truncated': stopped,
            'exhaustion': 'CANDIDATE_LIMIT' if stopped else 'FIT_SELECTED' if finished else 'FINITE_GRAMMAR_EXHAUSTED',
            'limits': {'max_nodes': max_nodes, 'max_candidates': max_candidates, 'integer_bound': INTEGER_BOUND},
            'diagnostics': diagnose(rows, rival), 'scientific_support': 'UNKNOWN'}
