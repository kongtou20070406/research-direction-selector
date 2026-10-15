"""Executable rival-hypothesis models for the Compass Advisor (pure values).

A hypothesis is a small rulebook: one bounded expression per declared
observable plus an optional applicability predicate. Compilation yields an
exact Fraction predictor; replay against original measurements decides whether
the rulebook still reproduces every observed transition within the declared
tolerance. Nothing here reads a ledger, starts a process or certifies a claim:
CONSISTENT means "not contradicted by these observations", never SUPPORT.
"""
from fractions import Fraction
import ast as _pyast
import math

from rds_core import canonical, object_digest

MAX_NODES = 15
MAX_BITS = 512
MAX_POWER = 8
MAX_POINTS = 256
MAX_SYNTH_CANDIDATES = 200000
UNARY = frozenset(('neg', 'abs', 'floor', 'ceil', 'sqrt'))
BINARY = frozenset(('add', 'sub', 'mul', 'div', 'mod', 'pow', 'min', 'max', 'lt', 'le', 'eq'))
TERNARY = frozenset(('ite',))
OPERATORS = UNARY | BINARY | TERNARY
COMMUTATIVE = frozenset(('add', 'mul', 'min', 'max', 'eq'))
_INFIX = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/', 'mod': '%', 'pow': '**',
          'lt': '<', 'le': '<=', 'eq': '=='}


class DomainError(ValueError):
    """An expression is undefined or leaves its bounded exact domain here."""


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return object_digest(value)


def exact(value):
    """Parse an exact rational; floats use their shortest decimal spelling."""
    if isinstance(value, bool):
        raise ValueError('Booleans are not numeric measurements')
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, int):
        result = Fraction(value)
    elif isinstance(value, float):
        require(math.isfinite(value), 'Non-finite measurement')
        result = Fraction(repr(value))
    elif isinstance(value, str) and 0 < len(value) <= 256:
        try:
            result = Fraction(value.strip())
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError('Invalid exact number: ' + value) from exc
    else:
        raise ValueError('Expected a finite number or exact rational string')
    return _bounded(result)


def _bounded(value):
    if value.numerator.bit_length() > MAX_BITS or value.denominator.bit_length() > MAX_BITS:
        raise DomainError('Exact value exceeds bounded precision')
    return value


def text(value):
    """Stable exact spelling used in packets and ledger bodies."""
    value = exact(value)
    return str(value.numerator) if value.denominator == 1 else f'{value.numerator}/{value.denominator}'


def _name(value):
    require(isinstance(value, str) and value.isidentifier() and len(value) <= 64
            and not value.startswith('_'), 'Invalid variable name: ' + repr(value))
    return value


def _arity(op):
    return 1 if op in UNARY else 3 if op in TERNARY else 2


def normalize(node, variables=None, *, limit=MAX_NODES):
    """Validate JSON AST data and apply only domain-preserving rewrites."""
    count = 0

    def visit(item):
        nonlocal count
        count += 1
        require(count <= limit and isinstance(item, dict), 'Invalid or oversized expression')
        if set(item) == {'var'}:
            name = _name(item['var'])
            require(variables is None or name in variables, 'Undeclared variable: ' + name)
            return {'var': name}
        if set(item) == {'const'}:
            return {'const': text(item['const'])}
        require(set(item) == {'op', 'args'} and item['op'] in OPERATORS
                and isinstance(item['args'], list) and len(item['args']) == _arity(item['op']),
                'Invalid expression operator or arguments')
        op, args = item['op'], [visit(arg) for arg in item['args']]
        if op == 'pow':
            require('const' in args[1] and Fraction(args[1]['const']).denominator == 1
                    and abs(Fraction(args[1]['const'])) <= MAX_POWER, 'pow needs a small integer exponent')
        # Identities that never erase a domain error in the other operand.
        if op == 'add' and {'const': '0'} in args:
            return args[1] if args[0] == {'const': '0'} else args[0]
        if op == 'sub' and args[1] == {'const': '0'}:
            return args[0]
        if op in ('mul', 'div') and args[1] == {'const': '1'}:
            return args[0]
        if op == 'mul' and args[0] == {'const': '1'}:
            return args[1]
        if op in COMMUTATIVE and canonical(args[0]) > canonical(args[1]):
            args = [args[1], args[0]]
        return {'op': op, 'args': args}

    return visit(node)


