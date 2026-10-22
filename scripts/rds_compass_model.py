"""Executable rival-hypothesis models for the Compass Advisor (pure values).

A hypothesis is a small rulebook: one bounded expression per declared
observable plus an optional applicability predicate. Compilation yields an
exact Fraction predictor; replay against original measurements decides whether
the rulebook still reproduces every observed transition within the declared
tolerance. Nothing here reads a ledger, starts a process or certifies a claim:
CONSISTENT means "not contradicted by these observations", never SUPPORT.

Irrational values (exp, log, sin, cos, pi, non-square roots, rational powers)
are never rounded into a point: evaluation carries a rigorous rational
enclosure, and replay counts a cell only when the whole enclosure lies inside
or outside the tolerance band. A cell the enclosure cannot decide stays UNKNOWN.
"""
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN
from fractions import Fraction
import ast as _pyast
import math

from rds_core import canonical, object_digest

MAX_NODES = 15  # One synthesized expression.
# A rulebook: a regime split or residual correction of synthesized parts (2 * MAX_NODES + 3),
# or a declared or agent rulebook of the same order. Synthesis itself stays within MAX_NODES.
MAX_RULEBOOK_NODES = 64
MAX_BITS = 512
# Longest spelling text() writes (sign, two MAX_BITS-bit integers, slash): exact() reads every one back.
MAX_TEXT = 2 * len(str(2 ** MAX_BITS)) + 2
MAX_POWER = 8
MAX_ROOT = 8  # Largest denominator of a rational exponent.
MAX_POINTS = 256
MAX_SYNTH_CANDIDATES = 200000
TRANSCENDENTAL = frozenset(('exp', 'log', 'sin', 'cos'))
NULLARY = frozenset(('pi',))
UNARY = frozenset(('neg', 'abs', 'floor', 'ceil', 'sqrt')) | TRANSCENDENTAL
BINARY = frozenset(('add', 'sub', 'mul', 'div', 'mod', 'pow', 'min', 'max', 'lt', 'le', 'eq'))
TERNARY = frozenset(('ite',))
OPERATORS = NULLARY | UNARY | BINARY | TERNARY
COMMUTATIVE = frozenset(('add', 'mul', 'min', 'max', 'eq'))
_INFIX = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/', 'mod': '%', 'pow': '**',
          'lt': '<', 'le': '<=', 'eq': '=='}
DIGITS = 40  # Significant digits of an enclosure endpoint.
_PI = (Fraction('3.141592653589793238462643383279502884197'), Fraction('3.141592653589793238462643383279502884198'))
_FLOOR, _CEIL, _NEAR = (Context(prec=DIGITS, rounding=r, Emax=10 ** 6, Emin=-10 ** 6)
                        for r in (ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_EVEN))
_WORK = Context(prec=60, rounding=ROUND_HALF_EVEN, Emax=10 ** 6, Emin=-10 ** 6)
_SERIES_ERROR = Fraction(1, 10 ** 50)  # 60-digit Taylor sum at |r| < 1: rounding stays below 1e-55.


class DomainError(ValueError):
    """An expression is undefined or leaves its bounded exact domain here."""


class Undecided(ValueError):
    """An enclosure straddles a decision (a branch, an integer or zero); the cell stays UNKNOWN."""


class Inexact(Fraction):
    """The midpoint of a rigorous enclosure of an irrational value; ``radius`` bounds its error."""
    __slots__ = ('radius',)

    def __new__(cls, lo, hi):
        self = super().__new__(cls, (lo + hi) / 2)
        self.radius = (hi - lo) / 2
        return self

    def __deepcopy__(self, memo=None):
        return self

    __copy__ = __deepcopy__

    def __reduce__(self):
        mid, radius = _envelope(self)
        return Inexact, (mid - radius, mid + radius)

    # Arithmetic carries the enclosure. Without these, ``a - b`` returns a plain Fraction and
    # every downstream decision reads the midpoint of an interval as if it were a point value.
    def __add__(self, other):
        a, ra = _envelope(self)
        b, rb = _envelope(other)
        return _carry(a + b, ra + rb)

    __radd__ = __add__

    def __sub__(self, other):
        a, ra = _envelope(self)
        b, rb = _envelope(other)
        return _carry(a - b, ra + rb)

    def __rsub__(self, other):
        a, ra = _envelope(other)
        b, rb = _envelope(self)
        return _carry(a - b, ra + rb)

    def __mul__(self, other):
        a, ra = _envelope(self)
        b, rb = _envelope(other)
        return _carry(a * b, abs(a) * rb + abs(b) * ra + ra * rb)  # Conservative product envelope.

    __rmul__ = __mul__

    def __truediv__(self, other):
        a, ra = _envelope(self)
        return _quotient(a, ra, *_envelope(other))

    def __rtruediv__(self, other):
        b, rb = _envelope(self)
        return _quotient(*_envelope(other), b, rb)


