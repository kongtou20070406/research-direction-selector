"""Interval observations through ``model.exact()``: the midpoint keeps its half-width.

An observed value measured across seeds is not a point. ``exact({'lo', 'hi'})``
parses to an ``Inexact`` midpoint whose ``radius`` is the half-width ``decide``
already reads, so noise reaches the verdict instead of being collapsed away.
Point-value inputs must keep their pre-change behaviour exactly.
"""
from fractions import Fraction
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass_model as model


class PointValueRegressionTests(unittest.TestCase):
    """Item 1 (reverse test): opening the interval branch moves no point value."""

    # Values recorded from exact() before the interval branch existed.
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


class IntervalObservationTests(unittest.TestCase):
    def test_interval_parses_to_the_midpoint_with_half_width_radius(self):
        got = model.exact({'lo': '0', 'hi': '1/2'})
        self.assertIsInstance(got, model.Inexact)
        self.assertEqual(got, Fraction(1, 4))
        self.assertEqual(got.radius, (Fraction(1, 2) - 0) / 2)

    def test_bounds_parse_through_the_point_value_path(self):
        got = model.exact({'lo': 0, 'hi': 0.1})
        self.assertEqual((got - got.radius, got + got.radius), (Fraction(0), Fraction(1, 10)))

    def test_degenerate_interval_is_a_point_with_zero_radius(self):
        got = model.exact({'lo': '3/2', 'hi': '3/2'})
        self.assertEqual((got, got.radius), (Fraction(3, 2), Fraction(0)))

    def test_inverted_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            model.exact({'lo': '1', 'hi': '0'})

    def test_dict_without_exactly_lo_and_hi_is_rejected(self):
        for bad in ({'lo': 0, 'hi': 1, 'unit': 'x'}, {}, {'lo': 0}, {'hi': 1},
                    {'value': 1}, {'lo': 0, 'hi': 1, 'mid': 0}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    model.exact(bad)

    def test_nested_intervals_are_rejected(self):
        for bad in ({'lo': {'lo': 0, 'hi': 1}, 'hi': 2},
                    {'lo': 0, 'hi': {'lo': 1, 'hi': 2}},
                    {'lo': {'lo': 0, 'hi': 1}, 'hi': {'lo': 2, 'hi': 3}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    model.exact(bad)

    def test_bounds_reuse_the_existing_parsing_rules(self):
        for bad in ({'lo': 'x', 'hi': 2}, {'lo': True, 'hi': 2},
                    {'lo': float('inf'), 'hi': 2}, {'lo': 0, 'hi': 2 ** 600}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    model.exact(bad)

    def test_bounded_precision_still_applies_to_the_midpoint(self):
        # Each bound fits in MAX_BITS bits; their midpoint does not.
        low, high = Fraction(1, 2 ** 400 + 3), Fraction(1, 2 ** 400 + 1)
        self.assertLess(low.denominator.bit_length(), model.MAX_BITS)
        self.assertLess(high.denominator.bit_length(), model.MAX_BITS)
        with self.assertRaises(model.DomainError):
            model.exact({'lo': low, 'hi': high})


class IntervalParticipationTests(unittest.TestCase):
    """Item 5 (end to end): the radius must reach ``decide`` and ``replay``."""

    def setUp(self):
        self.hypothesis = model.compile_hypothesis(
            {'id': 'h_interval', 'predict': {'y': {'var': 'x'}}}, ['x'], ['y'])
        self.tolerances = {'y': model.tolerance({'abs': 1})}
        self.predicted = model.exact(0)

    def replay(self, observed):
        row = {'id': 'o1', 'inputs': {'x': 0}, 'outputs': {'y': observed}}
        return model.replay(self.hypothesis, [row], self.tolerances)

    def test_decide_switches_on_the_radius_alone(self):
        # gap 0.9 x tolerance, observed radius 0.2 x tolerance.
        straddling = model.exact({'lo': '7/10', 'hi': '11/10'})
        point = model.exact('9/10')
        self.assertEqual(straddling.radius, Fraction(1, 5))
        self.assertIsNone(model.decide(self.predicted, straddling, self.tolerances['y']))
        self.assertIs(model.decide(self.predicted, point, self.tolerances['y']), True)

    def test_straddling_interval_leaves_the_cell_undecided(self):
        result = self.replay(model.exact({'lo': '7/10', 'hi': '11/10'}))
        self.assertIn(result['status'], ('CONSISTENT', 'REFUTED', 'UNTESTED'))
        self.assertEqual(result['status'], 'UNTESTED')
        self.assertEqual((result['tested'], result['undecided']), (0, 1))
        self.assertEqual(result['counterexamples'], [])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_the_same_numbers_as_a_point_value_do_test_the_cell(self):
        result = self.replay(model.exact('9/10'))
        self.assertEqual(result['status'], 'CONSISTENT')
        self.assertEqual(result['tested'], 1)
        self.assertEqual(result.get('undecided', 0), 0)

    def test_interval_inside_the_band_stays_consistent(self):
        result = self.replay(model.exact({'lo': '-1/10', 'hi': '1/10'}))
        self.assertEqual((result['status'], result['tested']), ('CONSISTENT', 1))

    def test_interval_outside_the_band_refutes(self):
        result = self.replay(model.exact({'lo': '5/2', 'hi': '3'}))
        self.assertEqual((result['status'], result['tested']), ('REFUTED', 1))
        self.assertEqual(len(result['counterexamples']), 1)
        self.assertEqual(result['counterexamples'][0]['observed'], '11/4')


class ToleranceIsNotAMeasurementTests(unittest.TestCase):
    """Opening ``exact`` to intervals must not open ``tolerance`` to them.

    A band that is itself an interval has no reading: ``decide`` adds the
    measurement radius to the gap, so an interval tolerance would silently
    collapse to its midpoint and quietly widen every verdict at once.
    """

    def test_an_interval_tolerance_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            model.tolerance({'abs': {'lo': '0', 'hi': '1'}})
        self.assertIn('interval tolerance has no reading', str(caught.exception))

    def test_a_point_tolerance_still_parses(self):
        absolute, relative = model.tolerance({'abs': '1/10', 'rel': '1/100'})
        self.assertEqual((absolute, relative), (Fraction(1, 10), Fraction(1, 100)))


if __name__ == '__main__':
    unittest.main()