def size(node):
    return 1 + sum(size(arg) for arg in node.get('args', ()))


def variables_of(node):
    if 'var' in node:
        return {node['var']}
    return set().union(*(variables_of(arg) for arg in node.get('args', ())))


def _sqrt(value):
    if value < 0:
        raise DomainError('sqrt of a negative value')
    num, den = math.isqrt(value.numerator), math.isqrt(value.denominator)
    if num * num != value.numerator or den * den != value.denominator:
        raise DomainError('sqrt is not exact rational here')
    return Fraction(num, den)


def _apply(op, values):
    a = values[0]
    if op == 'neg':
        return -a
    if op == 'abs':
        return abs(a)
    if op == 'floor':
        return Fraction(math.floor(a))
    if op == 'ceil':
        return Fraction(math.ceil(a))
    if op == 'sqrt':
        return _sqrt(a)
    if op == 'ite':
        return values[1] if a != 0 else values[2]
    b = values[1]
    if op == 'add':
        return a + b
    if op == 'sub':
        return a - b
    if op == 'mul':
        return a * b
    if op in ('div', 'mod'):
        if b == 0:
            raise DomainError('Division by zero')
        return a / b if op == 'div' else a - b * math.floor(a / b)
    if op == 'pow':
        if a == 0 and b < 0:
            raise DomainError('Zero to a negative power')
        return a ** int(b)
    if op == 'min':
        return min(a, b)
    if op == 'max':
        return max(a, b)
    return Fraction(int(a < b if op == 'lt' else a <= b if op == 'le' else a == b))


def _evaluate(node, env):
    if 'const' in node:
        return Fraction(node['const'])
    if 'var' in node:
        if node['var'] not in env:
            raise DomainError('Missing input: ' + node['var'])
        return env[node['var']]
    if node['op'] == 'ite':  # Only the chosen branch must be defined.
        test = _evaluate(node['args'][0], env)
        return _evaluate(node['args'][1 if test != 0 else 2], env)
    return _bounded(_apply(node['op'], [_evaluate(arg, env) for arg in node['args']]))


def evaluate(node, env):
    """Exact evaluation of a normalized AST over an exact input environment."""
    return _evaluate(node, {_name(k): exact(v) for k, v in env.items()})


def parse(source, variables=None):
    """Compile restricted infix text to the JSON AST; no names or calls leak."""
    if isinstance(source, dict):
        return normalize(source, variables)
    require(isinstance(source, str) and 0 < len(source) <= 2048, 'Expression text required')
    tree = _pyast.parse(source.strip(), mode='eval')
    ops = {_pyast.Add: 'add', _pyast.Sub: 'sub', _pyast.Mult: 'mul', _pyast.Div: 'div',
           _pyast.Mod: 'mod', _pyast.Pow: 'pow'}
    cmps = {_pyast.Lt: 'lt', _pyast.LtE: 'le', _pyast.Eq: 'eq'}
    calls = {'abs': 1, 'floor': 1, 'ceil': 1, 'sqrt': 1, 'min': 2, 'max': 2, 'ite': 3}

    def visit(node):
        if isinstance(node, _pyast.Constant) and type(node.value) in (int, float):
            return {'const': text(node.value)}
        if isinstance(node, _pyast.Name):
            return {'var': node.id}
        if isinstance(node, _pyast.UnaryOp) and isinstance(node.op, (_pyast.USub, _pyast.UAdd)):
            inner = visit(node.operand)
            return inner if isinstance(node.op, _pyast.UAdd) else {'op': 'neg', 'args': [inner]}
        if isinstance(node, _pyast.BinOp) and type(node.op) in ops:
            return {'op': ops[type(node.op)], 'args': [visit(node.left), visit(node.right)]}
        if (isinstance(node, _pyast.Compare) and len(node.ops) == 1
                and type(node.ops[0]) in cmps | {_pyast.Gt: 'gt', _pyast.GtE: 'ge'}):
            left, right = visit(node.left), visit(node.comparators[0])
            kind = type(node.ops[0])
            if kind in (_pyast.Gt, _pyast.GtE):  # a > b is b < a
                return {'op': 'lt' if kind is _pyast.Gt else 'le', 'args': [right, left]}
            return {'op': cmps[kind], 'args': [left, right]}
        if (isinstance(node, _pyast.Call) and isinstance(node.func, _pyast.Name)
                and node.func.id in calls and not node.keywords and len(node.args) == calls[node.func.id]):
            return {'op': node.func.id, 'args': [visit(arg) for arg in node.args]}
        if isinstance(node, _pyast.IfExp):
            return {'op': 'ite', 'args': [visit(node.test), visit(node.body), visit(node.orelse)]}
        raise ValueError('Unsupported expression syntax: ' + type(node).__name__)

    return normalize(visit(tree.body), variables)


