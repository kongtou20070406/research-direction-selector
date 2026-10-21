"""NewtonBench development set loader for RDS Compass benchmark evaluations.

Data Sources:
- AI Feynman Database for Symbolic Regression (Udrescu & Tegmark, Science Advances 2020 / arXiv:1905.11481)
- The Feynman Lectures on Physics (Caltech / Feynman, Leighton, Sands)
- Offline & deterministic: Built-in minimal equations with exact rational / bounded decimal points.
"""
from fractions import Fraction
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts')]
import rds_compass_model as model

def _to_text(val):
    if isinstance(val, (int, Fraction)):
        return model.text(val)
    return model.text(repr(float(val)))

def _row(idx, inputs, y):
    """One declared devset row; inputs keep their declaration order."""
    return {'id': f'pt_{idx}',
            'inputs': {k: _to_text(v) for k, v in inputs.items()},
            'outputs': {'y': _to_text(y)}}

def _product(names, columns):
    """Cartesian product of per-variable value lists, in declaration order."""
    combos = [{}]
    for name, values in zip(names, columns):
        combos = [{**combo, name: value} for combo in combos for value in values]
    return combos

def _exact_spec(rows, variables, constants, operators, expression, law):
    """Pure-algebra spec: Fraction rows, so the tolerance is exact equality."""
    return {
        'rows': rows,
        'tolerances': {'y': {'abs': 0, 'rel': 0}},
        'grammar': {'variables': variables, 'constants': constants, 'operators': operators},
        'truth': {'observable': 'y', 'expression': expression, 'law': law},
    }

def _float_spec(rows, variables, constants, operators, expression, law):
    """Transcendental spec: math floats, so the tolerance is the 1/1000 band."""
    return {
        'rows': rows,
        'tolerances': {'y': {'abs': '1/1000', 'rel': '1/1000'}},
        'grammar': {'variables': variables, 'constants': constants, 'operators': operators},
        'truth': {'observable': 'y', 'expression': expression, 'law': law},
    }

def _gen_feynman_I_6_20a():
    """Feynman I.6.20a: Normal distribution f = exp(-theta**2 / 2) / sqrt(2*pi)."""
    rows = []
    # 25 points from -3 to 3 with step 1/4
    for i, idx in enumerate(range(-12, 13)):
        th = Fraction(idx, 4)
        th_f = float(th)
        y = math.exp(-0.5 * (th_f ** 2)) / math.sqrt(2.0 * math.pi)
        rows.append({
            'id': f'pt_{i}',
            'inputs': {'theta': _to_text(th)},
            'outputs': {'y': _to_text(y)}
        })
    return {
        'rows': rows,
        'tolerances': {'y': {'abs': '1/1000', 'rel': '1/1000'}},
        'grammar': {
            'variables': ['theta'],
            'constants': ['1', '2'],
            'operators': ['neg', 'sub', 'div', 'pow', 'exp', 'sqrt', 'pi']
        },
        'truth': {
            'observable': 'y',
            'expression': 'exp(neg(pow(theta, 2) / 2)) / sqrt(2 * pi)',
            'law': 'exp(-theta**2/2)/sqrt(2*pi)'
        }
    }

def _gen_feynman_I_9_18():
    """Feynman I.9.18: Gravitational force magnitude F = (m1 * m2) / (r**2) [G=1]."""
    rows = []
    m1_vals = [Fraction(1), Fraction(2), Fraction(3)]
    m2_vals = [Fraction(1), Fraction(2), Fraction(4)]
    r_vals = [Fraction(1), Fraction(2), Fraction(3), Fraction(5)]
    idx = 0
    for m1 in m1_vals:
        for m2 in m2_vals:
            for r in r_vals:
                f_val = (m1 * m2) / (r ** 2)
                rows.append({
                    'id': f'pt_{idx}',
                    'inputs': {'m1': _to_text(m1), 'm2': _to_text(m2), 'r': _to_text(r)},
                    'outputs': {'y': _to_text(f_val)}
                })
                idx += 1
    return {
        'rows': rows,
        'tolerances': {'y': {'abs': 0, 'rel': 0}},
        'grammar': {
            'variables': ['m1', 'm2', 'r'],
            'constants': ['1', '2'],
            'operators': ['mul', 'div', 'pow']
        },
        'truth': {
            'observable': 'y',
            'expression': '(m1 * m2) / pow(r, 2)',
            'law': 'm1*m2/(r**2)'
        }
    }

