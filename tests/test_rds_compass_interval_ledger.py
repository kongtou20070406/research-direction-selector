"""Interval measurements must survive the ledger: a radius that is written down must read back.

``text`` spells only the midpoint of an ``Inexact``. Writing a ledger with it turned an
undecided cell (``UNTESTED``) into a decided one (``CONSISTENT``) as soon as the same
measurement came back through ``exact``. Item 1 is the reverse test: restore ``text`` in
``rds_compass.py:237`` and this module goes red.
"""
from fractions import Fraction
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_model as model
import rds_compass_store as cstore
from rds_compass_declaration import initialize
from rds_compass_drive import drive

# The experiment under study reports an interval measurement, never a point.
INTERVAL = '''import argparse, json, pathlib
p = argparse.ArgumentParser(); p.add_argument('--x'); p.add_argument('--output', required=True)
a = p.parse_args()
out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'outputs': {'y': {'lo': 0.7, 'hi': 1.1}}}), encoding='utf-8')
'''


class LedgerRoundTripTests(unittest.TestCase):
    """Item 1: the verdict may not change when the measurement passes through the ledger."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        root = self.base / 'interval'
        path = fx.law_campaign(root, 'x', [0], hypotheses=[('zero', '0')])
        fx.write(root / 'experiment.py', INTERVAL)
        value = json.loads(path.read_text(encoding='utf-8'))
        value['observables']['y']['tolerance'] = {'abs': 1}
        fx.write(path, value)
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        self.root = root
        self.store = cstore.store_for(root)
        self.tolerances = {'y': model.tolerance({'abs': 1})}
        self.measured_interval = model.exact({'lo': '7/10', 'hi': '11/10'})

    def tearDown(self):
        self.temp.cleanup()

    def buy_and_ingest(self):
        manifest = compass.manifest(compass.fold(self.store), 'x0')
        self.store.register(manifest)
        self.store.execute(manifest['id'])
        state, _ = compass.ingest(self.store)
        return state

    def test_in_memory_replay_leaves_the_cell_undecided(self):
        hyp = model.compile_hypothesis({'id': 'zero', 'predict': {'y': '0'}}, ['x'], ['y'])
        row = {'id': 'x0', 'inputs': {'x': model.exact(0)}, 'outputs': {'y': self.measured_interval}}
        result = model.replay(hyp, [row], self.tolerances)
        self.assertEqual(result['status'], 'UNTESTED')
        self.assertEqual((result['tested'], result['undecided']), (0, 1))

    def test_the_ledger_keeps_the_enclosure_the_verdict_kept(self):
        state = self.buy_and_ingest()
        observation = state['observations']['x0']
        self.assertEqual(observation['status'], 'MEASURED')
        # The ledger body itself carries both bounds, not the midpoint it would have lost.
        self.assertEqual(observation['outputs']['y'], {'lo': '7/10', 'hi': '11/10'})

        rows = compass.measured(state)
        self.assertEqual(len(rows), 1)
        read_back = model.exact(rows[0]['outputs']['y']).radius
        self.assertIsInstance(rows[0]['outputs']['y'], model.Inexact)
        self.assertEqual(read_back, Fraction(1, 5))

        result = model.replay(state['hypotheses']['zero'], rows, self.tolerances)
        self.assertEqual(result['status'], 'UNTESTED')
        self.assertEqual((result['tested'], result['undecided']), (0, 1))
        self.assertEqual(state['hypotheses']['zero']['status'], 'UNTESTED')
        self.assertEqual(state['hypotheses']['zero']['replay']['tested'], 0)

    def test_a_decided_campaign_reports_zero_undecided(self):
        """The key has to exist and say zero, not go missing when nothing is open.

        `model.replay` returns its dict without `undecided` when the count is zero, so a
        plain `replay[k]` comprehension drops the field exactly on the clean runs whose
        verification record is read for the tested/undecided split.
        """
        root = self.base / 'point'
        path = fx.law_campaign(root, 'x+1', [0, 1, 2], hypotheses=[('lin', 'x+1')])
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        drive(root, max_steps=12, publish=False)
        store = cstore.store_for(root)
        replay = compass.verify(store, compass.fold(store))['replay']
        self.assertIsNotNone(replay)
        self.assertEqual(replay['undecided'], 0)
        self.assertGreater(replay['tested'], 0)


class FitsMemoKeyTests(unittest.TestCase):
    """Item 2: a point value and the interval around it are two different measurements."""

    def key(self, output):
        rows = [{'id': 'x0', 'inputs': {'x': model.exact(0)}, 'outputs': {'y': output}}]
        return compass._fits_memo_key(rows, ['x'], 'y', {'y': model.tolerance({'abs': 1})},
                                      {}, 7, 100, [], False, 'none')

    def test_an_interval_and_its_midpoint_do_not_share_a_key(self):
        interval = self.key(model.exact({'lo': '7/10', 'hi': '11/10'}))
        point = self.key(model.exact('9/10'))
        self.assertNotEqual(interval, point)

    def test_identical_intervals_still_share_a_key(self):
        left = self.key(model.exact({'lo': '7/10', 'hi': '11/10'}))
        right = self.key(model.exact({'lo': '7/10', 'hi': '11/10'}))
        self.assertEqual(left, right)


class PointValueRegressionTests(unittest.TestCase):
    """Item 3: the point-value path keeps its pre-change behaviour exactly."""

    CASES = (
        (0, Fraction(0, 1)),
        (1, Fraction(1, 1)),
        (-3, Fraction(-3, 1)),
        (1 / 3, Fraction(3333333333333333, 10000000000000000)),
        (0.1, Fraction(1, 10)),
        ('7/2', Fraction(7, 2)),
    )

    def test_point_values_match_the_pre_change_baseline(self):
        for value, expected in self.CASES:
            with self.subTest(value=value):
                got = model.exact(value)
                self.assertEqual(got, expected)
                self.assertIs(type(got), Fraction)
                self.assertFalse(hasattr(got, 'radius'))
                self.assertEqual(getattr(got, 'radius', 0), 0)

    def test_point_spelling_is_unchanged(self):
        for value, expected in self.CASES:
            with self.subTest(value=value):
                got = model.spell(value)
                self.assertIsInstance(got, str)
                self.assertEqual(model.exact(got), expected)


class EnclosureArithmeticTests(unittest.TestCase):
    """Item 4: the arithmetic envelope contains every value the two spans can produce."""

    # (a_lo, a_hi, b_lo, b_hi): negatives, mixed signs and fractions, above and below one.
    CASES = (
        ('7/10', '11/10', '9/10', '9/10'),
        ('-1/2', '1/2', '1/3', '2/3'),
        ('-3/2', '-1/2', '-1/4', '3/4'),
        ('-5/3', '-4/3', '7/5', '8/5'),
        ('0', '1', '0', '1'),
        ('-2', '-1', '-3', '-2'),
        ('1/7', '2/7', '-1/3', '1/3'),
        ('-7/2', '1/2', '2', '5/2'),
        ('100/3', '101/3', '-1/1000', '1/1000'),
        ('-1', '2', '-2', '-1'),
    )

    def sample(self, lo, hi):
        """Both bounds plus interior points, so a wrong endpoint cannot hide."""
        lo, hi = Fraction(lo), Fraction(hi)
        if lo == hi:
            return (lo,)
        return tuple(lo + (hi - lo) * Fraction(k, 4) for k in range(5))

    def envelope(self, result):
        radius = getattr(result, 'radius', 0)
        return Fraction(result.numerator, result.denominator) - radius, \
            Fraction(result.numerator, result.denominator) + radius

    def test_subtraction_contains_every_difference(self):
        for a_lo, a_hi, b_lo, b_hi in self.CASES:
            a, b = model.exact({'lo': a_lo, 'hi': a_hi}), model.exact({'lo': b_lo, 'hi': b_hi})
            lo, hi = self.envelope(a - b)
            for left in self.sample(a_lo, a_hi):
                for right in self.sample(b_lo, b_hi):
                    with self.subTest(a=(a_lo, a_hi), b=(b_lo, b_hi), value=left - right):
                        self.assertLessEqual(lo, left - right)
                        self.assertGreaterEqual(hi, left - right)

    def test_addition_contains_every_sum(self):
        for a_lo, a_hi, b_lo, b_hi in self.CASES:
            a, b = model.exact({'lo': a_lo, 'hi': a_hi}), model.exact({'lo': b_lo, 'hi': b_hi})
            lo, hi = self.envelope(a + b)
            for left in self.sample(a_lo, a_hi):
                for right in self.sample(b_lo, b_hi):
                    with self.subTest(a=(a_lo, a_hi), b=(b_lo, b_hi), value=left + right):
                        self.assertLessEqual(lo, left + right)
                        self.assertGreaterEqual(hi, left + right)

    def test_multiplication_contains_every_product(self):
        for a_lo, a_hi, b_lo, b_hi in self.CASES:
            a, b = model.exact({'lo': a_lo, 'hi': a_hi}), model.exact({'lo': b_lo, 'hi': b_hi})
            lo, hi = self.envelope(a * b)
            for left in self.sample(a_lo, a_hi):
                for right in self.sample(b_lo, b_hi):
                    with self.subTest(a=(a_lo, a_hi), b=(b_lo, b_hi), value=left * right):
                        self.assertLessEqual(lo, left * right)
                        self.assertGreaterEqual(hi, left * right)

    def test_division_contains_every_quotient_when_the_divisor_excludes_zero(self):
        for a_lo, a_hi, b_lo, b_hi in self.CASES:
            if Fraction(b_lo) <= 0 <= Fraction(b_hi):
                continue
            a, b = model.exact({'lo': a_lo, 'hi': a_hi}), model.exact({'lo': b_lo, 'hi': b_hi})
            lo, hi = self.envelope(a / b)
            for left in self.sample(a_lo, a_hi):
                for right in self.sample(b_lo, b_hi):
                    with self.subTest(a=(a_lo, a_hi), b=(b_lo, b_hi), value=left / right):
                        self.assertLessEqual(lo, left / right)
                        self.assertGreaterEqual(hi, left / right)

    def test_a_plain_operand_degrades_to_the_same_radius(self):
        a = model.exact({'lo': '7/10', 'hi': '11/10'})
        self.assertEqual((a - Fraction(1, 2)).radius, a.radius)
        self.assertEqual((Fraction(1, 2) - a).radius, a.radius)
        self.assertEqual((a * 3).radius, 3 * a.radius)
        # A zero radius is a point: the result is a plain Fraction, as ``text`` spells it.
        self.assertIs(type(model.exact({'lo': '1', 'hi': '1'}) + 1), Fraction)
        self.assertIs(type(model.exact({'lo': '1', 'hi': '1'}) * 2), Fraction)


class DivisionByAnEnclosureContainingZeroTests(unittest.TestCase):
    """Item 5: a divisor span that straddles zero has no reading; it raises, never widens."""

    def test_a_straddling_divisor_raises_domain_error(self):
        straddling = model.exact({'lo': '-1/2', 'hi': '1/2'})
        with self.assertRaises(model.DomainError):
            Fraction(2) / straddling
        with self.assertRaises(model.DomainError):
            straddling / straddling
        # A numerator span may straddle zero; only the divisor has to avoid it.
        self.assertEqual((straddling / Fraction(2)).radius, Fraction(1, 4))

    def test_a_divisor_that_only_touches_zero_raises_domain_error(self):
        touching = model.exact({'lo': '0', 'hi': '1/2'})
        with self.assertRaises(model.DomainError):
            Fraction(1) / touching

    def test_a_divisor_away_from_zero_is_allowed(self):
        shifted = model.exact({'lo': '3/2', 'hi': '5/2'})
        self.assertEqual((Fraction(1) / shifted).radius, model._quotient(
            Fraction(1), 0, Fraction(2), Fraction(1, 2)).radius)


class SpellingTests(unittest.TestCase):
    def test_spell_round_trips_an_enclosure(self):
        value = model.exact({'lo': '7/10', 'hi': '11/10'})
        got = model.spell(value)
        self.assertEqual(got, {'lo': '7/10', 'hi': '11/10'})
        back = model.exact(got)
        self.assertIsInstance(back, model.Inexact)
        self.assertEqual((back, back.radius), (value, value.radius))

    def test_spell_of_a_degenerate_interval_is_point_text(self):
        got = model.spell(model.exact({'lo': '3/2', 'hi': '3/2'}))
        self.assertIsInstance(got, str)
        self.assertEqual(got, '3/2')

    def test_spell_rejects_what_exact_rejects(self):
        for bad in (True, {'lo': 'x', 'hi': 2}, {'lo': '1', 'hi': '0'}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    model.spell(bad)


if __name__ == '__main__':
    unittest.main()