def render(node):
    """Readable infix spelling of a normalized AST (display only)."""
    if 'const' in node:
        return node['const'] if '/' not in node['const'] else '(' + node['const'] + ')'
    if 'var' in node:
        return node['var']
    op, args = node['op'], [render(arg) for arg in node['args']]
    if op == 'neg':
        return '(-' + args[0] + ')'
    if op in _INFIX:
        return '(' + args[0] + ' ' + _INFIX[op] + ' ' + args[1] + ')'
    return op + '(' + ', '.join(args) + ')'


def tolerance(spec):
    spec = spec or {}
    require(isinstance(spec, dict) and set(spec) <= {'abs', 'rel'}, 'Tolerance accepts abs/rel only')
    absolute, relative = exact(spec.get('abs', 0)), exact(spec.get('rel', 0))
    require(absolute >= 0 and relative >= 0, 'Tolerance must be non-negative')
    return absolute, relative


def matches(predicted, observed, tol):
    absolute, relative = tol
    return abs(predicted - observed) <= absolute + relative * abs(observed)


def compile_hypothesis(value, inputs, observables, *, goal=None):
    """Freeze one rival rulebook; identity covers only its executable content.

    ``goal`` names the observable an IDENTIFY_LAW rulebook must predict.
    """
    require(isinstance(value, dict) and {'id', 'predict'} <= set(value)
            and set(value) <= {'id', 'statement', 'predict', 'applies', 'premises', 'origin', 'parent'},
            'Hypothesis requires id and predict')
    ident = value['id']
    require(isinstance(ident, str) and 0 < len(ident) <= 80 and ident.replace('-', '_').replace(':', '_').replace('.', '_').isidentifier(),
            'Invalid hypothesis id')
    predict = value['predict']
    require(isinstance(predict, dict) and predict and set(predict) <= set(observables),
            'Hypothesis must predict declared observables only')
    require(goal is None or goal in predict, f'Hypothesis must predict the goal observable {goal}')
    rules = {name: parse(predict[name], set(inputs)) for name in sorted(predict)}
    applies = value.get('applies')
    applies = parse(applies, set(inputs)) if applies is not None else None
    premises = value.get('premises', [])
    require(isinstance(premises, list) and len(premises) <= 16
            and all(isinstance(p, str) and 0 < len(p) <= 512 for p in premises), 'Invalid premises')
    statement = value.get('statement') or ' ; '.join(f'{k} = {render(v)}' for k, v in rules.items())
    require(isinstance(statement, str) and len(statement) <= 2048, 'Invalid statement')
    executable = {'predict': rules, 'applies': applies}
    return {'id': ident, 'statement': statement, 'predict': rules, 'applies': applies,
            'premises': list(premises), 'origin': value.get('origin', 'declared'),
            'parent': value.get('parent'), 'sha256': digest(executable),
            'size': sum(size(v) for v in rules.values()) + (size(applies) if applies else 0)}


def predict(hypothesis, env):
    """Return {observable: Fraction|None}; None marks NOT_APPLICABLE/UNDEFINED."""
    try:
        if hypothesis['applies'] is not None and evaluate(hypothesis['applies'], env) == 0:
            return {name: None for name in hypothesis['predict']}, 'NOT_APPLICABLE'
        return {name: evaluate(rule, env) for name, rule in hypothesis['predict'].items()}, 'PREDICTED'
    except DomainError:
        return {name: None for name in hypothesis['predict']}, 'UNDEFINED'


