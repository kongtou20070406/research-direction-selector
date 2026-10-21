"""Measure the local-dimensional pruning rate on the NewtonBench devset.

Enumerates every AST of size <= 4 straight from each equation's declared grammar
(variables / constants / operators), runs `rds_compass_dimension.dimension_of`
on it, and reports the fraction of forms rejected as locally ill-formed. It
also runs the two decisive checks: the known I.18.4 decoys must die, and every
equation's own truth law must survive.

Size follows `rds_compass_model.size`: a leaf costs 1, an op node costs 1 plus
its subtrees. Nothing here touches synthesize, the ledger or the source files
under test; it only prints and writes one JSON result.
"""
import ast as pyast
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(HERE.parent)]

import devset  # noqa: E402
import rds_compass_model as model  # noqa: E402
import rds_compass_dimension as dim  # noqa: E402

# Result cache: the enumeration is small (6224 trees, well under a second), but
# the file is the single source every reported number is copied from.
OUT = ROOT.parent / 'dimension-measure-results.json'

D = (0, 0, 0, 0, 0, 0, 0)
MASS = (1, 0, 0, 0, 0, 0, 0)
LENGTH = (0, 1, 0, 0, 0, 0, 0)
TIME = (0, 0, 1, 0, 0, 0, 0)
CURRENT = (0, 0, 0, 1, 0, 0, 0)
TEMPERATURE = (0, 0, 0, 0, 1, 0, 0)


def _mul(*vectors):
    out = list(D)
    for vector in vectors:
        out = [a + b for a, b in zip(out, vector)]
    return tuple(out)


def _div(left, right):
    return tuple(a - b for a, b in zip(left, right))


def _pow(vector, exponent):
    return tuple(item * exponent for item in vector)


AREA, VOLUME = _pow(LENGTH, 2), _pow(LENGTH, 3)
DENSITY = _div(MASS, VOLUME)
VELOCITY = _div(LENGTH, TIME)
ACCELERATION = _div(LENGTH, _pow(TIME, 2))
FORCE = _mul(MASS, LENGTH, _pow(TIME, -2))
ENERGY = _mul(FORCE, LENGTH)
POWER = _mul(ENERGY, _pow(TIME, -1))
PRESSURE = _div(FORCE, AREA)
ENERGY_DENSITY = _div(ENERGY, VOLUME)
HEAT_FLUX = _div(POWER, AREA)
NUMBER_DENSITY = _pow(LENGTH, -3)
CHARGE = _mul(CURRENT, TIME)
EFIELD = _div(_mul(MASS, ACCELERATION), _mul(CURRENT, TIME))
VOLTAGE = _mul(EFIELD, LENGTH)
CAPACITANCE = _div(CHARGE, VOLTAGE)
PERMITTIVITY = _div(CAPACITANCE, LENGTH)
BFIELD = _div(FORCE, _mul(CHARGE, VELOCITY))
MAGNETIC_MOMENT = _mul(CURRENT, AREA)
STIFFNESS = _div(FORCE, LENGTH)
YOUNG = _div(FORCE, AREA)
KAPPA = _div(POWER, _mul(LENGTH, TEMPERATURE))
ANGULAR = _pow(TIME, -1)
BOLTZMANN = _div(ENERGY, TEMPERATURE)
HBAR = _mul(ENERGY, TIME)
WAVENUMBER = _pow(LENGTH, -1)
SURFACE_CHARGE = _div(CHARGE, AREA)