def _gen_feynman_II_2_42():
    """Feynman II.2.42: Heat conduction flux P = kappa * (T2 - T1) / d."""
    rows = []
    kappas = [Fraction(1), Fraction(2)]
    t1_vals = [Fraction(10), Fraction(20)]
    t2_vals = [Fraction(30), Fraction(50)]
    d_vals = [Fraction(1), Fraction(2), Fraction(4), Fraction(8)]
    idx = 0
    for k in kappas:
        for t1 in t1_vals:
            for t2 in t2_vals:
                for d in d_vals:
                    p_val = k * (t2 - t1) / d
                    rows.append({
                        'id': f'pt_{idx}',
                        'inputs': {'kappa': _to_text(k), 't1': _to_text(t1), 't2': _to_text(t2), 'd': _to_text(d)},
                        'outputs': {'y': _to_text(p_val)}
                    })
                    idx += 1
    return {
        'rows': rows,
        'tolerances': {'y': {'abs': 0, 'rel': 0}},
        'grammar': {
            'variables': ['kappa', 't1', 't2', 'd'],
            'constants': ['0', '1', '2'],
            'operators': ['sub', 'mul', 'div']
        },
        'truth': {
            'observable': 'y',
            'expression': 'kappa * (t2 - t1) / d',
            'law': 'kappa*(t2-t1)/d'
        }
    }

def _gen_feynman_I_12_1():
    """Feynman I.12.1: Dry friction force F = mu * N."""
    rows = []
    mus = [Fraction(1, 10), Fraction(1, 5), Fraction(1, 2), Fraction(4, 5), Fraction(1, 1)]
    ns = [Fraction(10), Fraction(25), Fraction(50), Fraction(100), Fraction(200)]
    idx = 0
    for mu in mus:
        for n in ns:
            f_val = mu * n
            rows.append({
                'id': f'pt_{idx}',
                'inputs': {'mu': _to_text(mu), 'n': _to_text(n)},
                'outputs': {'y': _to_text(f_val)}
            })
            idx += 1
    return {
        'rows': rows,
        'tolerances': {'y': {'abs': 0, 'rel': 0}},
        'grammar': {
            'variables': ['mu', 'n'],
            'constants': ['0', '1'],
            'operators': ['mul']
        },
        'truth': {
            'observable': 'y',
            'expression': 'mu * n',
            'law': 'mu*n'
        }
    }

def _gen_feynman_I_14_3():
    """Feynman I.14.3: Gravitational potential energy U = m * g * h."""
    rows = []
    ms = [Fraction(1), Fraction(2), Fraction(5)]
    gs = [Fraction(98, 10), Fraction(10), Fraction(1)]
    hs = [Fraction(1), Fraction(5), Fraction(10)]
    idx = 0
    for m in ms:
        for g in gs:
            for h in hs:
                u_val = m * g * h
                rows.append({
                    'id': f'pt_{idx}',
                    'inputs': {'m': _to_text(m), 'g': _to_text(g), 'h': _to_text(h)},
                    'outputs': {'y': _to_text(u_val)}
                })
                idx += 1
    return {
        'rows': rows,
        'tolerances': {'y': {'abs': 0, 'rel': 0}},
        'grammar': {
            'variables': ['m', 'g', 'h'],
            'constants': ['1', '2'],
            'operators': ['mul']
        },
        'truth': {
            'observable': 'y',
            'expression': 'm * g * h',
            'law': 'm*g*h'
        }
    }

# ---------------------------------------------------------------------------
# L1: plain algebra (no sqrt / exp / sin / cos / log / pi). Only + - * / by a
# plain constant, and integer powers, so every row stays an exact Fraction.
# Source of every formula: Feynman Lectures eq. number, cross-checked against
# SRSD's dataset (`datasets/feynman.py`, omron-sinicx/srsd-benchmark) which is
# the AI Feynman / FSReD set of Udrescu & Tegmark 2020 (arXiv:1905.11481).
# ---------------------------------------------------------------------------