def replay(hypothesis, observations, tolerances):
    """Cell-exact replay over original measurements; counterexamples retained.

    UNDEFINED where the rulebook claims applicability counts against it: an
    explanation that cannot produce its own prediction does not explain the
    observation. Missing measurements stay UNKNOWN and refute nothing.
    """
    counterexamples, tested, skipped, unknown = [], 0, 0, 0
    for row in observations:
        values, status = predict(hypothesis, row['inputs'])
        if status == 'NOT_APPLICABLE':
            skipped += 1
            continue
        for name in hypothesis['predict']:
            observed = row['outputs'].get(name)
            if observed is None:
                unknown += 1
                continue
            tested += 1
            if status == 'UNDEFINED':
                counterexamples.append({'id': row['id'], 'observable': name, 'predicted': None,
                                        'observed': text(observed), 'residual': None})
            elif not matches(values[name], observed, tolerances[name]):
                counterexamples.append({'id': row['id'], 'observable': name, 'predicted': text(values[name]),
                                        'observed': text(observed), 'residual': text(observed - values[name])})
    status = 'REFUTED' if counterexamples else 'CONSISTENT' if tested else 'UNTESTED'
    return {'status': status, 'counterexamples': counterexamples, 'tested': tested,
            'not_applicable': skipped, 'unknown_measurements': unknown, 'scientific_support': 'UNKNOWN'}


def _classes(predictions, tol):
    """Group exact predictions whose values lie within tolerance of a class anchor."""
    groups = []
    for ident, value in sorted(predictions, key=lambda row: (row[1], row[0])):
        if groups and matches(value, groups[-1][0], tol):
            groups[-1][1].append(ident)
        else:
            groups.append((value, [ident]))
    return groups


def _entropy(weights):
    total = sum(weights)
    return -sum(w / total * math.log2(w / total) for w in weights if w > 0) if total > 0 else 0.0


def prior(hypothesis):
    """Description-length prior: smaller executable rulebooks weigh more."""
    return 2.0 ** -min(hypothesis['size'], 60)


def outcome_partition(hypotheses, env, tolerances):
    """Partition live rivals by what they predict for one candidate experiment."""
    determinate, undetermined, undefined = {}, [], []
    for hyp in hypotheses:
        values, status = predict(hyp, env)
        if status == 'NOT_APPLICABLE':
            undetermined.append(hyp['id'])
            continue
        if status == 'UNDEFINED':
            # Replay counts an undefined prediction where the rulebook applies as a
            # counterexample, so any measurement here eliminates it: its own class.
            undefined.append(hyp['id'])
            continue
        for name, value in values.items():
            determinate.setdefault(name, []).append((hyp['id'], value))
    keys = {}
    for name in sorted(determinate):
        for index, (anchor, members) in enumerate(_classes(determinate[name], tolerances[name])):
            for ident in members:
                keys.setdefault(ident, []).append((name, text(anchor), index))
    classes = {}
    for ident, key in keys.items():
        classes.setdefault(tuple(key), []).append(ident)
    rows = [{'prediction': {name: value for name, value, _ in key}, 'hypotheses': sorted(ids)}
            for key, ids in sorted(classes.items(), key=lambda item: (item[0], item[1]))]
    if undefined:
        rows.append({'prediction': 'UNDEFINED', 'hypotheses': sorted(undefined)})
    return rows, sorted(undetermined)


def information_gain(hypotheses, env, tolerances, weights=None):
    """Expected entropy reduction over rivals if this experiment is observed.

    Hypotheses that do not predict here survive every outcome; they cannot be
    discriminated by this experiment and dilute its value accordingly.
    """
    weights = weights or {h['id']: prior(h) for h in hypotheses}
    rows, undetermined = outcome_partition(hypotheses, env, tolerances)
    total = sum(weights[h['id']] for h in hypotheses)
    if total <= 0 or len(hypotheses) < 2:
        return {'eig_bits': 0.0, 'classes': rows, 'undetermined': undetermined, 'discriminated_pairs': 0}
    before = _entropy([weights[h['id']] for h in hypotheses])
    mass = [sum(weights[i] for i in row['hypotheses']) for row in rows]
    determinate = sum(mass)
    spread = [weights[i] for i in undetermined]
    after = 0.0
    for row, m in zip(rows, mass):
        if determinate > 0:
            after += m / determinate * _entropy([weights[i] for i in row['hypotheses']] + spread)
    if not rows:
        after = before
    sizes = [len(row['hypotheses']) for row in rows]
    pairs = (sum(sizes) ** 2 - sum(s * s for s in sizes)) // 2
    return {'eig_bits': round(max(0.0, before - after), 9), 'classes': rows,
            'undetermined': undetermined, 'discriminated_pairs': pairs}