# Per-equation variable dimensions. Same name, different meaning across
# equations (`n` is a normal force in I.12.1 and a refractive index in I.27.6;
# `t1`/`t2` are temperatures in II.2.42 and times nowhere here). Each entry is
# the grammar's `variables` plus the base-unit reading of the symbol.
DIMENSIONS = {
    'feynman-I.6.20a': {'theta': D},
    'feynman-I.9.18': {'m1': MASS, 'm2': MASS, 'r': LENGTH},
    'feynman-II.2.42': {'kappa': KAPPA, 't1': TEMPERATURE, 't2': TEMPERATURE, 'd': LENGTH},
    'feynman-I.12.1': {'mu': D, 'n': FORCE},
    'feynman-I.14.3': {'m': MASS, 'g': ACCELERATION, 'h': LENGTH},
    'feynman-I.12.5': {'q2': CHARGE, 'Ef': EFIELD},
    'feynman-II.37.1': {'mom': MAGNETIC_MOMENT, 'chi': D, 'B': BFIELD},
    'feynman-I.14.4': {'k_spring': STIFFNESS, 'x': LENGTH},
    'feynman-I.39.10': {'pr': PRESSURE, 'V': VOLUME},
    'feynman-II.34.2': {'q': CHARGE, 'v': VELOCITY, 'r': LENGTH},
    'feynman-II.27.18': {'eps0': PERMITTIVITY, 'Ef': EFIELD},
    'feynman-I.25.13': {'q': CHARGE, 'C': CAPACITANCE},
    'feynman-II.38.14': {'Y': YOUNG, 'sigma': D},
    'feynman-II.10.9': {'sigma_den': SURFACE_CHARGE, 'eps0': PERMITTIVITY, 'chi': D},
    'feynman-I.18.4': {'m1': MASS, 'r1': LENGTH, 'm2': MASS, 'r2': LENGTH},
    'feynman-I.27.6': {'d1': LENGTH, 'n': D, 'd2': LENGTH},
    'feynman-II.34.11': {'g_factor': D, 'q': CHARGE, 'B': BFIELD, 'm': MASS},
    'feynman-II.38.3': {'Y': YOUNG, 'A': AREA, 'x': LENGTH, 'd': LENGTH},
    'feynman-I.8.14': {'x1': LENGTH, 'y1': LENGTH, 'x2': LENGTH, 'y2': LENGTH},
    'feynman-I.10.7': {'m_0': MASS, 'v': VELOCITY},
    'feynman-I.47.23': {'gamma': D, 'pr': PRESSURE, 'rho': DENSITY},
    'feynman-III.10.19': {'mom': MAGNETIC_MOMENT, 'Bx': BFIELD, 'By': BFIELD, 'Bz': BFIELD},
    'feynman-II.24.17': {'omega': ANGULAR, 'd': LENGTH},
    'feynman-I.18.12': {'r': LENGTH, 'F': FORCE, 'theta': D},
    'feynman-I.40.1': {'n_0': NUMBER_DENSITY, 'm': MASS, 'x': LENGTH, 'T': TEMPERATURE},
    'feynman-I.41.16': {'omega': ANGULAR, 'T': TEMPERATURE},
    'feynman-I.26.2': {'theta1': D, 'theta2': D},
    'srsd-hard-feynman-I.9.18': {'m1': MASS, 'm2': MASS, 'r': LENGTH, 'zeta': D},
    'srsd-hard-feynman-I.14.3': {'m': MASS, 'g': ACCELERATION, 'h': LENGTH, 'phi': D},
    'srsd-hard-feynman-II.2.42': {'kappa': KAPPA, 't1': TEMPERATURE, 't2': TEMPERATURE,
                                  'd': LENGTH, 'psi': D, 'eta': D},
}

# Physical constants the truth *expression* spells as symbols but the grammar
# does not declare as inputs. Declared so validation B judges the physics, not
# a missing binding; every one is listed as an absorbed constant in the report.
IMPLICIT = {
    'feynman-I.10.7': {'c': VELOCITY},
    'feynman-II.24.17': {'c': VELOCITY},
    'feynman-I.40.1': {'g': ACCELERATION, 'k': BOLTZMANN},
    'feynman-I.41.16': {'hbar': HBAR, 'c': VELOCITY, 'k': BOLTZMANN},
}

