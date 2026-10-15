"""Compass exact model: EIG over rival partitions, equivalence, synthesis, profiles."""
from fractions import Fraction
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass_model as model

EXACT = {'y': (Fraction(0), Fraction(0))}


def hyp(ident, expression, applies=None):
    value = {'id': ident, 'predict': {'y': expression}}
    if applies is not None:
        value['applies'] = applies
    return model.compile_hypothesis(value, ['x'], ['y'])


def rows(law, xs):
    return [{'id': f'x{x}', 'inputs': {'x': Fraction(x)}, 'outputs': {'y': Fraction(law(x))},
             'receipt_sha256': '0' * 64} for x in xs]


def candidates(xs):
    return [{'id': f'x{x}', 'inputs': {'x': Fraction(x)}} for x in xs]


class ExactDialectTests(unittest.TestCase):
    def test_rationals_stay_exact_and_division_by_zero_is_undefined(self):
        half = hyp('half', 'x/3')
        self.assertEqual(model.predict(half, {'x': Fraction(1)}), ({'y': Fraction(1, 3)}, 'PREDICTED'))
        inverse = hyp('inverse', '1/x')
        self.assertEqual(model.predict(inverse, {'x': Fraction(0)})[1], 'UNDEFINED')

    def test_identity_covers_only_executable_content(self):
        a = model.compile_hypothesis({'id': 'a', 'predict': {'y': 'x+1'}, 'statement': 'one story'}, ['x'], ['y'])
        b = model.compile_hypothesis({'id': 'b', 'predict': {'y': 'x+1'}, 'statement': 'another story'}, ['x'], ['y'])
        self.assertEqual(a['sha256'], b['sha256'])

    def test_replay_counts_undefined_where_applicable_against_the_rulebook(self):
        result = model.replay(hyp('inverse', '1/x'), rows(lambda x: 1, [0]), EXACT)
        self.assertEqual(result['status'], 'REFUTED')
        self.assertIsNone(result['counterexamples'][0]['predicted'])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_missing_measurement_refutes_nothing(self):
        row = {'id': 'x1', 'inputs': {'x': Fraction(1)}, 'outputs': {}}
        result = model.replay(hyp('lin', 'x'), [row], EXACT)
        self.assertEqual((result['status'], result['unknown_measurements']), ('UNTESTED', 1))


class InformationGainTests(unittest.TestCase):
    def test_equivalent_predictions_are_not_bought_twice(self):
        # x+1 and 2*x agree only at x=1; x=2 and x=3 split the rivals identically.
        rivals = [hyp('a', 'x+1'), hyp('b', '2*x')]
        ranked = model.rank_experiments(rivals, candidates([1, 2, 3]), EXACT)
        reasons = {row['id']: row['reason'] for row in ranked['excluded']}
        self.assertEqual(reasons['x1'], 'NON_DISCRIMINATING')
        self.assertEqual(len(ranked['ranked']), 1)
        self.assertEqual(sorted(reasons), ['x1', next(i for i in ('x2', 'x3') if i != ranked['ranked'][0]['id'])])
        self.assertIn('EQUIVALENT_PARTITION', reasons.values())
        self.assertEqual(ranked['selection_effect'], 'ADVISORY')

    def test_observed_inputs_are_never_repurchased(self):
        rivals = [hyp('a', 'x+1'), hyp('b', '2*x')]
        ranked = model.rank_experiments(rivals, candidates([2]), EXACT, observed=[{'x': Fraction(2)}])
        self.assertEqual(ranked['excluded'], [{'id': 'x2', 'reason': 'ALREADY_OBSERVED'}])

    def test_undefined_prediction_is_its_own_outcome_class(self):
        # Without its own class an UNDEFINED rival looks non-discriminating and never gets tested.
        rivals = [hyp('inverse', '1/x'), hyp('const', '1')]
        classes, undetermined = model.outcome_partition(rivals, {'x': Fraction(0)}, EXACT)
        self.assertIn({'prediction': 'UNDEFINED', 'hypotheses': ['inverse']}, classes)
        self.assertEqual(undetermined, [])
        self.assertGreater(model.information_gain(rivals, {'x': Fraction(0)}, EXACT)['eig_bits'], 0)

    def test_observational_equivalence_on_the_declared_space(self):
        points = [{'x': Fraction(x)} for x in range(-3, 4)]
        self.assertEqual(model.equivalent(hyp('a', 'x+x'), hyp('b', '2*x'), points, EXACT), 'OBSERVATIONALLY_EQUIVALENT')
        self.assertIsNone(model.equivalent(hyp('a', 'x*x'), hyp('b', '2*x'), points, EXACT))