def signature(rows):
    """Order-free partition identity: equal signatures discriminate identically."""
    return digest(sorted(sorted(row['hypotheses']) for row in rows))


def rank_experiments(hypotheses, candidates, tolerances, *, observed=(), weights=None, limit=8):
    """Rank candidate experiments by expected information gain per unit cost.

    Already-measured inputs are never repurchased; candidates whose partition
    equals a better-ranked one are retained as EQUIVALENT, not ranked twice.
    """
    seen_inputs = {canonical({k: text(v) for k, v in env.items()}) for env in observed}
    scored, excluded = [], []
    for item in candidates:
        key = canonical({k: text(v) for k, v in item['inputs'].items()})
        if key in seen_inputs:
            excluded.append({'id': item['id'], 'reason': 'ALREADY_OBSERVED'})
            continue
        gain = information_gain(hypotheses, item['inputs'], tolerances, weights)
        cost = exact(item.get('cost', 1))
        require(cost > 0, 'Experiment cost must be positive')
        scored.append({'id': item['id'], 'inputs': {k: text(v) for k, v in item['inputs'].items()},
                       'cost': text(cost), 'eig_bits': gain['eig_bits'],
                       'eig_per_cost': round(gain['eig_bits'] / float(cost), 9),
                       'discriminated_pairs': gain['discriminated_pairs'],
                       'classes': gain['classes'], 'undetermined': gain['undetermined'],
                       'partition_sha256': signature(gain['classes'])})
    scored.sort(key=lambda row: (-row['eig_per_cost'], -row['discriminated_pairs'], row['id']))
    ranked, partitions = [], {}
    for row in scored:
        if row['eig_bits'] <= 0:
            excluded.append({'id': row['id'], 'reason': 'NON_DISCRIMINATING'})
        elif row['partition_sha256'] in partitions:
            excluded.append({'id': row['id'], 'reason': 'EQUIVALENT_PARTITION',
                             'equivalent_to': partitions[row['partition_sha256']]})
        else:
            partitions[row['partition_sha256']] = row['id']
            ranked.append(row)
    return {'ranked': ranked[:limit], 'ranked_total': len(ranked),
            'excluded': excluded, 'selection_effect': 'ADVISORY'}


def behavior(hypothesis, points):
    """Prediction vector over probe points (None for undetermined)."""
    out = []
    for env in points:
        values, status = predict(hypothesis, env)
        out.append(None if status != 'PREDICTED' else {k: text(v) for k, v in sorted(values.items())})
    return out


def equivalent(left, right, points, tolerances):
    """Executable equivalence on the available experiment space only."""
    if left['sha256'] == right['sha256']:
        return 'IDENTICAL_EXECUTABLE'
    determinate = 0
    for env in points:
        a, sa = predict(left, env)
        b, sb = predict(right, env)
        if (sa == 'PREDICTED') != (sb == 'PREDICTED'):
            return None
        if sa != 'PREDICTED':
            continue
        if set(a) != set(b) or any(not matches(a[k], b[k], tolerances[k]) for k in a):
            return None
        determinate += 1
    return 'OBSERVATIONALLY_EQUIVALENT' if determinate else None


# --- Grammar-guided synthesis with counterexample filtering -----------------

def _grammar(grammar, inputs):
    grammar = grammar or {}
    require(isinstance(grammar, dict) and set(grammar) <= {'variables', 'constants', 'operators'},
            'Grammar accepts variables/constants/operators')
    names = grammar.get('variables', list(inputs))
    require(isinstance(names, list) and names and set(names) <= set(inputs), 'Grammar variables must be inputs')
    constants = [text(c) for c in grammar.get('constants', [0, 1, 2, -1])]
    require(len(constants) <= 24 and len(set(constants)) == len(constants), 'At most 24 distinct constants')
    operators = grammar.get('operators', ['add', 'sub', 'mul', 'div'])
    require(isinstance(operators, list) and set(operators) <= OPERATORS - TERNARY,
            'Synthesis operators must be unary/binary')
    return sorted(set(names)), constants, sorted(set(operators))