# Dimension of the observed quantity `y`, read from the physics of each law.
OUTPUT = {
    'feynman-I.6.20a': D,
    'feynman-I.9.18': FORCE,
    'feynman-II.2.42': HEAT_FLUX,
    'feynman-I.12.1': FORCE,
    'feynman-I.14.3': ENERGY,
    'feynman-I.12.5': FORCE,
    'feynman-II.37.1': ENERGY,
    'feynman-I.14.4': ENERGY,
    'feynman-I.39.10': ENERGY,
    'feynman-II.34.2': MAGNETIC_MOMENT,
    'feynman-II.27.18': ENERGY_DENSITY,
    'feynman-I.25.13': VOLTAGE,
    'feynman-II.38.14': YOUNG,
    'feynman-II.10.9': EFIELD,
    'feynman-I.18.4': LENGTH,
    'feynman-I.27.6': LENGTH,
    'feynman-II.34.11': ANGULAR,
    'feynman-II.38.3': FORCE,
    'feynman-I.8.14': LENGTH,
    'feynman-I.10.7': MASS,
    'feynman-I.47.23': VELOCITY,
    'feynman-III.10.19': ENERGY,
    'feynman-II.24.17': WAVENUMBER,
    'feynman-I.18.12': ENERGY,
    'feynman-I.40.1': NUMBER_DENSITY,
    # devset's docstring calls this the Planck *spectral* density, i.e. energy
    # per volume per hertz = J*s/m^3, so the extra TIME factor is part of y.
    'feynman-I.41.16': _mul(ENERGY_DENSITY, TIME),
    'feynman-I.26.2': D,
    'srsd-hard-feynman-I.9.18': FORCE,
    'srsd-hard-feynman-I.14.3': ENERGY,
    'srsd-hard-feynman-II.2.42': HEAT_FLUX,
}

DECOYS = [
    '(m1 + r2) / (2 + m1)',
    '(m1 + r2) / (1 + m2)',
    '(m1 + r2) / (2 + r2)',
]
TRUTH_I184 = '(m1 * r1 + m2 * r2) / (m1 + m2)'
MAX_SIZE = 4


def dims_of(name):
    return {**DIMENSIONS[name], **IMPLICIT.get(name, {})}


def pools_of(grammar, max_size):
    """Every AST per size, built straight from the declared grammar."""
    variables = sorted(set(grammar['variables']))
    constants = sorted(set(grammar['constants']))
    operators = sorted(set(grammar['operators']))
    leaves = [{'var': v} for v in variables] + [{'const': c} for c in constants]
    if 'pi' in operators:
        leaves.append({'op': 'pi', 'args': []})
    pools = {1: leaves}
    for total in range(2, max_size + 1):
        bucket = []
        for op in operators:
            if op == 'pi' or op not in model.OPERATORS or op in model.TERNARY:
                continue
            if op in model.UNARY:
                bucket += [{'op': op, 'args': [child]} for child in pools.get(total - 1, ())]
                continue
            for left in range(1, total - 1):
                right = total - 1 - left
                for a in pools.get(left, ()):
                    for b in pools.get(right, ()):
                        bucket.append({'op': op, 'args': [a, b]})
        pools[total] = bucket
    for size, trees in pools.items():
        assert all(model.size(tree) == size for tree in trees), 'size disagrees with model.size'
    return pools


def rate(total, legal):
    return None if not total else round(1 - legal / total, 6)


_BINOP = {pyast.Add: 'add', pyast.Sub: 'sub', pyast.Mult: 'mul',
          pyast.Div: 'div', pyast.Mod: 'mod', pyast.Pow: 'pow'}


def to_model(text):
    """Compile the devset spelling of a truth law into the Compass AST dialect.

    `rds_compass_model.parse` reads infix text, but the devset expressions spell
    powers as ``pow(a, b)`` and negation as ``neg(x)`` and its call whitelist
    carries neither. This walks the Python AST instead and emits the very nodes
    `parse` would emit for ``a ** b`` and ``-x``: same dialect, same size, same
    dimension.
    """
    def visit(node):
        if isinstance(node, pyast.Expression):
            return visit(node.body)
        if isinstance(node, pyast.Constant) and type(node.value) in (int, float):
            return {'const': model.text(node.value)}
        if isinstance(node, pyast.Name):
            return {'op': 'pi', 'args': []} if node.id == 'pi' else {'var': node.id}
        if isinstance(node, pyast.UnaryOp) and isinstance(node.op, (pyast.USub, pyast.UAdd)):
            inner = visit(node.operand)
            return inner if isinstance(node.op, pyast.UAdd) else {'op': 'neg', 'args': [inner]}
        if isinstance(node, pyast.BinOp) and type(node.op) in _BINOP:
            return {'op': _BINOP[type(node.op)], 'args': [visit(node.left), visit(node.right)]}
        if (isinstance(node, pyast.Call) and isinstance(node.func, pyast.Name) and not node.keywords):
            return {'op': node.func.id, 'args': [visit(arg) for arg in node.args]}
        raise ValueError('Unsupported truth syntax: ' + type(node).__name__)

    return visit(pyast.parse(text.strip(), mode='eval'))