def _envelope(value):
    """The exact numeric value and the half-width it stands for; a plain number has no radius."""
    return (Fraction(value.numerator, value.denominator), getattr(value, 'radius', 0)) \
        if isinstance(value, Fraction) else (exact(value), 0)


def _carry(mid, radius):
    """A result keeps its enclosure; a zero radius degenerates back to a plain point value."""
    return Inexact(mid - radius, mid + radius) if radius else Fraction(mid.numerator, mid.denominator)


def _quotient(a, ra, b, rb):
    """Enclosure of ``a / b``; a divisor span that contains zero has no reading at all.

    The bounds come from the four corners rather than from the first-order quotient rule
    ``(|a|*rb + |b|*ra) / (b**2 - rb**2)``: that rule is only conservative while the radii are
    small against the values. ``a = [-2, -1]`` and ``b = [-3, -2]`` reach ``a / b = 1`` while
    the first-order rule caps the enclosure at ``14/15``, which would decide a cell the data
    cannot decide.
    """
    if abs(b) <= rb:
        raise DomainError('Division by an enclosure that contains zero')
    corners = [left / right for left in (a - ra, a + ra) for right in (b - rb, b + rb)]
    mid = a / b
    return _carry(mid, max(mid - min(corners), max(corners) - mid))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return object_digest(value)


def exact(value):
    """Parse an exact rational; floats use their shortest decimal spelling.

    A ``{'lo', 'hi'}`` mapping is an interval observation: it parses to the
    ``Inexact`` midpoint of that span, so ``radius`` reaches ``decide`` and the
    measurement's noise is never collapsed into a point value.
    """
    if isinstance(value, bool):
        raise ValueError('Booleans are not numeric measurements')
    if isinstance(value, dict):
        require(set(value) == {'lo', 'hi'}, 'Interval observations need exactly lo and hi')
        lo, hi = exact(value['lo']), exact(value['hi'])
        require(not hasattr(lo, 'radius') and not hasattr(hi, 'radius'),
                'Nested interval observations are not supported')
        require(lo <= hi, 'Interval observation needs lo <= hi')
        interval = Inexact(lo, hi)
        _bounded(interval.radius)
        return _bounded(interval)
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, int):
        result = Fraction(value)
    elif isinstance(value, float):
        require(math.isfinite(value), 'Non-finite measurement')
        result = Fraction(repr(value))
    elif isinstance(value, str) and 0 < len(value) <= MAX_TEXT:
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


def spell(value):
    """Round-trip spelling: an enclosure keeps its bounds, a point keeps its text.

    ``text`` writes only the midpoint of an ``Inexact``, so a ledger written with it
    loses the radius and ``exact`` reads the measurement back as a point value: an
    undecided cell would silently become a decided one. ``exact({'lo', 'hi'})``
    parses these bounds back, so the spelling closes the round trip. A degenerate
    interval is a point and stays a point spelling, never a mapping.
    """
    value = exact(value)
    radius = getattr(value, 'radius', 0)
    if not radius:
        return text(value)
    mid = Fraction(value.numerator, value.denominator)
    return {'lo': text(mid - radius), 'hi': text(mid + radius)}


def _name(value):
    require(isinstance(value, str) and value.isidentifier() and len(value) <= 64
            and not value.startswith('_'), 'Invalid variable name: ' + repr(value))
    return value


def _arity(op):
    return 0 if op in NULLARY else 1 if op in UNARY else 3 if op in TERNARY else 2


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
            require('const' in args[1] and Fraction(args[1]['const']).denominator <= MAX_ROOT
                    and abs(Fraction(args[1]['const'])) <= MAX_POWER, 'pow needs a small rational exponent')
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


