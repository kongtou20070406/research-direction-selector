"""Local dimensional legality for the Compass expression AST dialect.

A *local* judgment, not a global one. `dimension_of` says whether a form is
dimensionally well formed on its own; it never asks whether the two sides of
an equation balance. The devset laws are frequently de-dimensionalised (the
gravitational constant of Feynman I.9.18 is absorbed into `m1*m2/r**2`), so a
global-balance criterion would reject true laws. Adding a mass to a length is
illegal in any context, and that is all this module decides.

Dimensions are 7-tuples of integers over SI base units in this order:

    (M, L, T, I, Theta, N, J)

mass, length, time, electric current, thermodynamic temperature, amount of
substance, luminous intensity. Constants, pi and the dimensionless builtins
carry the zero vector. The function is pure: no I/O, no globals, and it fails
closed on any operator it does not know.
"""
from fractions import Fraction

ZERO = (0, 0, 0, 0, 0, 0, 0)
RANK = 7

_UNARY_PRESERVING = frozenset(('neg', 'abs', 'floor', 'ceil'))
_UNARY_DIMENSIONLESS = frozenset(('exp', 'log', 'sin', 'cos'))
_BINARY_SAMEDIM = frozenset(('add', 'sub', 'min', 'max', 'mod'))
_ARITY = {'pi': 0, 'pow': 2, 'mul': 2, 'div': 2}
_ARITY.update({op: 1 for op in _UNARY_PRESERVING | _UNARY_DIMENSIONLESS | {'sqrt'}})
_ARITY.update({op: 2 for op in _BINARY_SAMEDIM})
_ARITY.update({'ite': 3})


def _vector(value):
    """Coerce a declared dimension to a 7-tuple of ints, or None when malformed."""
    if not isinstance(value, tuple) or len(value) != RANK:
        return None
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        out.append(item)
    return tuple(out)


def _add(left, right):
    return tuple(a + b for a, b in zip(left, right))


def _sub(left, right):
    return tuple(a - b for a, b in zip(left, right))


def _scale(value, factor):
    return tuple(item * factor for item in value)


def _even(value):
    return all(item % 2 == 0 for item in value)


def _constant(node):
    """The exact rational of a ``const`` node, else None."""
    if not isinstance(node, dict) or set(node) != {'const'}:
        return None
    try:
        return Fraction(node['const'])
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def dimension_of(node, dimensions):
    """Return the 7-tuple dimension of ``node``, or None when the form is not
    dimensionally well formed. ``dimensions`` maps variable name to a 7-tuple of
    integers. Constants, pi and the dimensionless builtins have the zero vector.

    None means "illegal or unknown"; it never means "balanced" or "unbalanced".
    """
    if not isinstance(node, dict) or not isinstance(dimensions, dict):
        return None
    if set(node) == {'const'}:
        return ZERO if _constant(node) is not None else None
    if set(node) == {'var'}:
        return _vector(dimensions.get(node['var']))
    if set(node) != {'op', 'args'} or not isinstance(node['args'], list):
        return None
    op, args = node['op'], node['args']
    if op not in _ARITY or len(args) != _ARITY[op]:
        return None
    if op == 'pi':
        return ZERO

    parts = [dimension_of(arg, dimensions) for arg in args]
    if any(part is None for part in parts):
        return None

    if op in _UNARY_PRESERVING:
        return parts[0]
    if op in _UNARY_DIMENSIONLESS:
        return ZERO if parts[0] == ZERO else None
    if op == 'sqrt':
        if not _even(parts[0]):
            return None
        return tuple(item // 2 for item in parts[0])
    if op in _BINARY_SAMEDIM:
        return parts[0] if parts[0] == parts[1] else None
    if op == 'mul':
        return _add(parts[0], parts[1])
    if op == 'div':
        return _sub(parts[0], parts[1])
    if op == 'pow':
        base, power = parts[0], parts[1]
        if power != ZERO:
            return None
        exact = _constant(args[1])
        if exact is not None and exact.denominator == 1:
            return _scale(base, int(exact))
        return ZERO if base == ZERO else None
    if op == 'ite':
        condition, yes, no = parts
        if condition != ZERO or yes != no:
            return None
        return yes
    return None