def synthesize(observations, inputs, observable, tolerances, *, grammar=None, max_nodes=7,
               max_candidates=20000, probes=(), base=None, keep=4):
    """Bottom-up enumeration with observational-equivalence pruning.

    Each candidate must reproduce every original measurement (the counterexample
    filter). Value vectors cover observed AND probe inputs, so two retained fits
    always differ on some probe: they arrive as rivals with distinct predictions,
    never as renamed copies. With ``base`` the search fits the residual
    ``observed - base`` and returns ``base + correction`` (a premise shift).
    Exhaustion speaks only about this grammar and bound.
    """
    require(1 <= max_nodes <= MAX_NODES and 1 <= max_candidates <= MAX_SYNTH_CANDIDATES, 'Synthesis bounds')
    names, constants, operators = _grammar(grammar, inputs)
    rows = [row for row in observations if row['outputs'].get(observable) is not None]
    require(rows, 'Synthesis needs at least one measured observation')
    require(len(rows) + len(probes) <= MAX_POINTS, 'Too many observation/probe points')
    points = [{k: exact(v) for k, v in row['inputs'].items()} for row in rows]
    points += [{k: exact(v) for k, v in env.items()} for env in probes]
    targets = [exact(row['outputs'][observable]) for row in rows]
    if base is not None:
        offsets = []
        for env in points[:len(rows)]:
            try:
                offsets.append(_evaluate(base, env))
            except DomainError:
                return {'status': 'BASE_UNDEFINED', 'fits': [], 'attempted': 0, 'scientific_support': 'UNKNOWN'}
        targets = [t - o for t, o in zip(targets, offsets)]
    tol = tolerances[observable]
    n = len(rows)
    pools = {s: [] for s in range(1, max_nodes + 1)}
    seen, fits = set(), []
    attempted = 0
    stopped = False

    def vector(node):
        out = []
        for env in points:
            try:
                out.append(_evaluate(node, env))
            except DomainError:
                out.append(None)
        return tuple(out)

    def consider(node):
        nonlocal attempted, stopped
        if attempted >= max_candidates:
            stopped = True
            return False
        attempted += 1
        try:
            node = normalize(node, set(names), limit=max_nodes)
        except (ValueError, DomainError):
            return True
        values = vector(node)
        key = tuple(None if v is None else (v.numerator, v.denominator) for v in values)
        if key in seen:
            return True  # Observationally equivalent to a smaller retained expression.
        seen.add(key)
        pools[size(node)].append((node, values))
        if all(v is not None and matches(v, t, tol) for v, t in zip(values[:n], targets)):
            fits.append(node)
        return len(fits) < keep

    leaves = [{'var': v} for v in names] + [{'const': c} for c in constants]
    go = all(consider(leaf) for leaf in leaves)
    for total in range(2, max_nodes + 1):
        if not go:
            break
        for op in operators:
            if not go:
                break
            if op in UNARY:
                for child, _ in tuple(pools[total - 1]):
                    if not consider({'op': op, 'args': [child]}):
                        go = False
                        break
                continue
            if op == 'pow':
                exps = [c for c in constants if Fraction(c).denominator == 1 and 0 < abs(Fraction(c)) <= MAX_POWER]
                for child, _ in tuple(pools.get(total - 2, ())):
                    for e in exps:
                        if not consider({'op': 'pow', 'args': [child, {'const': e}]}):
                            go = False
                            break
                    if not go:
                        break
                continue
            for left_size in range(1, total - 1):
                right_size = total - 1 - left_size
                if op in COMMUTATIVE and left_size > right_size:
                    continue
                for left, _ in tuple(pools[left_size]):
                    for right, _ in tuple(pools[right_size]):
                        if not consider({'op': op, 'args': [left, right]}):
                            go = False
                            break
                    if not go:
                        break
                if not go:
                    break
    results = []
    for node in fits:
        full = node if base is None else normalize({'op': 'add', 'args': [base, node]}, None, limit=2 * MAX_NODES + 1)
        results.append({'expression': full, 'text': render(full), 'size': size(full),
                        'correction': render(node) if base is not None else None})
    return {'status': 'FIT_FOUND' if results else 'SEARCH_TRUNCATED' if stopped else 'NO_FIT',
            'fits': results, 'attempted': attempted, 'distinct_behaviours': len(seen),
            'limits': {'max_nodes': max_nodes, 'max_candidates': max_candidates},
            'grammar': {'variables': names, 'constants': constants, 'operators': operators},
            'mode': 'RESIDUAL_CORRECTION' if base is not None else 'WHOLE_LAW',
            'scientific_support': 'UNKNOWN'}