def _iroot(n, q):
    """Exact integer q-th root of n >= 0, or None."""
    if n == 0:
        return 0
    x = 1 << -(-n.bit_length() // q)
    while True:
        y = ((q - 1) * x + n // x ** (q - 1)) // q
        if y >= x:
            return x if x ** q == n else None
        x = y


def _root(value, q):
    """Exact real q-th root of a rational, None when it is irrational."""
    if value < 0:
        if q % 2 == 0:
            raise DomainError('Even root of a negative value')
        inner = _root(-value, q)
        return None if inner is None else -inner
    num, den = _iroot(value.numerator, q), _iroot(value.denominator, q)
    return None if num is None or den is None else Fraction(num, den)


def _apply(op, values):
    """Exact value of op on exact arguments; None when the value is irrational (an enclosure follows)."""
    a = values[0]
    if op in TRANSCENDENTAL:
        if op == 'log' and a <= 0:
            raise DomainError('log of a non-positive value')
        special = {('exp', 0): 1, ('log', 1): 0, ('sin', 0): 0, ('cos', 0): 1}.get((op, a))
        return None if special is None else Fraction(special)
    if op == 'neg':
        return -a
    if op == 'abs':
        return abs(a)
    if op == 'floor':
        return Fraction(math.floor(a))
    if op == 'ceil':
        return Fraction(math.ceil(a))
    if op == 'sqrt':
        if a < 0:
            raise DomainError('sqrt of a negative value')
        return _root(a, 2)
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
        if b.denominator == 1:
            return a ** int(b)
        root = _root(a, b.denominator)
        return None if root is None else root ** b.numerator
    if op == 'min':
        return min(a, b)
    if op == 'max':
        return max(a, b)
    return Fraction(int(a < b if op == 'lt' else a <= b if op == 'le' else a == b))


# --- Rigorous enclosures for irrational values --------------------------------

def _dec(value, context):
    return context.divide(Decimal(value.numerator), Decimal(value.denominator))


def _frac(value):
    if value and abs(value.adjusted()) > 150:
        raise DomainError('Exact value exceeds bounded precision')
    return _bounded(Fraction(value))


def _out(lo, hi):
    """Round an enclosure outward to DIGITS significant digits so endpoint sizes stay bounded."""
    return _frac(_dec(lo, _FLOOR)), _frac(_dec(hi, _CEIL))


def _monotone(name, lo, hi):
    """exp/ln/sqrt are correctly rounded by ``decimal``: the true value is within one step."""
    f = getattr(_NEAR, name)
    return _frac(_NEAR.next_minus(f(_dec(lo, _FLOOR)))), _frac(_NEAR.next_plus(f(_dec(hi, _CEIL))))


def _series(r, cosine):
    """Taylor sum of sin or cos at |r| < 1; alternating and decreasing, so the tail is below the last term."""
    term = Decimal(1) if cosine else r
    total, n, r2 = term, 0 if cosine else 1, _WORK.multiply(r, r)
    while abs(term) > Decimal('1e-58'):
        term = _WORK.divide(_WORK.minus(_WORK.multiply(term, r2)), (n + 1) * (n + 2))
        total, n = _WORK.add(total, term), n + 2
    return Fraction(total)


def _trig(lo, hi, cosine):
    """Both are 1-Lipschitz: the value at a reduced centre, widened by every tracked error."""
    mid, radius = (lo + hi) / 2, (hi - lo) / 2
    k = round(mid / (_PI[0] / 2))
    r_lo, r_hi = sorted((mid - k * _PI[0] / 2, mid - k * _PI[1] / 2))
    if r_hi - r_lo > Fraction(1, 4) or radius > 1:
        return Fraction(-1), Fraction(1)
    quadrant = (k + cosine) % 4  # sin(r + k pi/2) cycles sin, cos, -sin, -cos; cos x = sin(x + pi/2).
    centre = (r_lo + r_hi) / 2
    point = _dec(centre, _WORK)
    error = _SERIES_ERROR + abs(Fraction(point) - centre) + (r_hi - r_lo) / 2 + radius
    value = _series(point, quadrant % 2) * (-1 if quadrant >= 2 else 1)
    return max(Fraction(-1), value - error), min(Fraction(1), value + error)


def _power(a0, a1, e):
    """Enclosure of x**e over [a0, a1] for a constant rational exponent."""
    if e.denominator == 1:
        n = abs(int(e))
        ends = sorted((a0 ** n, a1 ** n))
        lo, hi = (Fraction(0), ends[1]) if n % 2 == 0 and a0 < 0 < a1 else tuple(ends)
        return (lo, hi) if e >= 0 else _wide('div', [(Fraction(1), Fraction(1)), (lo, hi)])
    if a0 < 0:
        if a1 >= 0 or e.denominator % 2 == 0:
            if a1 < 0:
                raise DomainError('Even root of a negative value')
            raise Undecided('Power base enclosure reaches a non-positive value')
        lo, hi = _power(-a1, -a0, e)
        return (-hi, -lo) if e.numerator % 2 else (lo, hi)
    if a0 == 0:
        if e < 0:
            raise Undecided('Power base enclosure contains zero')
        return Fraction(0), _power(a1, a1, e)[1]
    l0, l1 = _monotone('ln', a0, a1)
    return _monotone('exp', *sorted((l0 * e, l1 * e)))


def _wide(op, values):
    """Interval semantics of op; raises Undecided where the exact outcome depends on the unknown digits."""
    a0, a1 = values[0]
    if op == 'neg':
        return -a1, -a0
    if op == 'abs':
        return (a0, a1) if a0 >= 0 else (-a1, -a0) if a1 <= 0 else (Fraction(0), max(-a0, a1))
    if op in ('floor', 'ceil'):
        f = math.floor if op == 'floor' else math.ceil
        if f(a0) != f(a1):
            raise Undecided(op + ' enclosure crosses an integer')
        return Fraction(f(a0)), Fraction(f(a0))
    if op in ('sqrt', 'log'):
        if a1 < 0 or (op == 'log' and a1 <= 0):
            raise DomainError(op + ' of a negative value')
        if a0 < 0 or (op == 'log' and a0 <= 0):
            raise Undecided(op + ' argument enclosure reaches zero')
        lo, hi = _monotone('sqrt' if op == 'sqrt' else 'ln', a0, a1)
        return (max(lo, Fraction(0)) if op == 'sqrt' else lo), hi
    if op == 'exp':
        if a1 > 360 or a0 < -360:
            raise DomainError('exp leaves the bounded domain')
        return _monotone('exp', a0, a1)
    if op in ('sin', 'cos'):
        return _trig(a0, a1, op == 'cos')
    b0, b1 = values[1]
    if op in ('add', 'sub'):
        return (a0 + b0, a1 + b1) if op == 'add' else (a0 - b1, a1 - b0)
    if op in ('mul', 'div'):
        if op == 'div' and b0 <= 0 <= b1:
            if b0 == b1:
                raise DomainError('Division by zero')
            raise Undecided('Divisor enclosure contains zero')
        ends = [x * y if op == 'mul' else x / y for x in (a0, a1) for y in (b0, b1)]
        return min(ends), max(ends)
    if op == 'mod':
        q0, q1 = _wide('div', values)
        if math.floor(q0) != math.floor(q1):
            raise Undecided('mod quotient crosses an integer')
        k = Fraction(math.floor(q0))
        return _wide('sub', [values[0], _wide('mul', [values[1], (k, k)])])
    if op == 'pow':
        return _power(a0, a1, b0)
    if op in ('min', 'max'):
        f = min if op == 'min' else max
        return f(a0, b0), f(a1, b1)
    true = a1 < b0 if op == 'lt' else a1 <= b0 if op == 'le' else False
    false = a0 >= b1 if op == 'lt' else a0 > b1 if op == 'le' else a1 < b0 or b1 < a0
    if true == false:
        raise Undecided('Comparison enclosures overlap')
    return Fraction(int(true)), Fraction(int(true))


def _step(op, values):
    """One composition: the exact point when every argument is a point, else its enclosure.

    Lifted out of ``_enclose`` so the bottom-up enumeration composes a cached child span
    with the very same semantics a whole-tree re-evaluation uses.
    """
    if all(lo == hi for lo, hi in values):
        point = _apply(op, [lo for lo, _ in values])
        if point is not None:
            point = _bounded(point)
            return point, point
    lo, hi = _wide(op, values)
    return (_bounded(lo), hi) if lo == hi else _out(lo, hi)


def _enclose(node, env):
    """Rigorous (lo, hi) of a node's value; a point (v, v) whenever the value is exact."""
    if 'const' in node:
        value = Fraction(node['const'])
        return value, value
    if 'var' in node:
        if node['var'] not in env:
            raise DomainError('Missing input: ' + node['var'])
        value = env[node['var']]
        radius = getattr(value, 'radius', 0)
        point = Fraction(value.numerator, value.denominator)  # The bounds themselves are points.
        return point - radius, point + radius
    op, args = node['op'], node['args']
    if op == 'pi':
        return _PI
    if op == 'ite':  # Only the chosen branch must be defined.
        lo, hi = _enclose(args[0], env)
        if lo <= 0 <= hi and lo != hi:
            raise Undecided('Branch test enclosure contains zero')
        return _enclose(args[1 if lo != 0 else 2], env)
    values = [_enclose(arg, env) for arg in args]
    return _step(op, values)


def _evaluate(node, env):
    lo, hi = _enclose(node, env)
    return lo if lo == hi else Inexact(lo, hi)


def evaluate(node, env):
    """Exact evaluation of a normalized AST over an exact input environment (Inexact if irrational)."""
    return _evaluate(node, {_name(k): exact(v) for k, v in env.items()})


def parse(source, variables=None):
    """Compile restricted infix text to the JSON AST; no names or calls leak."""
    if isinstance(source, dict):
        return normalize(source, variables, limit=MAX_RULEBOOK_NODES)
    require(isinstance(source, str) and 0 < len(source) <= 2048, 'Expression text required')
    tree = _pyast.parse(source.strip(), mode='eval')
    ops = {_pyast.Add: 'add', _pyast.Sub: 'sub', _pyast.Mult: 'mul', _pyast.Div: 'div',
           _pyast.Mod: 'mod', _pyast.Pow: 'pow'}
    cmps = {_pyast.Lt: 'lt', _pyast.LtE: 'le', _pyast.Eq: 'eq'}
    calls = {'abs': 1, 'floor': 1, 'ceil': 1, 'sqrt': 1, 'min': 2, 'max': 2, 'ite': 3,
             'exp': 1, 'log': 1, 'sin': 1, 'cos': 1}

    def visit(node):
        if isinstance(node, _pyast.Constant) and type(node.value) in (int, float):
            return {'const': text(node.value)}
        if isinstance(node, _pyast.Name):
            pi = node.id == 'pi' and (variables is None or 'pi' not in variables)
            return {'op': 'pi', 'args': []} if pi else {'var': node.id}
        if isinstance(node, _pyast.UnaryOp) and isinstance(node.op, (_pyast.USub, _pyast.UAdd)):
            inner = visit(node.operand)
            return inner if isinstance(node.op, _pyast.UAdd) else {'op': 'neg', 'args': [inner]}
        if isinstance(node, _pyast.BinOp) and type(node.op) in ops:
            right = visit(node.right)
            if isinstance(node.op, _pyast.Pow) and not variables_of(right) and 'const' not in right:
                folded = _evaluate(normalize(right), {})  # x ** (3/2): a constant exponent, spelled exactly
                right = right if isinstance(folded, Inexact) else {'const': text(folded)}
            return {'op': ops[type(node.op)], 'args': [visit(node.left), right]}
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

    return normalize(visit(tree.body), variables, limit=MAX_RULEBOOK_NODES)


def render(node):
    """Readable infix spelling of a normalized AST (display only)."""
    if 'const' in node:
        return node['const'] if '/' not in node['const'] else '(' + node['const'] + ')'
    if 'var' in node:
        return node['var']
    op, args = node['op'], [render(arg) for arg in node['args']]
    if op == 'pi':
        return 'pi'
    if op == 'neg':
        return '(-' + args[0] + ')'
    if op in _INFIX:
        return '(' + args[0] + ' ' + _INFIX[op] + ' ' + args[1] + ')'
    return op + '(' + ', '.join(args) + ')'


def tolerance(spec):
    spec = spec or {}
    require(isinstance(spec, dict) and set(spec) <= {'abs', 'rel'}, 'Tolerance accepts abs/rel only')
    absolute, relative = exact(spec.get('abs', 0)), exact(spec.get('rel', 0))
    require(not hasattr(absolute, 'radius') and not hasattr(relative, 'radius'),
            'Tolerance is a band width, not a measurement; an interval tolerance has no reading')
    require(absolute >= 0 and relative >= 0, 'Tolerance must be non-negative')
    return absolute, relative


def decide(predicted, observed, tol):
    """True/False when every value in both enclosures is inside/outside the band; None otherwise."""
    absolute, relative = tol
    radius = getattr(predicted, 'radius', 0) + getattr(observed, 'radius', 0)
    gap, spread = abs(predicted - observed), relative * getattr(observed, 'radius', 0)
    if gap + radius <= absolute + relative * abs(observed) - spread:
        return True
    if gap - radius > absolute + relative * abs(observed) + spread:
        return False
    return None


def matches(predicted, observed, tol):
    """Certain agreement only: an enclosure that straddles the band does not match."""
    if not hasattr(predicted, 'radius') and not hasattr(observed, 'radius'):
        absolute, relative = tol
        return abs(predicted - observed) <= absolute + relative * abs(observed)
    return decide(predicted, observed, tol) is True


def agree(left, right, tol):
    """Two predictions agree only if each would survive the other as the measured value.

    ``matches`` scales its relative band by the observed value, so it is directional;
    comparing two predictions needs both directions."""
    return matches(left, right, tol) and matches(right, left, tol)


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
    """Return {observable: Fraction|None}; None marks NOT_APPLICABLE/UNDEFINED/UNDECIDED."""
    try:
        if hypothesis['applies'] is not None:
            guard = evaluate(hypothesis['applies'], env)
            radius = getattr(guard, 'radius', 0)
            if radius and radius >= abs(guard):
                raise Undecided('Applicability enclosure contains zero')
            if guard == 0:
                return {name: None for name in hypothesis['predict']}, 'NOT_APPLICABLE'
        return {name: evaluate(rule, env) for name, rule in hypothesis['predict'].items()}, 'PREDICTED'
    except DomainError:
        return {name: None for name in hypothesis['predict']}, 'UNDEFINED'
    except Undecided:
        return {name: None for name in hypothesis['predict']}, 'UNDECIDED'


def replay(hypothesis, observations, tolerances):
    """Cell-exact replay over original measurements; counterexamples retained.

    UNDEFINED where the rulebook claims applicability counts against it: an
    explanation that cannot produce its own prediction does not explain the
    observation. Missing measurements, and observables with no declared
    tolerance, stay UNKNOWN and refute nothing. So does a cell whose prediction
    enclosure straddles the tolerance band (``undecided``).
    """
    counterexamples, tested, skipped, unknown, undecided = [], 0, 0, 0, 0
    for row in observations:
        values, status = predict(hypothesis, row['inputs'])
        if status == 'NOT_APPLICABLE':
            skipped += 1
            continue
        for name in hypothesis['predict']:
            observed = row['outputs'].get(name)
            if observed is None or name not in tolerances:
                unknown += 1
                continue
            verdict = None if status == 'UNDECIDED' else False if status == 'UNDEFINED' else \
                decide(values[name], observed, tolerances[name])
            if verdict is None:
                undecided += 1
                continue
            tested += 1
            if status == 'UNDEFINED':
                counterexamples.append({'id': row['id'], 'observable': name, 'predicted': None,
                                        'observed': text(observed), 'residual': None})
            elif not verdict:
                counterexamples.append({'id': row['id'], 'observable': name, 'predicted': text(values[name]),
                                        'observed': text(observed), 'residual': text(observed - values[name])})
    status = 'REFUTED' if counterexamples else 'CONSISTENT' if tested else 'UNTESTED'
    result = {'status': status, 'counterexamples': counterexamples, 'tested': tested,
              'not_applicable': skipped, 'unknown_measurements': unknown, 'scientific_support': 'UNKNOWN'}
    return {**result, 'undecided': undecided} if undecided else result


def _classes(predictions, tol):
    """Group exact predictions that agree, in both directions, with every member of their class.

    Agreement is not transitive, so agreeing with the class anchor is not enough (as in verification).
    """
    groups = []
    for ident, value in sorted(predictions, key=lambda row: (row[1], row[0])):
        if groups and all(agree(value, other, tol) for other in groups[-1][2]):
            groups[-1][1].append(ident)
            groups[-1][2].append(value)
        else:
            groups.append((value, [ident], [value]))
    return [(anchor, members) for anchor, members, _ in groups]


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
        if status in ('NOT_APPLICABLE', 'UNDECIDED'):
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


def _coverage(candidates, observed):
    """Each candidate's L1 distance to the nearest observed input, per-input range normalized."""
    observed = [{k: exact(v) for k, v in env.items()} for env in observed]
    points = {item['id']: {k: exact(v) for k, v in item['inputs'].items()} for item in candidates}
    names = sorted({k for point in points.values() for k in point})
    span = {}
    for name in names:
        values = [p[name] for p in list(points.values()) + observed if name in p]
        span[name] = (max(values) - min(values)) or Fraction(1)
    out = {}
    for ident, point in points.items():
        distances = [sum(abs(point[n] - o[n]) / span[n] for n in names if n in o) for o in observed]
        out[ident] = min(distances) if distances else Fraction(0)
    return out


def rank_experiments(hypotheses, candidates, tolerances, *, observed=(), weights=None, limit=8):
    """Rank candidate experiments by expected information gain per unit cost.

    Already-measured inputs are never repurchased; candidates whose partition
    equals a better-ranked one are retained as EQUIVALENT, not ranked twice.
    Equal gain is broken by coverage: the input farthest from every observed
    one, so a tie never concentrates purchases where the evidence already is.
    """
    seen_inputs = {canonical({k: text(v) for k, v in env.items()}) for env in observed}
    coverage = _coverage(candidates, observed)
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
                       'partition_sha256': signature(gain['classes']), 'coverage': text(coverage[item['id']])})
    scored.sort(key=lambda row: (-row['eig_per_cost'], -row['discriminated_pairs'], -exact(row['coverage']),
                                 row['id']))
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
    """Executable equivalence on the available experiment space only.

    As in replay, an observable with no declared tolerance is never compared: no measurement
    could separate two rulebooks on it. With nothing adjudicable, no equivalence is claimed.
    """
    if left['sha256'] == right['sha256']:
        return 'IDENTICAL_EXECUTABLE'
    determinate = 0
    for env in points:
        a, sa = predict(left, env)
        b, sb = predict(right, env)
        # Replay keeps a NOT_APPLICABLE rulebook and refutes an UNDEFINED one on any measurement,
        # so the two statuses separate rulebooks as surely as differing values do.
        if sa != sb:
            return None
        if sa != 'PREDICTED':
            continue
        compared = sorted(k for k in a if k in tolerances)
        if compared != sorted(k for k in b if k in tolerances) or \
                any(not agree(a[k], b[k], tolerances[k]) for k in compared):
            return None
        determinate += bool(compared)
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
            'Synthesis operators must be nullary, unary or binary')
    return sorted(set(names)), constants, sorted(set(operators))


