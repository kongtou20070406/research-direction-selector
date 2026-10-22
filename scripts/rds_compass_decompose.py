"""Inverse decomposition: guess the outermost operator, then solve for the other operand.

Bottom-up enumeration pays for every tree below the bound, so a wide leaf pool can eat
the candidate budget at a shallow level while the law sits one layer deeper. Decomposition
pays once for a depth-limited pool and then inverts: for each candidate outermost operator
and each pool node playing the known side, the value vector the *other* side must have is
computed arithmetically and looked up in a per-size hash index.

Inverse table (``t`` is the observed target vector, ``o`` the known side)::

    t = left / right    other = right -> left  = t * o
                        other = left  -> right = o / t
    t = left * right    other = right -> left  = t / o
                        other = left  -> right = t / o
    t = left + right    need = t - o              (one direction: add commutes)
    t = left - right    other = left  -> right = o - t
                        other = right -> left  = t + o

Every assembled candidate is re-evaluated point by point over the whole tree before it is
reported, so a wrong inverse formula costs a miss, never a false fit. Only exact rational
value vectors are inverted: one ``Inexact`` component in the targets or the pool entries
used makes the run ``INEXACT_UNSUPPORTED``. Exhaustion speaks only about this grammar and
bound; ``max_candidates`` caps the one-time pool build, and hitting it is reported as
``SEARCH_TRUNCATED``, never as NO_FIT.
"""
from rds_compass_model import (COMMUTATIVE, DomainError, Inexact, MAX_NODES, MAX_POINTS,
                               MAX_SYNTH_CANDIDATES, NULLARY, UNARY, Undecided, _enclose,
                               _evaluate, _grammar, _name, _step, exact, matches, normalize,
                               parse, render, require, size)

INVERSE_OPERATORS = ('add', 'sub', 'mul', 'div')
MODE = 'INVERSE_DECOMPOSITION'


def _inexact(value):
    return hasattr(value, 'radius')


def _point_key(values):
    """Hashable exact value vector; ``None`` marks an undefined point."""
    return tuple(None if v is None else (v.numerator, v.denominator) for v in values)


def _inverse(op, other_is_left, targets, other_values):
    """The value vector the missing side must carry, or None when a point cannot be solved."""
    need = []
    for target, other in zip(targets, other_values):
        if target is None or other is None:
            return None
        try:
            if op == 'add':
                need.append(target - other)
            elif op == 'sub':
                need.append(other - target if other_is_left else target + other)
            elif op == 'mul':
                need.append(target / other)
            elif other_is_left:
                if target == 0:
                    return None
                need.append(other / target)
            else:
                need.append(target * other)
        except (ZeroDivisionError, DomainError):
            return None
    return tuple(need)


def _index_lookup(index, need):
    """One hash probe: the pool node whose whole value vector equals ``need``, else None."""
    return index.get(_point_key(need))


def _build_pools(extended_points, names, constants, derived, operators, half, budget):
    """Bottom-up enumeration truncated at ``half``; one ``(node, spans, values)`` per behaviour."""
    def values_from(spans):
        return tuple(None if span is None else (span[0] if span[0] == span[1] else Inexact(*span))
                     for span in spans)

    def spans_of(node):
        out = []
        for env in extended_points:
            try:
                out.append(_enclose(node, env))
            except (DomainError, Undecided):
                out.append(None)
        return tuple(out)

    def combine(op, child_spans):
        out = []
        for index in range(len(extended_points)):
            values = [spans[index] for spans in child_spans]
            if any(value is None for value in values):
                out.append(None)
                continue
            try:
                out.append(_step(op, values))
            except (DomainError, Undecided):
                out.append(None)
        return tuple(out)

    allowed = set(names) | set(derived)
    pools = {level: [] for level in range(1, half + 1)}
    seen, attempted, stopped = set(), 0, False

    def consider(node, spans=None):
        nonlocal attempted, stopped
        if attempted >= budget:
            stopped = True
            return False
        attempted += 1
        try:
            normalized = normalize(node, allowed, limit=half)
        except (ValueError, DomainError):
            return True
        if spans is None:
            spans = spans_of(normalized)
        values = values_from(spans)
        key = _point_key(values)
        if key in seen:
            return True
        seen.add(key)
        pools[size(normalized)].append((normalized, spans, values))
        return True

    leaves = [{'var': v} for v in names] + [{'const': c} for c in constants]
    leaves += [{'var': name} for name in sorted(derived)]
    leaves += [{'op': op, 'args': []} for op in operators if op in NULLARY]
    for leaf in leaves:
        if not consider(leaf):
            break
    for total in range(2, half + 1):
        if stopped:
            break
        for op in operators:
            if stopped:
                break
            if op in NULLARY:
                continue
            if op in UNARY:
                for child, child_spans, _values in list(pools[total - 1]):
                    if not consider({'op': op, 'args': [child]}, combine(op, [child_spans])):
                        break
                continue
            if op == 'pow':
                continue  # A constant exponent leaves the inverse ambiguous; only four operators invert here.
            for left_size in range(1, total - 1):
                right_size = total - 1 - left_size
                if op in COMMUTATIVE and left_size > right_size:
                    continue
                for left, left_spans, _values in list(pools[left_size]):
                    for right, right_spans, _left in list(pools[right_size]):
                        if not consider({'op': op, 'args': [left, right]},
                                        combine(op, [left_spans, right_spans])):
                            break
                    if stopped:
                        break
                if stopped:
                    break
    return pools, attempted, stopped, seen