class SynthesisTests(unittest.TestCase):
    def test_counterexample_filter_and_distinct_rivals(self):
        observed = rows(lambda x: x * x + 1, [0, 1, 2, 3])
        result = model.synthesize(observed, ['x'], 'y', EXACT, max_nodes=5, max_candidates=5000,
                                  probes=[{'x': Fraction(x)} for x in range(-4, 5)])
        self.assertEqual(result['status'], 'FIT_FOUND')
        for fit in result['fits']:
            law = model.compile_hypothesis({'id': 'f', 'predict': {'y': fit['expression']}}, ['x'], ['y'])
            self.assertEqual(model.replay(law, observed, EXACT)['status'], 'CONSISTENT')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_exhaustion_is_bound_relative(self):
        observed = rows(lambda x: 7 * x * x * x - 5, [0, 1, 2, 3])
        result = model.synthesize(observed, ['x'], 'y', EXACT, max_nodes=3, max_candidates=500)
        self.assertIn(result['status'], ('NO_FIT', 'SEARCH_TRUNCATED'))
        self.assertEqual(result['limits'], {'max_nodes': 3, 'max_candidates': 500})


class PolynomialCertificateTests(unittest.TestCase):
    """#194: the router's independent checker replays a uniqueness certificate outside the synthesis bound."""

    def state(self, measured, law=lambda x: x * x):
        import rds_compass as compass
        observations = {f'x{x}': {'status': 'MEASURED', 'outputs': {'y': str(law(x))}, 'receipt_sha256': '0' * 64,
                                  'seq': x} for x in measured}
        plan = {'goal': {'observable': 'y'}, 'inputs': {'x': {'integer': True}},
                'tolerances': {'y': {'abs': '0', 'rel': '0'}},
                'candidates': [{'id': f'x{x}', 'inputs': {'x': str(x)}} for x in range(6)]}
        return compass, {'plan': plan, 'observations': observations, 'tools': {}}

    def test_law_agreeing_with_the_certified_interpolant(self):
        compass, state = self.state(range(4))
        result = compass._polynomial_check(state, hyp('square', 'x*x'))
        self.assertEqual(result['status'], 'AGREES_WITH_UNIQUE_POLYNOMIAL_ON_DECLARED_EXPERIMENTS')
        self.assertEqual((result['degree_bound'], result['coefficients'], result['assurance']),
                         (2, ['0', '0', '1'], 'CERTIFICATE_CHECKED'))

    def test_rival_outside_the_bound_that_fits_every_receipt_is_named(self):
        compass, state = self.state(range(3), law=lambda x: x)
        # Equal to x on 0..2 (the product vanishes there), different from x = 3 on.
        wiggle = hyp('wiggle', 'x + x*(x-1)*(x-2)')
        result = compass._polynomial_check(state, wiggle)
        self.assertEqual(result['status'], 'POLYNOMIAL_RIVAL_DISAGREES')
        self.assertEqual(result['witness'], {'candidate': 'x3', 'law': '9', 'polynomial': '3'})


class ProfileTests(unittest.TestCase):
    def test_mean_gain_cannot_mask_a_local_regression(self):
        baseline = {'s1': 10, 's2': 10, 's3': 10, 's4': 10}
        masked = {'s1': 20, 's2': 20, 's3': 20, 's4': 2}  # Mean +5.5 hides an 8-point collapse.
        profile = model.sample_profile(baseline, masked)
        self.assertEqual((profile['worst_sample'], profile['worst_degradation']), ('s4', '8'))
        self.assertGreater(Fraction(profile['mean_gain']), 0)
        self.assertEqual(model.profile_verdict(profile, 1), 'LOCAL_REGRESSION')
        steady = model.sample_profile(baseline, {'s1': 12, 's2': 11, 's3': 10, 's4': 10})
        self.assertEqual(model.profile_verdict(steady, 1), 'ACCEPT')

    def test_lower_is_better_flips_the_sign(self):
        profile = model.sample_profile({'a': 5}, {'a': 3}, higher_is_better=False)
        self.assertEqual((profile['mean_gain'], profile['worst_degradation']), ('2', '0'))


if __name__ == '__main__':
    unittest.main()