def synthesize(observations, inputs, observable, tolerances, *, grammar=None, max_nodes=7,
               max_candidates=20000, probes=(), base=None, keep=4, derived=None, seeds=()):
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
    derived = derived or {}
    derived_values, derived_nodes = {}, {}
    for derived_name, expression in derived.items():
        _name(derived_name)
        derived_nodes[derived_name] = parse(expression, set(names))
        values = []
        for env in points:
            try:
                values.append(_evaluate(derived_nodes[derived_name], env))
            except (DomainError, Undecided):
                values.append(None)
        derived_values[derived_name] = tuple(values)
    if base is not None:
        offsets = []
        for env in points[:len(rows)]:
            try:
                offsets.append(_evaluate(base, env))
            except (DomainError, Undecided):
                return {'status': 'BASE_UNDEFINED', 'fits': [], 'attempted': 0, 'scientific_support': 'UNKNOWN'}
        targets = [t - o for t, o in zip(targets, offsets)]
    tol = tolerances[observable]
    n = len(rows)
    pools = {s: [] for s in range(1, max_nodes + 1)}
    seen, fits = set(), []
    attempted = 0
    stopped = False

    # points and the derived columns never change during the search: build each extended
    # environment once instead of rebuilding the dictionary for every candidate and point.
    extended_points = [{**env, **{name: values[index] for name, values in derived_values.items()
                                  if values[index] is not None}}
                       for index, env in enumerate(points)]

    def spans_of(node):
        """Per-point enclosures of a whole tree; None where the point is undefined or undecided."""
        out = []
        for env in extended_points:
            try:
                out.append(_enclose(node, env))
            except (DomainError, Undecided):
                out.append(None)
        return tuple(out)

    def values_from(spans):
        return tuple(None if span is None else (span[0] if span[0] == span[1] else Inexact(*span))
                     for span in spans)

    def combine(op, child_spans):
        """Compose cached child spans exactly as a whole-tree enclosure would at each point."""
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

    def const_spans(value):
        point = Fraction(value)  # _enclose reads a const node as Fraction(node['const']).
        return ((point, point),) * len(extended_points)

    def consider(node, spans=None):
        nonlocal attempted, stopped
        if attempted >= max_candidates:
            stopped = True
            return False
        attempted += 1
        try:
            normalized = normalize(node, set(names) | set(derived), limit=max_nodes)
        except (ValueError, DomainError):
            return True
        # A rewrite (or a node with no cached spans) moves the value away from the child
        # vectors the caller combined: only recomputed points are then trustworthy.
        if spans is None or normalized != node:
            spans = spans_of(normalized)
        values = values_from(spans)
        key = tuple(None if v is None else (v.numerator, v.denominator) for v in values)
        if key in seen:
            return True  # Observationally equivalent to a smaller retained expression.
        seen.add(key)
        pools[size(normalized)].append((normalized, spans))
        if all(v is not None and matches(v, t, tol) for v, t in zip(values[:n], targets)):
            if normalized not in fits:
                fits.append(normalized)
        return len(fits) < keep

    def consider_seed(expression):
        """A source-supplied law is judged, never enumerated.

        It never enters ``pools``: the pools are the material the search combines, and
        they are keyed by size up to ``max_nodes``.  A seed is allowed to be larger than
        that — that is the whole reason it is supplied — so it is measured against the
        same targets and the same tolerance as anything the search builds, and then left
        alone.  Anything that survives here can also enter the rulebook, because
        ``compile_hypothesis`` admits ``MAX_RULEBOOK_NODES``.
        """
        node = parse(expression, set(names))
        if size(node) > MAX_RULEBOOK_NODES:
            raise ValueError(f'source law exceeds MAX_RULEBOOK_NODES ({MAX_RULEBOOK_NODES})')
        # The enumeration stores what ``normalize`` produced, so a seed has to be normalized
        # before it is compared or stored. Two sources spelling the same law differently
        # (``x + 1`` and ``x + 0 + 1``) would otherwise occupy two of the ``keep`` slots and
        # stop the search before it starts.
        normalized = normalize(node, set(names) | set(derived), limit=MAX_RULEBOOK_NODES)
        values = values_from(spans_of(normalized))
        if all(v is not None and matches(v, t, tol) for v, t in zip(values[:n], targets)):
            if normalized not in fits:
                fits.append(normalized)

    for expression in seeds:
        consider_seed(expression)
    leaves = [{'var': v} for v in names] + [{'const': c} for c in constants]
    leaves += [{'var': name} for name in sorted(derived)]
    leaves += [{'op': op, 'args': []} for op in operators if op in NULLARY]
    go = len(fits) < keep and all(consider(leaf) for leaf in leaves)
    for total in range(2, max_nodes + 1):
        if not go:
            break
        for op in operators:
            if not go:
                break
            if op in NULLARY:
                continue
            if op in UNARY:
                for child, child_spans in tuple(pools[total - 1]):
                    if not consider({'op': op, 'args': [child]}, combine(op, [child_spans])):
                        go = False
                        break
                continue
            if op == 'pow':
                exps = [c for c in constants if Fraction(c).denominator <= MAX_ROOT and 0 < abs(Fraction(c)) <= MAX_POWER]
                for child, child_spans in tuple(pools.get(total - 2, ())):
                    for e in exps:
                        if not consider({'op': 'pow', 'args': [child, {'const': e}]},
                                        combine('pow', [child_spans, const_spans(e)])):
                            go = False
                            break
                    if not go:
                        break
                continue
            for left_size in range(1, total - 1):
                right_size = total - 1 - left_size
                if op in COMMUTATIVE and left_size > right_size:
                    continue
                for left, left_spans in tuple(pools[left_size]):
                    for right, right_spans in tuple(pools[right_size]):
                        if not consider({'op': op, 'args': [left, right]},
                                        combine(op, [left_spans, right_spans])):
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