def _gen_feynman_I_12_5():
    """Feynman I.12.5: force on a charge in an electric field, F = q2 * Ef.

    Raw from the AI Feynman set: `q2 * Ef` (Vars: q2, Ef).
    """
    names = ['q2', 'Ef']
    columns = [[Fraction(-3), Fraction(-1), Fraction(1), Fraction(2), Fraction(5)],
               [Fraction(1, 2), Fraction(1), Fraction(2), Fraction(5)]]
    rows = [_row(i, c, c['q2'] * c['Ef'])
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1'], ['mul'], 'q2 * Ef', 'q2*Ef')

def _gen_feynman_II_37_1():
    """Feynman II.37.1: energy of a magnetic moment, U = mom * (1 + chi) * B.

    Raw from the AI Feynman set: `mom * (1 + chi) * B` (Vars: mom, chi, B).
    """
    names = ['mom', 'chi', 'B']
    columns = [[Fraction(1), Fraction(2), Fraction(4)],
               [Fraction(1, 10), Fraction(1, 2), Fraction(1)],
               [Fraction(1), Fraction(2), Fraction(5)]]
    rows = [_row(i, c, c['mom'] * (1 + c['chi']) * c['B'])
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['add', 'mul'],
                       'mom * (1 + chi) * B', 'mom*(1+chi)*B')

def _gen_feynman_I_14_4():
    """Feynman I.14.4: elastic potential energy, U = k_spring * x**2 / 2.

    Raw from the AI Feynman set: `1 / 2 * k_spring * x ** 2`.
    """
    names = ['k_spring', 'x']
    columns = [[Fraction(1), Fraction(2), Fraction(4), Fraction(10)],
               [Fraction(1, 2), Fraction(1), Fraction(2), Fraction(3)]]
    rows = [_row(i, c, c['k_spring'] * c['x'] ** 2 / 2)
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['div', 'mul', 'pow'],
                       'k_spring * pow(x, 2) / 2', 'k_spring*x**2/2')

def _gen_feynman_I_39_10():
    """Feynman I.39.10: energy of a monatomic gas, U = pr * V * 3 / 2.

    Raw from the AI Feynman set: `3 / 2 * pr * V` (Vars: pr, V).
    """
    names = ['pr', 'V']
    columns = [[Fraction(1), Fraction(2), Fraction(3), Fraction(5)],
               [Fraction(1), Fraction(2), Fraction(4), Fraction(10)]]
    rows = [_row(i, c, c['pr'] * c['V'] * 3 / 2)
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2', '3'], ['div', 'mul'],
                       'pr * V * 3 / 2', 'pr*V*3/2')

def _gen_feynman_II_34_2():
    """Feynman II.34.2: magnetic moment of a current loop, mom = q * v * r / 2.

    Raw from the AI Feynman set: `q * v * r / 2` (Vars: q, v, r).
    """
    names = ['q', 'v', 'r']
    columns = [[Fraction(1), Fraction(2), Fraction(-1)],
               [Fraction(1, 2), Fraction(1), Fraction(3)],
               [Fraction(1), Fraction(2), Fraction(4)]]
    rows = [_row(i, c, c['q'] * c['v'] * c['r'] / 2)
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['div', 'mul'],
                       'q * v * r / 2', 'q*v*r/2')

def _gen_feynman_II_27_18():
    """Feynman II.27.18: electric field energy density, u = eps0 * Ef**2.

    Raw from the AI Feynman set: `8.854e-12 * Ef ** 2`; the permittivity is
    declared as the input `eps0` here so the rows stay exact rationals.
    """
    names = ['eps0', 'Ef']
    columns = [[Fraction(8854, 10 ** 15), Fraction(17708, 10 ** 15)],
               [Fraction(1000), Fraction(2000), Fraction(3000), Fraction(5000)]]
    rows = [_row(i, c, c['eps0'] * c['Ef'] ** 2)
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['mul', 'pow'],
                       'eps0 * pow(Ef, 2)', 'eps0*Ef**2')

# ---------------------------------------------------------------------------
# L2: rational fractions (a variable in the denominator, or a negative power).
# Still exact: Fraction in, Fraction out, so the tolerance stays 0/0.
# ---------------------------------------------------------------------------

def _gen_feynman_I_25_13():
    """Feynman I.25.13: voltage across a capacitor, V = q / C.

    Raw from the AI Feynman set: `q / C` (Vars: q, C).
    """
    names = ['q', 'C']
    columns = [[Fraction(1), Fraction(2), Fraction(5), Fraction(-3)],
               [Fraction(1), Fraction(2), Fraction(5), Fraction(10)]]
    rows = [_row(i, c, c['q'] / c['C'])
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1'], ['div'], 'q / C', 'q/C')

def _gen_feynman_II_38_14():
    """Feynman II.38.14: shear modulus, G = Y / (2 * (1 + sigma)).

    Raw from the AI Feynman set: `Y / (2 * (1 + sigma))` (Vars: Y, sigma).
    """
    names = ['Y', 'sigma']
    columns = [[Fraction(10), Fraction(20), Fraction(50)],
               [Fraction(1, 10), Fraction(1, 4), Fraction(1, 2)]]
    rows = [_row(i, c, c['Y'] / (2 * (1 + c['sigma'])))
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['add', 'div', 'mul'],
                       'Y / (2 * (1 + sigma))', 'Y/(2*(1+sigma))')

def _gen_feynman_II_10_9():
    """Feynman II.10.9: E field in a dielectric, E = sigma_den / (eps0 * (1 + chi)).

    Raw from the AI Feynman set: `sigma_den / 8.854e-12 * 1 / (1 + chi)`;
    the permittivity is declared as the input `eps0` here to keep rows exact.
    """
    names = ['sigma_den', 'eps0', 'chi']
    columns = [[Fraction(1, 100), Fraction(2, 100), Fraction(5, 100)],
               [Fraction(8854, 10 ** 15), Fraction(17708, 10 ** 15)],
               [Fraction(1), Fraction(2), Fraction(4)]]
    rows = [_row(i, c, c['sigma_den'] / (c['eps0'] * (1 + c['chi'])))
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['add', 'div', 'mul'],
                       'sigma_den / (eps0 * (1 + chi))',
                       'sigma_den/(eps0*(1+chi))')

def _gen_feynman_I_18_4():
    """Feynman I.18.4: centre of mass, r = (m1*r1 + m2*r2) / (m1 + m2).

    Raw from the AI Feynman set: `(m1 * r1 + m2 * r2) / (m1 + m2)`.
    """
    names = ['m1', 'r1', 'm2', 'r2']
    # 3 x 3 x 3 x 2 = 54 rows: a campaign declares at most MAX_CANDIDATES (64) candidates.
    columns = [[Fraction(1), Fraction(2), Fraction(4)],
               [Fraction(-2), Fraction(1), Fraction(3)],
               [Fraction(1), Fraction(3), Fraction(5)],
               [Fraction(-1), Fraction(4)]]
    rows = [_row(i, c, (c['m1'] * c['r1'] + c['m2'] * c['r2']) / (c['m1'] + c['m2']))
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['add', 'div', 'mul'],
                       '(m1 * r1 + m2 * r2) / (m1 + m2)',
                       '(m1*r1+m2*r2)/(m1+m2)')

def _gen_feynman_I_27_6():
    """Feynman I.27.6: thin lens, 1/f = 1/d1 + n/d2 hence f = 1 / (1/d1 + n/d2).

    Raw from the AI Feynman set: `1 / (1 / d1 + n / d2)` (Vars: d1, n, d2).
    """
    names = ['d1', 'n', 'd2']
    columns = [[Fraction(1, 2), Fraction(1), Fraction(2)],
               [Fraction(1), Fraction(3, 2), Fraction(2)],
               [Fraction(1, 2), Fraction(1), Fraction(2)]]
    rows = [_row(i, c, 1 / (1 / c['d1'] + c['n'] / c['d2']))
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['add', 'div'],
                       '1 / (1 / d1 + n / d2)', '1/(1/d1+n/d2)')

def _gen_feynman_II_34_11():
    """Feynman II.34.11: Larmor frequency, omega = g_factor * q * B / (2 * m).

    Raw from the AI Feynman set: `g_ * q * B / (2 * m)` (Vars: g_, q, B, m).
    """
    names = ['g_factor', 'q', 'B', 'm']
    columns = [[Fraction(1), Fraction(2)],
               [Fraction(1), Fraction(-1), Fraction(2)],
               [Fraction(1), Fraction(2), Fraction(4)],
               [Fraction(1), Fraction(2), Fraction(5)]]
    rows = [_row(i, c, c['g_factor'] * c['q'] * c['B'] / (2 * c['m']))
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['div', 'mul'],
                       'g_factor * q * B / (2 * m)', 'g_factor*q*B/(2*m)')

def _gen_feynman_II_38_3():
    """Feynman II.38.3: Hooke's law for a stretched bar, F = Y * A * x / d.

    Raw from the AI Feynman set: `Y * A * x / d` (Vars: Y, A, x, d).
    """
    names = ['Y', 'A', 'x', 'd']
    columns = [[Fraction(1), Fraction(2), Fraction(5)],
               [Fraction(1), Fraction(3), Fraction(6)],
               [Fraction(0), Fraction(1), Fraction(2)],
               [Fraction(1), Fraction(4)]]
    rows = [_row(i, c, c['Y'] * c['A'] * c['x'] / c['d'])
            for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['0', '1'], ['div', 'mul'],
                       'Y * A * x / d', 'Y*A*x/d')

# ---------------------------------------------------------------------------
# L3: square roots and geometry. sqrt is evaluated in floating point, so these
# specs carry the 1/1000 band; input boxes are chosen so the radicand stays
# positive over the whole declared box (devset_run samples uniformly in it).
# ---------------------------------------------------------------------------

def _gen_feynman_I_8_14():
    """Feynman I.8.14: Euclidean distance, d = sqrt((x2-x1)**2 + (y2-y1)**2).

    Raw from the AI Feynman set: `sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)`.
    """
    names = ['x1', 'y1', 'x2', 'y2']
    columns = [[Fraction(0), Fraction(1), Fraction(3)],
               [Fraction(0), Fraction(2)],
               [Fraction(1), Fraction(2), Fraction(5)],
               [Fraction(1), Fraction(4)]]
    rows = [_row(i, c, math.sqrt((c['x2'] - c['x1']) ** 2 + (c['y2'] - c['y1']) ** 2))
            for i, c in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1', '2'], ['add', 'pow', 'sqrt', 'sub'],
                       'sqrt(pow(x2 - x1, 2) + pow(y2 - y1, 2))',
                       'sqrt((x2-x1)**2+(y2-y1)**2)')

def _gen_feynman_I_10_7():
    """Feynman I.10.7: relativistic mass, m = m_0 / sqrt(1 - v**2/c**2).

    Raw from the AI Feynman set: `m_0 / sqrt(1 - v ** 2 / 2.99792458e8 ** 2)`.
    v stays below c over the whole declared box.
    """
    c = 2.99792458e8
    names = ['m_0', 'v']
    columns = [[Fraction(1), Fraction(2), Fraction(5)],
               [1.0e8, 2.0e8, 2.5e8]]
    rows = [_row(i, c_, c_['m_0'] / math.sqrt(1.0 - c_['v'] ** 2 / c ** 2))
            for i, c_ in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1', '2'], ['div', 'pow', 'sqrt', 'sub'],
                       'm_0 / sqrt(1 - pow(v, 2) / pow(c, 2))',
                       'm_0/sqrt(1-v**2/2.99792458e8**2)')

def _gen_feynman_I_47_23():
    """Feynman I.47.23: speed of sound, c_s = sqrt(gamma * pr / rho).

    Raw from the AI Feynman set: `sqrt(gamma * pr / rho)` (Vars: gamma, pr, rho).
    """
    names = ['gamma', 'pr', 'rho']
    columns = [[1.2, 1.4, 1.67],
               [1.0e5, 1.5e5, 2.0e5],
               [0.5, 1.0, 2.0]]
    rows = [_row(i, c, math.sqrt(c['gamma'] * c['pr'] / c['rho']))
            for i, c in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1'], ['div', 'mul', 'sqrt'],
                       'sqrt(gamma * pr / rho)', 'sqrt(gamma*pr/rho)')

def _gen_feynman_III_10_19():
    """Feynman III.10.19: Zeeman energy, E = mom * sqrt(Bx**2 + By**2 + Bz**2).

    Raw from the AI Feynman set: `mom * sqrt(Bx ** 2 + By ** 2 + Bz ** 2)`.
    """
    names = ['mom', 'Bx', 'By', 'Bz']
    columns = [[Fraction(1), Fraction(2), Fraction(5)],
               [Fraction(-3), Fraction(0), Fraction(4)],
               [Fraction(-2), Fraction(1), Fraction(3)],
               [Fraction(-1), Fraction(5)]]
    rows = [_row(i, c, c['mom'] * math.sqrt(c['Bx'] ** 2 + c['By'] ** 2 + c['Bz'] ** 2))
            for i, c in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1', '2'], ['add', 'mul', 'pow', 'sqrt'],
                       'mom * sqrt(pow(Bx, 2) + pow(By, 2) + pow(Bz, 2))',
                       'mom*sqrt(Bx**2+By**2+Bz**2)')

def _gen_feynman_II_24_17():
    """Feynman II.24.17: waveguide wavenumber, k = sqrt(omega**2/c**2 - pi**2/d**2).

    Raw from the AI Feynman set: `sqrt(omega ** 2 / 2.99792458e8 ** 2 - pi ** 2 / d ** 2)`.
    The declared box keeps omega**2/c**2 > pi**2/d**2 at its extreme corner
    (omega = 3e12, d = 1e-3 gives 1.0e8 - 9.9e6 > 0), so no sample leaves the domain.
    """
    c = 2.99792458e8
    names = ['omega', 'd']
    columns = [[3.0e12, 4.0e12, 6.0e12],
               [1.0e-3, 2.0e-3, 5.0e-3]]
    rows = [_row(i, c_, math.sqrt(c_['omega'] ** 2 / c ** 2 - math.pi ** 2 / c_['d'] ** 2))
            for i, c_ in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1', '2'], ['div', 'pi', 'pow', 'sqrt', 'sub'],
                       'sqrt(pow(omega, 2) / pow(c, 2) - pow(pi, 2) / pow(d, 2))',
                       'sqrt(omega**2/2.99792458e8**2-pi**2/d**2)')

# ---------------------------------------------------------------------------
# L4: transcendental laws (exp / sin / cos / log / pi). Floating point rows and
# the 1/1000 band; the declared boxes keep every sampled point inside the domain.
# ---------------------------------------------------------------------------

def _gen_feynman_I_18_12():
    """Feynman I.18.12: torque, tau = r * F * sin(theta).

    Raw from the AI Feynman set: `r * F * sin(theta)` (Vars: r, F, theta).
    """
    names = ['r', 'F', 'theta']
    columns = [[Fraction(1), Fraction(2), Fraction(5)],
               [Fraction(-3), Fraction(1), Fraction(4)],
               [0.0, 0.5, 1.0, 1.8]]
    rows = [_row(i, c, c['r'] * c['F'] * math.sin(c['theta']))
            for i, c in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1'], ['mul', 'sin'],
                       'r * F * sin(theta)', 'r*F*sin(theta)')

def _gen_feynman_I_40_1():
    """Feynman I.40.1: barometric formula, n = n_0 * exp(-m*g*x/(k*T)).

    Raw from the AI Feynman set:
    `n_0 * exp(-m * 9.80665 * x / (1.380649e-23 * T))`.
    Box keeps the exponent inside [-1.5, 0], so exp stays in [0.22, 1].
    """
    g = 9.80665
    k = 1.380649e-23
    names = ['n_0', 'm', 'x', 'T']
    columns = [[Fraction(1), Fraction(2), Fraction(5)],
               [1.0e-25, 2.0e-25, 5.0e-25],
               [100.0, 500.0, 1000.0],
               [250.0, 350.0]]
    rows = [_row(i, c, c['n_0'] * math.exp(-c['m'] * g * c['x'] / (k * c['T'])))
            for i, c in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1'], ['div', 'exp', 'mul', 'neg'],
                       'n_0 * exp(-m * g * x / (k * T))',
                       'n_0*exp(-m*9.80665*x/(1.380649e-23*T))')

def _gen_feynman_I_41_16():
    """Feynman I.41.16: Planck spectral density,
    u = hbar*omega**3 / (pi**2 * c**2 * (exp(hbar*omega/(k*T)) - 1)).

    Raw from the AI Feynman set:
    `6.626e-34 / (2 * pi) * omega ** 3 / (pi ** 2 * 2.99792458e8 ** 2 *
     (exp((6.626e-34 / (2 * pi)) * omega / (1.380649e-23 * T)) - 1))`.
    Box keeps hbar*omega/(k*T) in [1.27, 3.83], so the Boltzmann factor is never 1.
    """
    hbar = 1.054571817e-34
    k = 1.380649e-23
    c = 2.99792458e8
    names = ['omega', 'T']
    columns = [[1.0e15, 2.0e15, 3.0e15],
               [4000.0, 5000.0, 6000.0]]
    rows = [_row(i, c_,
                 hbar * c_['omega'] ** 3
                 / (math.pi ** 2 * c ** 2 * (math.exp(hbar * c_['omega'] / (k * c_['T'])) - 1)))
            for i, c_ in enumerate(_product(names, columns))]
    spec = _float_spec(rows, names, ['0', '1', '2', '3'],
                       ['div', 'exp', 'mul', 'pi', 'pow', 'sub'],
                       'hbar*omega**3 / (pow(pi, 2)*pow(c, 2)*(exp(hbar*omega/(k*T)) - 1))',
                       '1.054571817e-34*omega**3/(pi**2*2.99792458e8**2*'
                       '(exp(1.054571817e-34*omega/(1.380649e-23*T))-1))')
    # Every value here sits near 1e-8, five orders below the shared 1/1000 band, so
    # the constant 0 used to certify and be scored exact (law="0", claim_exact=True
    # in results/devset-run-30-fixed.json).  Nothing in the law depends on the scale
    # of the inputs, so grade it relatively and let a wrong law be wrong.
    spec['tolerances']['y']['abs'] = '0'
    return spec

def _gen_feynman_I_26_2():
    """Feynman I.26.2: Snell's law ratio, n1/n2 = sin(theta1) / sin(theta2).

    Raw from the AI Feynman set: `sin(theta1) / sin(theta2)`.
    Both angles stay inside (0, pi/2) over the box, so neither sine vanishes.
    """
    names = ['theta1', 'theta2']
    columns = [[0.2, 0.6, 1.2],
               [0.1, 0.3, 0.8]]
    rows = [_row(i, c, math.sin(c['theta1']) / math.sin(c['theta2']))
            for i, c in enumerate(_product(names, columns))]
    return _float_spec(rows, names, ['0', '1'], ['div', 'sin'],
                       'sin(theta1) / sin(theta2)', 'sin(theta1)/sin(theta2)')

# ---------------------------------------------------------------------------
# L5: distractors. The law is a real Feynman formula; one or two statistically
# irrelevant inputs are declared alongside it. Values are drawn from a fixed
# seed so the rows are byte-identical across runs.
# ---------------------------------------------------------------------------

def _gen_srsd_hard_feynman_I_9_18():
    """Feynman I.9.18 with a distractor: F = m1*m2/(r**2) [G=1], extra input `zeta`.

    Raw from the AI Feynman set: `6.6743e-11 * m1 * m2 / ((x2-x1)**2 + ...)`,
    reduced here exactly like the existing `feynman-I.9.18` generator. `zeta`
    never appears in the law; it only widens the declared input space.
    """
    rng = random.Random(388)
    names = ['m1', 'm2', 'r', 'zeta']
    columns = [[Fraction(1), Fraction(2), Fraction(3)],
               [Fraction(1), Fraction(2), Fraction(4)],
               [Fraction(1), Fraction(2), Fraction(3), Fraction(5)]]
    rows = [_row(i, {**c, 'zeta': Fraction(rng.randint(1, 40), 4)},
                 c['m1'] * c['m2'] / (c['r'] ** 2))
            for i, c in enumerate(_product(names[:3], columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['div', 'mul', 'pow'],
                       '(m1 * m2) / pow(r, 2)', 'm1*m2/(r**2)')

def _gen_srsd_hard_feynman_I_14_3():
    """Feynman I.14.3 with a distractor: U = m*g*h, extra input `phi`.

    Raw from the AI Feynman set: `9.8066 * m * z`; `g` is kept as an input like
    the existing `feynman-I.14.3` generator. `phi` never appears in the law.
    """
    rng = random.Random(139)
    names = ['m', 'g', 'h', 'phi']
    columns = [[Fraction(1), Fraction(2), Fraction(5)],
               [Fraction(98, 10), Fraction(10), Fraction(1)],
               [Fraction(1), Fraction(5), Fraction(10)]]
    rows = [_row(i, {**c, 'phi': Fraction(rng.randint(1, 60), 3)},
                 c['m'] * c['g'] * c['h'])
            for i, c in enumerate(_product(names[:3], columns))]
    return _exact_spec(rows, names, ['1', '2'], ['mul'],
                       'm * g * h', 'm*g*h')

def _gen_srsd_hard_feynman_II_2_42():
    """Feynman II.2.42 with two distractors: P = kappa*(T2-T1)/d, extra `psi`, `eta`.

    Raw from the AI Feynman set: `kappa * (T2 - T1) * A / d`; this generator keeps
    the same reduced form as the existing `feynman-II.2.42` one. Neither `psi`
    nor `eta` appears in the law.
    """
    rng = random.Random(378)
    names = ['kappa', 't1', 't2', 'd', 'psi', 'eta']
    columns = [[Fraction(1), Fraction(2)],
               [Fraction(10), Fraction(20)],
               [Fraction(30), Fraction(50)],
               [Fraction(1), Fraction(2), Fraction(4), Fraction(8)]]
    rows = [_row(i, {**c, 'psi': Fraction(rng.randint(1, 100), 5),
                     'eta': Fraction(rng.randint(1, 100), 7)},
                 c['kappa'] * (c['t2'] - c['t1']) / c['d'])
            for i, c in enumerate(_product(names[:4], columns))]
    return _exact_spec(rows, names, ['0', '1', '2'], ['sub', 'mul', 'div'],
                       'kappa * (t2 - t1) / d', 'kappa*(t2-t1)/d')

# ---------------------------------------------------------------------------
# Mathematics: sequence inversion over an index. The observable is the n-th
# term, the only input is the index, and one experiment buys one more term.
# The algebraic layers are exact (zero tolerance); the transcendental ones reuse
# the 1/1000 band from L4 physics. These exist because the physics set exercises
# neither of the two fields this kernel is aimed at: a sequence can be
# reverse-engineered without measurement error, and a short prefix really does
# admit several inequivalent continuations, which is the underdetermination the
# physics set only shows by accident.

_MATH_INDEX = [Fraction(n) for n in range(1, 9)]

def _math_rows(values, name='n', index=_MATH_INDEX):
    """One row per index, in index order; ``values`` maps an index to its term."""
    return [_row(i, {name: n}, values(n)) for i, n in enumerate(index)]

def _gen_math_square():
    """L1: a(n) = n**2 -> 1, 4, 9, 16, 25, 36, 49, 64."""
    return _exact_spec(_math_rows(lambda n: n ** 2), ['n'], ['2'], ['pow'],
                       'pow(n, 2)', 'n**2')

def _gen_math_pronic():
    """L1: a(n) = n*(n-1), the pronic numbers 0, 2, 6, 12, 20, 30, 42, 56."""
    return _exact_spec(_math_rows(lambda n: n * (n - 1)), ['n'], ['1'], ['mul', 'sub'],
                       'n * (n - 1)', 'n*(n-1)')

def _gen_math_triangular():
    """L1: a(n) = n*(n+1)/2 -> 1, 3, 6, 10, 15, 21, 28, 36."""
    return _exact_spec(_math_rows(lambda n: n * (n + 1) / 2), ['n'], ['1', '2'],
                       ['mul', 'add', 'div'], '(n * (n + 1)) / 2', 'n*(n+1)/2')

def _gen_math_reciprocal():
    """L2: a(n) = 1/(n+1) -> 1/2, 1/3, ..., 1/9, all exact rationals."""
    return _exact_spec(_math_rows(lambda n: 1 / (n + 1)), ['n'], ['1'], ['add', 'div'],
                       '1 / (n + 1)', '1/(n+1)')

def _gen_math_reciprocal_square():
    """L2: a(n) = 1/n**2 -> 1, 1/4, 1/9, ..., 1/64, all exact rationals."""
    return _exact_spec(_math_rows(lambda n: 1 / (n ** 2)), ['n'], ['1', '2'],
                       ['div', 'pow'], '1 / pow(n, 2)', '1/n**2')

def _gen_math_sqrt_index():
    """L3: a(n) = sqrt(n). Irrational from n=2 on, so the band is 1/1000."""
    return _float_spec(_math_rows(lambda n: math.sqrt(n)), ['n'], ['1'], ['sqrt'],
                       'sqrt(n)', 'sqrt(n)')

def _gen_math_hypotenuse():
    """L3: a(n) = sqrt(n**2 + 1), the hypotenuse of a unit step."""
    return _float_spec(_math_rows(lambda n: math.sqrt(n ** 2 + 1)), ['n'], ['1', '2'],
                       ['add', 'pow', 'sqrt'], 'sqrt(pow(n, 2) + 1)', 'sqrt(n**2+1)')

def _gen_math_exp_decay():
    """L4: a(n) = exp(-n), a discrete decay whose law is not a polynomial."""
    return _float_spec(_math_rows(lambda n: math.exp(-float(n))), ['n'], ['1'],
                       ['neg', 'exp'], 'exp(neg(n))', 'exp(-n)')

def _gen_math_sin_index():
    """L4: a(n) = sin(n) in radians, the canonical oscillator sequence."""
    return _float_spec(_math_rows(lambda n: math.sin(float(n))), ['n'], ['1'], ['sin'],
                       'sin(n)', 'sin(n)')

def _gen_math_square_distractor():
    """L5: a(n) = n**2 with a distractor `q` that never enters the law."""
    names = ['n', 'q']
    columns = [[Fraction(1), Fraction(2), Fraction(5), Fraction(8)],
               [Fraction(1), Fraction(2)]]
    rows = [_row(i, c, c['n'] ** 2) for i, c in enumerate(_product(names, columns))]
    return _exact_spec(rows, names, ['2'], ['pow'], 'pow(n, 2)', 'n**2')

REGISTRY = {
    'feynman-I.6.20a': _gen_feynman_I_6_20a,
    'feynman-I.9.18': _gen_feynman_I_9_18,
    'feynman-II.2.42': _gen_feynman_II_2_42,
    'feynman-I.12.1': _gen_feynman_I_12_1,
    'feynman-I.14.3': _gen_feynman_I_14_3,
    # L1 plain algebra
    'feynman-I.12.5': _gen_feynman_I_12_5,
    'feynman-II.37.1': _gen_feynman_II_37_1,
    'feynman-I.14.4': _gen_feynman_I_14_4,
    'feynman-I.39.10': _gen_feynman_I_39_10,
    'feynman-II.34.2': _gen_feynman_II_34_2,
    'feynman-II.27.18': _gen_feynman_II_27_18,
    # L2 rational fractions
    'feynman-I.25.13': _gen_feynman_I_25_13,
    'feynman-II.38.14': _gen_feynman_II_38_14,
    'feynman-II.10.9': _gen_feynman_II_10_9,
    'feynman-I.18.4': _gen_feynman_I_18_4,
    'feynman-I.27.6': _gen_feynman_I_27_6,
    'feynman-II.34.11': _gen_feynman_II_34_11,
    'feynman-II.38.3': _gen_feynman_II_38_3,
    # L3 square roots and geometry
    'feynman-I.8.14': _gen_feynman_I_8_14,
    'feynman-I.10.7': _gen_feynman_I_10_7,
    'feynman-I.47.23': _gen_feynman_I_47_23,
    'feynman-III.10.19': _gen_feynman_III_10_19,
    'feynman-II.24.17': _gen_feynman_II_24_17,
    # L4 transcendental
    'feynman-I.18.12': _gen_feynman_I_18_12,
    'feynman-I.40.1': _gen_feynman_I_40_1,
    'feynman-I.41.16': _gen_feynman_I_41_16,
    'feynman-I.26.2': _gen_feynman_I_26_2,
    # L5 distractor variables
    'srsd-hard-feynman-I.9.18': _gen_srsd_hard_feynman_I_9_18,
    'srsd-hard-feynman-I.14.3': _gen_srsd_hard_feynman_I_14_3,
    'srsd-hard-feynman-II.2.42': _gen_srsd_hard_feynman_II_2_42,
    # Mathematics: sequence inversion. L1 exact algebra, L2 exact rationals,
    # L3 square roots, L4 transcendental, L5 distractor variable.
    'math-square': _gen_math_square,
    'math-pronic': _gen_math_pronic,
    'math-triangular': _gen_math_triangular,
    'math-reciprocal': _gen_math_reciprocal,
    'math-reciprocal-square': _gen_math_reciprocal_square,
    'math-sqrt-index': _gen_math_sqrt_index,
    'math-hypotenuse': _gen_math_hypotenuse,
    'math-exp-decay': _gen_math_exp_decay,
    'math-sin-index': _gen_math_sin_index,
    'math-square-distractor': _gen_math_square_distractor,
}

def list_equations():
    """Return all available offline built-in devset equations."""
    return sorted(REGISTRY.keys())

def load(name):
    """Load a devset equation specification.

    Returns dict with keys: 'rows', 'tolerances', 'grammar', 'truth'.
    """
    if name not in REGISTRY:
        raise KeyError(f'Unknown devset equation: {name}. Available: {list_equations()}')
    return REGISTRY[name]()
