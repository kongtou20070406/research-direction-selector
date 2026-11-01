"""F3 regression: the polynomial certificate never yields to the receipts it fit.

The deleted ``continue`` at the old rds_compass.py:1406 let a memorised-exception
law survive: the interpolant is fitted on only the first MAX_ROWS receipts, so a
pulse beyond that prefix made the receipt agree with the law and not with the
interpolation -- and the interpolation was discarded as "a spent hypothesis".
A certificate that yields to the receipts it vouches for is not independent:
a disagreement is a rival, reported, never waved through.
"""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_store as cstore
from rds_compass_declaration import initialize


class CertificateYieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_pulse_beyond_the_fit_prefix_is_a_rival_not_a_continue(self):
        # World: y = x, except pulses of 100 at x = 8 and x = 9. The certificate
        # fits its degree-(m-2) polynomial on the first MAX_ROWS=8 receipts
        # (x = 0..7, pure y = x), so the interpolant is y = x. The declared law
        # memorises both pulses with nested conditionals. At x = 8 the receipt
        # agrees with the law (100) and not with the interpolant (8): exactly
        # the case the old code continued past.
        root = self.base / 'pulse'
        fx.law_campaign(root, '100 if x in (8, 9) else x', range(10),
                        hypotheses=[('pulse', '100 if x == 8 else (100 if x == 9 else x)')])
        self.assertEqual(initialize(root, root / 'compass.json')['status'], 'INITIALIZED')
        store = cstore.store_for(root)
        for x in range(10):
            manifest = compass.manifest(compass.fold(store), 'x' + str(x))
            store.register(manifest)
            store.execute(manifest['id'])
        state, _ = compass.ingest(store)
        best = state['hypotheses']['pulse']
        result = compass._polynomial_check(state, best)
        self.assertEqual(result['status'], 'POLYNOMIAL_RIVAL_DISAGREES', result)
        self.assertEqual(result['witness']['candidate'], 'x8')


if __name__ == '__main__':
    unittest.main()