def _side_fit(rows, inputs, observable, tolerances, grammar, max_nodes, max_candidates, probes):
    """One measured value on a side is its own constant; fewer points do not fit."""
    values = {exact(r['outputs'][observable]) for r in rows if r['outputs'].get(observable) is not None}
    if len(rows) == 1 and len(values) == 1:
        value = text(values.pop())
        return {'fits': [{'expression': {'const': value}, 'text': value, 'size': 1}], 'attempted': 0}
    return synthesize(rows, inputs, observable, tolerances, grammar=grammar, max_nodes=max_nodes,
                      max_candidates=max_candidates, probes=probes, keep=1)


def regime_split(observations, inputs, observable, tolerances, *, variable, grammar=None,
                 max_nodes=5, max_candidates=4000, max_splits=64, probes=()):
    """Search a two-regime premise shift: one law below a threshold, another above."""
    rows = sorted((r for r in observations if r['outputs'].get(observable) is not None),
                  key=lambda r: exact(r['inputs'][variable]))
    values = sorted({exact(r['inputs'][variable]) for r in rows})
    if len(values) < 4:
        return {'status': 'TOO_FEW_POINTS', 'fits': [], 'attempted': 0, 'mode': 'REGIME_SPLIT',
                'variable': variable, 'scientific_support': 'UNKNOWN'}
    cuts = [(a + b) / 2 for a, b in zip(values, values[1:])]
    if len(cuts) > max_splits:
        # Even coverage including the last boundary: a regime change at the domain edge must
        # remain reachable, not be skipped by a coarse stride.
        cuts = [cuts[round(i * (len(cuts) - 1) / (max_splits - 1))] for i in range(max_splits)]
    fits, attempted = [], 0
    for cut in cuts:
        left = [r for r in rows if exact(r['inputs'][variable]) < cut]
        right = [r for r in rows if exact(r['inputs'][variable]) >= cut]
        lp = [p for p in probes if exact(p[variable]) < cut]
        rp = [p for p in probes if exact(p[variable]) >= cut]
        a = _side_fit(left, inputs, observable, tolerances, grammar, max_nodes, max_candidates, lp)
        b = _side_fit(right, inputs, observable, tolerances, grammar, max_nodes, max_candidates, rp)
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