def regime_split(observations, inputs, observable, tolerances, *, variable, grammar=None,
                 max_nodes=5, max_candidates=4000, max_splits=12, probes=()):
    """Search a two-regime premise shift: one law below a threshold, another above."""
    rows = sorted((r for r in observations if r['outputs'].get(observable) is not None),
                  key=lambda r: exact(r['inputs'][variable]))
    values = sorted({exact(r['inputs'][variable]) for r in rows})
    if len(values) < 4:
        return {'status': 'TOO_FEW_POINTS', 'fits': [], 'scientific_support': 'UNKNOWN'}
    cuts = [(a + b) / 2 for a, b in zip(values, values[1:])][1:-1]
    step = max(1, len(cuts) // max_splits)
    fits, attempted = [], 0
    for cut in cuts[::step][:max_splits]:
        left = [r for r in rows if exact(r['inputs'][variable]) < cut]
        right = [r for r in rows if exact(r['inputs'][variable]) >= cut]
        lp = [p for p in probes if exact(p[variable]) < cut]
        rp = [p for p in probes if exact(p[variable]) >= cut]
        a = synthesize(left, inputs, observable, tolerances, grammar=grammar, max_nodes=max_nodes,
                       max_candidates=max_candidates, probes=lp, keep=1)
        b = synthesize(right, inputs, observable, tolerances, grammar=grammar, max_nodes=max_nodes,
                       max_candidates=max_candidates, probes=rp, keep=1)
        attempted += a['attempted'] + b['attempted']
        if a['fits'] and b['fits']:
            node = {'op': 'ite', 'args': [{'op': 'lt', 'args': [{'var': variable}, {'const': text(cut)}]},
                                         a['fits'][0]['expression'], b['fits'][0]['expression']]}
            node = normalize(node, None, limit=2 * MAX_NODES + 3)
            fits.append({'expression': node, 'text': render(node), 'size': size(node), 'cut': text(cut)})
    fits.sort(key=lambda f: (f['size'], f['text']))
    return {'status': 'FIT_FOUND' if fits else 'NO_FIT', 'fits': fits[:4], 'attempted': attempted,
            'mode': 'REGIME_SPLIT', 'variable': variable, 'scientific_support': 'UNKNOWN'}


# --- Per-sample profile: averages must not hide local regressions ----------

def sample_profile(baseline, candidate, *, higher_is_better=True):
    """Compare per-sample metrics; report mean gain and the worst degradation."""
    require(isinstance(baseline, dict) and isinstance(candidate, dict) and baseline
            and set(baseline) == set(candidate), 'Per-sample metrics must cover the same samples')
    sign = 1 if higher_is_better else -1
    deltas = {k: sign * (exact(candidate[k]) - exact(baseline[k])) for k in sorted(baseline)}
    worst_key = min(deltas, key=lambda k: (deltas[k], k))
    mean = sum(deltas.values()) / len(deltas)
    return {'samples': len(deltas), 'mean_gain': text(mean), 'worst_sample': worst_key,
            'worst_degradation': text(max(Fraction(0), -deltas[worst_key])),
            'degraded': sum(1 for v in deltas.values() if v < 0),
            'improved': sum(1 for v in deltas.values() if v > 0)}


def profile_verdict(profile, max_degradation):
    """ACCEPT only when the mean improves and no sample degrades past the bound."""
    if exact(profile['mean_gain']) <= 0:
        return 'NO_MEAN_GAIN'
    if exact(profile['worst_degradation']) > exact(max_degradation):
        return 'LOCAL_REGRESSION'
    return 'ACCEPT'
