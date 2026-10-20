"""NewtonBench development set loader for RDS Compass benchmark evaluations.

Data Sources:
- AI Feynman Database for Symbolic Regression (Udrescu & Tegmark, Science Advances 2020 / arXiv:1905.11481)
- The Feynman Lectures on Physics (Caltech / Feynman, Leighton, Sands)
- Offline & deterministic: Built-in minimal equations with exact rational / bounded decimal points.
"""
from fractions import Fraction
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts')]
import rds_compass_model as model

def _to_text(val):
    if isinstance(val, (int, Fraction)):
        return model.text(val)
    return model.text(repr(float(val)))

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

REGISTRY = {
    'feynman-I.6.20a': _gen_feynman_I_6_20a,
    'feynman-I.9.18': _gen_feynman_I_9_18,
    'feynman-II.2.42': _gen_feynman_II_2_42,
    'feynman-I.12.1': _gen_feynman_I_12_1,
    'feynman-I.14.3': _gen_feynman_I_14_3,
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