def sample_profile(baseline, candidate, *, higher_is_better=True, tolerance=(0, 0)):
    """Compare per-sample metrics; report mean gain and the worst degradation.

    Against the metric's declared tolerance the profile is one-sided: a sample's gain counts
    only by what it exceeds the tolerance band around its baseline value, and a degradation
    counts in full. A tolerance can make an arm harder to accept, never easier.
    """
    require(isinstance(baseline, dict) and isinstance(candidate, dict) and baseline
            and set(baseline) == set(candidate), 'Per-sample metrics must cover the same samples')
    sign = 1 if higher_is_better else -1
    absolute, relative = tolerance
    deltas, credited = {}, {}
    for k in sorted(baseline):
        base = exact(baseline[k])
        deltas[k] = sign * (exact(candidate[k]) - base)
        band = absolute + relative * abs(base)
        credited[k] = max(Fraction(0), deltas[k] - band) if deltas[k] > 0 else deltas[k]
    worst_key = min(deltas, key=lambda k: (deltas[k], k))
    mean = sum(credited.values()) / len(credited)
    return {'samples': len(deltas), 'mean_gain': text(mean), 'worst_sample': worst_key,
            'worst_degradation': text(max(Fraction(0), -deltas[worst_key])),
            'degraded': sum(1 for v in deltas.values() if v < 0),
            'improved': sum(1 for v in credited.values() if v > 0),
            'within_tolerance': sum(1 for k in deltas if deltas[k] > 0 and credited[k] == 0)}


def profile_verdict(profile, max_degradation):
    """ACCEPT only when the mean improves and no sample degrades past the bound."""
    if exact(profile['mean_gain']) <= 0:
        return 'NO_MEAN_GAIN'
    if exact(profile['worst_degradation']) > exact(max_degradation):
        return 'LOCAL_REGRESSION'
    return 'ACCEPT'