def _verify(op, left, right, extended_points, targets, count, tol, max_nodes, allowed):
    """Whole-tree re-evaluation: the assembled candidate must match every observed point."""
    try:
        normalized = normalize({'op': op, 'args': [left, right]}, allowed, limit=max_nodes)
    except (ValueError, DomainError):
        return None
    if size(normalized) > max_nodes:
        return None
    values = []
    for env in extended_points:
        try:
            values.append(_evaluate(normalized, env))
        except (DomainError, Undecided):
            return None
    if not all(matches(value, target, tol) for value, target in zip(values[:count], targets)):
        return None
    return normalized, values


def decompose(observations, inputs, observable, tolerances, *, grammar=None,
              max_nodes=7, max_candidates=20000, probes=(), derived=None,
              half=None, keep=4):
    """Inverse-decomposition search; returns the same shape as ``synthesize``.

    ``half`` bounds the pool depth (default ``max(2, (max_nodes - 1) // 2)``); the outermost
    operator is always one of add/sub/mul/div, so any tree up to ``2 * half + 1`` nodes is
    reachable. Only exact rational value vectors invert; one ``Inexact`` target anywhere
    returns ``INEXACT_UNSUPPORTED`` rather than a rounded lookup.
    """
    require(1 <= max_nodes <= MAX_NODES and 1 <= max_candidates <= MAX_SYNTH_CANDIDATES,
            'Decomposition bounds')
    names, constants, operators = _grammar(grammar, inputs)
    rows = [row for row in observations if row['outputs'].get(observable) is not None]
    require(rows, 'Decomposition needs at least one measured observation')
    require(len(rows) + len(probes) <= MAX_POINTS, 'Too many observation/probe points')
    points = [{k: exact(v) for k, v in row['inputs'].items()} for row in rows]
    points += [{k: exact(v) for k, v in env.items()} for env in probes]
    targets = [exact(row['outputs'][observable]) for row in rows]
    derived = derived or {}
    derived_values = {}
    for derived_name, expression in derived.items():
        _name(derived_name)
        node = parse(expression, set(names))
        values = []
        for env in points:
            try:
                values.append(_evaluate(node, env))
            except (DomainError, Undecided):
                values.append(None)
        derived_values[derived_name] = tuple(values)
    extended_points = [{**env, **{name: values[index] for name, values in derived_values.items()
                                  if values[index] is not None}}
                       for index, env in enumerate(points)]
    half = max(2, (max_nodes - 1) // 2) if half is None else half
    require(1 <= half <= max_nodes, 'Decomposition half bound')
    allowed = set(names) | set(derived)
    tol = tolerances[observable]
    count = len(rows)
    used = [op for op in INVERSE_OPERATORS if op in operators]
    notes = {'half': half, 'pools': {}, 'lookups': 0, 'hits': 0, 'inverse_attempted': 0,
             'pool_attempted': 0, 'points': count, 'probes': len(probes),
             'inverse_operators': used, 'pool_size': 0}
    limits = {'max_nodes': max_nodes, 'max_candidates': max_candidates, 'half': half}
    grammar_note = {'variables': names, 'constants': constants, 'operators': operators}
    result = {'status': 'NO_FIT', 'fits': [], 'attempted': 0, 'distinct_behaviours': 0,
              'mode': MODE, 'limits': limits, 'grammar': grammar_note,
              'scientific_support': 'UNKNOWN', 'notes': notes}
    if any(_inexact(target) for target in targets):
        return {**result, 'status': 'INEXACT_UNSUPPORTED',
                'notes': {**notes, 'reason': 'observed targets carry inexact enclosures'}}
    pools, pool_attempted, stopped, seen = _build_pools(
        extended_points, names, constants, derived, operators, half, max_candidates)
    notes['pool_attempted'] = pool_attempted
    notes['pools'] = {level: len(entries) for level, entries in pools.items()}
    notes['pool_size'] = sum(len(entries) for entries in pools.values())
    indexes = {}
    for level, entries in pools.items():
        index = {}
        for node, _spans, values in entries:
            if any(value is None or _inexact(value) for value in values):
                continue
            index.setdefault(_point_key(values), node)
        indexes[level] = index
    fits = {}
    attempted, lookups, hits = pool_attempted, 0, 0
    for op in used:
        for other_is_left in ((True,) if op == 'add' else (True, False)):
            for level in sorted(pools):
                for other, _spans, other_values in pools[level]:
                    if any(value is None or _inexact(value) for value in other_values):
                        continue
                    attempted += 1
                    need = _inverse(op, other_is_left, targets, other_values)
                    if need is None:
                        continue
                    for need_level in sorted(indexes):
                        if level + need_level + 1 > max_nodes:
                            continue
                        lookups += 1
                        found = _index_lookup(indexes[need_level], need)
                        if found is None:
                            continue
                        hits += 1
                        left, right = (other, found) if other_is_left else (found, other)
                        verified = _verify(op, left, right, extended_points, targets, count,
                                           tol, max_nodes, allowed)
                        if verified is None:
                            break
                        node, values = verified
                        key = _point_key(values)
                        if key not in fits or size(node) < size(fits[key]):
                            fits[key] = node
                        break
    notes['lookups'], notes['hits'] = lookups, hits
    notes['inverse_attempted'] = attempted - pool_attempted
    ordered = sorted(fits.values(), key=lambda node: (size(node), render(node)))[:keep]
    results = [{'expression': node, 'text': render(node), 'size': size(node), 'correction': None}
               for node in ordered]
    status = 'FIT_FOUND' if results else 'SEARCH_TRUNCATED' if stopped else 'NO_FIT'
    return {**result, 'status': status, 'fits': results, 'attempted': attempted,
            'distinct_behaviours': len(seen)}