def main():
    if '--from-cache' in sys.argv and OUT.exists():
        cached = json.loads(OUT.read_text(encoding='utf-8'))
        _tables(cached)
        print('\nfrom cache:', OUT)
        return
    report = {'source': str(HERE), 'max_size': MAX_SIZE, 'equations': [], 'validation_a': {},
              'validation_b': []}
    for name in devset.list_equations():
        spec = devset.load(name)
        dims = dims_of(name)
        missing = sorted(set(spec['grammar']['variables']) - set(dims))
        assert not missing, f'{name}: unannotated variables {missing}'
        pools = pools_of(spec['grammar'], MAX_SIZE)
        sizes = {}
        for size in sorted(pools):
            trees = pools[size]
            legal = sum(1 for tree in trees if dim.dimension_of(tree, dims) is not None)
            sizes[str(size)] = {'total': len(trees), 'legal': legal,
                                'pruned_rate': rate(len(trees), legal)}
        entry = {'name': name,
                 'grammar': spec['grammar'],
                 'dimensions': {var: list(dims[var]) for var in spec['grammar']['variables']},
                 'implicit': {k: list(v) for k, v in IMPLICIT.get(name, {}).items()},
                 'output_dimension': list(OUTPUT[name]),
                 'sizes': sizes}
        report['equations'].append(entry)

        truth = to_model(spec["truth"]["expression"])
        truth_dim = dim.dimension_of(truth, dims)
        entry['truth_expression'] = spec['truth']['expression']
        entry['truth_dimension'] = None if truth_dim is None else list(truth_dim)
        entry['truth_local_legal'] = truth_dim is not None
        entry['truth_global_balanced'] = truth_dim == OUTPUT[name]
        report['validation_b'].append({'name': name, 'pass': truth_dim is not None,
                                       'dimension': None if truth_dim is None else list(truth_dim),
                                       'expected_output': list(OUTPUT[name]),
                                       'balanced': truth_dim == OUTPUT[name],
                                       'expression': spec['truth']['expression']})
        print(f'{name:32s} truth={"LEGAL" if truth_dim is not None else "ILLEGAL":7s} '
              f'dim={truth_dim} y={OUTPUT[name]} balanced={truth_dim == OUTPUT[name]}')

    i184 = dims_of('feynman-I.18.4')
    decoy = []
    for text in DECOYS:
        tree = to_model(text)
        decoy.append({'expression': text, 'dimension': dim.dimension_of(tree, i184),
                      'pruned': dim.dimension_of(tree, i184) is None})
    truth = to_model(TRUTH_I184)
    report['validation_a'] = {'equation': 'feynman-I.18.4', 'decoys': decoy,
                              'truth': {'expression': TRUTH_I184,
                                        'expanded': '(m1*r1 + m2*r2)/(m1+m2)',
                                        'dimension': list(dim.dimension_of(truth, i184)),
                                        'pruned': dim.dimension_of(truth, i184) is None}}

    # Exhaustive version of validation A: every enumerated I.18.4 form holding an
    # ill-typed add/sub must be rejected, and no well-typed form may be rejected.
    pools = pools_of(devset.load('feynman-I.18.4')['grammar'], MAX_SIZE)
    bad = [t for group in pools.values() for t in group if _ill_typed(t, i184)]
    report['validation_a']['enumerated'] = {
        'ill_typed_trees': len(bad),
        'ill_typed_pruned': sum(1 for t in bad if dim.dimension_of(t, i184) is None),
        'well_typed_trees': sum(len(g) for g in pools.values()) - len(bad),
        'well_typed_survived': sum(1 for g in pools.values() for t in g
                                   if not _ill_typed(t, i184) and dim.dimension_of(t, i184) is not None)}

    summary, pooled = {}, {}
    for size in map(str, range(1, MAX_SIZE + 1)):
        rows = [(e['name'], e['sizes'][size]['pruned_rate']) for e in report['equations']
                if e['sizes'][size]['pruned_rate'] is not None]
        total = sum(e['sizes'][size]['total'] for e in report['equations'])
        legal = sum(e['sizes'][size]['legal'] for e in report['equations'])
        pooled[size] = {'trees': total, 'legal': legal, 'pruned_rate': rate(total, legal)}
        if not rows:
            continue
        values = sorted(v for _, v in rows)
        summary[size] = {'equations_measured': len(values),
                         'equations_with_trees': sum(1 for e in report['equations']
                                                     if e['sizes'][size]['total']),
                         'mean': round(sum(values) / len(values), 6),
                         'median': values[len(values) // 2] if len(values) % 2 else
                         round((values[len(values) // 2 - 1] + values[len(values) // 2]) / 2, 6),
                         'best': {'equation': max(rows, key=lambda r: r[1])[0], 'rate': values[-1]},
                         'worst': {'equation': min(rows, key=lambda r: r[1])[0], 'rate': values[0]}}
    report['summary'] = summary
    report['pooled'] = pooled
    grand_total = sum(v['trees'] for v in pooled.values())
    grand_legal = sum(v['legal'] for v in pooled.values())
    report['pooled_all_sizes'] = {'trees': grand_total, 'legal': grand_legal,
                                  'pruned_rate': rate(grand_total, grand_legal)}

    out = OUT
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    _tables(report)
    print('\nwrote', out)


def _pct(value):
    return 'n/a' if value is None else f'{100 * value:.2f}%'


def _tables(report):
    """Markdown tables, so every number in the report comes from one run."""
    print('\n## pruning by equation\n')
    print('| equation | size1 | size2 | size3 | size4 |')
    print('| --- | --- | --- | --- | --- |')
    for entry in report['equations']:
        cells = [_pct(entry['sizes'][s]['pruned_rate']) for s in ('1', '2', '3', '4')]
        print(f"| `{entry['name']}` | " + ' | '.join(cells) + ' |')
    print('\n## tree counts by equation\n')
    print('| equation | size1 | size2 | size3 | size4 |')
    print('| --- | --- | --- | --- | --- |')
    for entry in report['equations']:
        cells = [f"{entry['sizes'][s]['total']}/{entry['sizes'][s]['legal']}"
                 for s in ('1', '2', '3', '4')]
        print(f"| `{entry['name']}` | " + ' | '.join(cells) + ' |')
    print('\n## summary\n')
    print('| size | equations with trees | mean | median | best | worst |')
    print('| --- | --- | --- | --- | --- | --- |')
    for size, row in report['summary'].items():
        print(f"| {size} | {row['equations_with_trees']}/{len(report['equations'])} | "
              f"{_pct(row['mean'])} | {_pct(row['median'])} | "
              f"`{row['best']['equation']}` {_pct(row['best']['rate'])} | "
              f"`{row['worst']['equation']}` {_pct(row['worst']['rate'])} |")
    print('\n## pooled over all 30 equations\n')
    print('| size | trees | legal | pruned |')
    print('| --- | --- | --- | --- |')
    for size, row in report['pooled'].items():
        print(f"| {size} | {row['trees']} | {row['legal']} | {_pct(row['pruned_rate'])} |")
    row = report['pooled_all_sizes']
    print(f"| 1-4 | {row['trees']} | {row['legal']} | {_pct(row['pruned_rate'])} |")


def _ill_typed(tree, dims):
    """True when an add/sub sits on two children of different dimension."""
    if 'op' in tree and tree['op'] in ('add', 'sub'):
        left, right = (dim.dimension_of(a, dims) for a in tree['args'])
        if left is None or right is None or left != right:
            return True
    return any(_ill_typed(arg, dims) for arg in tree.get('args', ()))


if __name__ == '__main__':
    main()
