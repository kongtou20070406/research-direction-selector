"""Guard: no devset equation may be graded with a band that swallows its own values.

`_float_spec` used to hand every transcendental equation `abs: 1/1000`.
`feynman-I.41.16`'s true values sit near 1e-8, five orders below that band, so
the constant `0` passed all nine points and `results/devset-run-30-fixed.json`
records it as `law="0"`, `claim_exact=True` on three budgets.
"""
import sys
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / 'scripts', ROOT / 'benchmark' / 'newtonbench'):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import devset  # noqa: E402
import rds_compass_model as model  # noqa: E402


class ToleranceBandTests(unittest.TestCase):
    def test_no_equation_is_graded_with_a_band_at_its_own_scale(self):
        """An absolute band of a hundredth of the largest value lets a constant pass."""
        offenders = []
        for name in devset.list_equations():
            spec = devset.load(name)
            observable = spec['truth']['observable']
            declared = spec['tolerances'][observable]
            absolute = Fraction(declared['abs'])
            if not absolute:
                continue
            values = [abs(model.exact(row['outputs'][observable])) for row in spec['rows']
                      if row['outputs'].get(observable) is not None]
            if values and absolute * 100 >= max(values):
                offenders.append(name)
        self.assertEqual(offenders, [], 'these equations need a relative band, not the shared one')

    def test_the_constant_zero_no_longer_certifies_on_the_spectral_density(self):
        spec = devset.load('feynman-I.41.16')
        observable = spec['truth']['observable']
        band = model.tolerance(spec['tolerances'][observable])
        zero = model.exact(Fraction(0))
        passed = [row['id'] for row in spec['rows']
                  if model.decide(zero, model.exact(row['outputs'][observable]), band)]
        self.assertEqual(passed, [])


if __name__ == '__main__':
    unittest.main()
