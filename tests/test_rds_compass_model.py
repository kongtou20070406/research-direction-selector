"""Compass exact model: EIG over rival partitions, equivalence, synthesis, profiles."""
from fractions import Fraction
import math
from pathlib import Path
import sys
import unittest
from unittest import mock

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


class TranscendentalDialectTests(unittest.TestCase):
    """exp/log/sin/cos/pi and rational powers: rigorous enclosures, never rounded points."""

    REFERENCE = {'exp(1)': '2.71828182845904523536028747135266249', 'log(2)': '0.69314718055994530941723212145817656',
                 'sin(1)': '0.84147098480789650665250232163029899', 'cos(1)': '0.54030230586813971740093660744297660',
                 'sqrt(2)': '1.41421356237309504880168872420969807', '2 ** (1/3)': '1.25992104989487316476721060727822835',
                 'pi': '3.14159265358979323846264338327950288', 'sin(pi / 6)': '0.5'}

    def test_irrational_values_carry_an_enclosure_of_the_true_value(self):
        for source, digits in self.REFERENCE.items():
            value = model.evaluate(model.parse(source), {})
            slack = Fraction(1, 10 ** 35)
            self.assertLess(value.radius, Fraction(1, 10 ** 36), source)
            self.assertTrue(value - value.radius - slack <= Fraction(digits) <= value + value.radius + slack, source)

    def test_exact_points_stay_exact(self):
        cases = {'exp(0)': 1, 'log(1)': 0, 'sin(0)': 0, 'cos(0)': 1, 'sqrt(9/4)': Fraction(3, 2),
                 '4 ** 1.5': 8, '(-8) ** (1/3)': -2, '(1/9) ** (-1/2)': 3}
        for source, expected in cases.items():
            value = model.evaluate(model.parse(source), {})
            self.assertEqual(value, expected, source)
            self.assertNotIsInstance(value, model.Inexact, source)
        self.assertEqual(model.predict(hyp('root', '(-x) ** (1/2)'), {'x': Fraction(4)})[1], 'UNDEFINED')

    def test_every_exact_spelling_reads_back(self):
        x = Fraction('0.123456789012345678')
        cell = [{'id': 'x', 'inputs': {'x': x}, 'outputs': {'y': Fraction(3, 2)}}]
        residual = model.replay(hyp('octic', 'x ** 8 / 7'), cell, EXACT)['counterexamples'][0]['residual']
        self.assertGreater(len(residual), 256)
        self.assertEqual(model.exact(residual), Fraction(3, 2) - x ** 8 / 7)
        widest = Fraction(-(2 ** model.MAX_BITS - 1), 2 ** model.MAX_BITS - 3)
        self.assertEqual(model.exact(model.text(widest)), widest)
        with self.assertRaises(ValueError):  # The spelling bound itself stays.
            model.exact('1' * (len(model.text(widest)) + 1))

    def test_replay_never_decides_a_cell_the_enclosure_straddles(self):
        law = hyp('root2', 'sqrt(2) * x')
        midpoint = Fraction(model.evaluate(law['predict']['y'], {'x': Fraction(1)}))
        cell = [{'id': 'x1', 'inputs': {'x': Fraction(1)}, 'outputs': {'y': midpoint}}]
        self.assertEqual((model.replay(law, cell, EXACT)['status'], model.replay(law, cell, EXACT)['undecided']),
                         ('UNTESTED', 1))
        near = [{'id': 'x1', 'inputs': {'x': Fraction(1)}, 'outputs': {'y': Fraction('1.41421356237')}}]
        self.assertEqual(model.replay(law, near, {'y': model.tolerance({'abs': '1e-10'})})['status'], 'CONSISTENT')
        self.assertEqual(model.replay(law, near, {'y': model.tolerance({'abs': '1e-12'})})['status'], 'REFUTED')

    def test_an_undecidable_branch_is_unknown_not_a_counterexample(self):
        law = hyp('step', 'floor(sin(pi * x))')
        self.assertEqual(model.predict(law, {'x': Fraction(1)}), ({'y': None}, 'UNDECIDED'))
        result = model.replay(law, rows(lambda x: 0, [1]), EXACT)
        self.assertEqual((result['status'], result['counterexamples'], result['undecided']), ('UNTESTED', [], 1))
        self.assertEqual(model.outcome_partition([law, hyp('zero', '0')], {'x': Fraction(1)}, EXACT)[1], ['step'])

    def test_synthesis_reaches_trig_and_fractional_power_laws(self):
        tol = {'y': model.tolerance({'rel': '1e-9'})}
        observed = [{'id': f'x{x}', 'inputs': {'x': Fraction(x)}, 'outputs': {'y': model.exact(math.sin(2 * x))},
                     'receipt_sha256': '0' * 64} for x in range(1, 6)]
        found = model.synthesize(observed, ['x'], 'y', tol, grammar={'constants': [2], 'operators': ['mul', 'sin']},
                                 max_nodes=4, max_candidates=2000)
        self.assertIn('sin((2 * x))', [f['text'] for f in found['fits']])
        observed = [{'id': f'x{x}', 'inputs': {'x': Fraction(x)}, 'outputs': {'y': model.exact(x ** 1.5)},
                     'receipt_sha256': '0' * 64} for x in range(1, 6)]
        found = model.synthesize(observed, ['x'], 'y', tol, grammar={'constants': ['3/2'], 'operators': ['pow']},
                                 max_nodes=3, max_candidates=2000)
        self.assertEqual(found['fits'][0]['text'], '(x ** (3/2))')

    def test_a_rational_stand_in_for_a_trig_law_meets_a_discriminating_experiment(self):
        # The cubic Taylor polynomial passes for sin(x) within 5% near zero; with sin expressible,
        # EIG skips the inputs where the two agree and buys the one that separates them.
        tol = {'y': model.tolerance({'rel': '1/20'})}
        rivals = [hyp('sine', 'sin(x)'), hyp('cubic', 'x - x ** 3 / 6')]
        pool = [{'id': x, 'inputs': {'x': Fraction(x)}} for x in ('1/10', '1/2', '3')]
        ranked = model.rank_experiments(rivals, pool, tol)
        self.assertEqual([row['id'] for row in ranked['ranked']], ['3'])
        self.assertEqual({row['id']: row['reason'] for row in ranked['excluded']},
                         {'1/10': 'NON_DISCRIMINATING', '1/2': 'NON_DISCRIMINATING'})

    def test_copies_keep_the_enclosure(self):
        import copy
        import pickle
        value = model.evaluate(model.parse('exp(1)'), {})
        for clone in (copy.deepcopy(value), pickle.loads(pickle.dumps(value))):
            self.assertEqual((clone, clone.radius), (value, value.radius))


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

    def test_derived_feature_shrinks_the_search(self):
        observed = [{'id': 'a', 'inputs': {'x': model.exact(1), 'y': model.exact(2)},
                     'outputs': {'z': model.exact(6)}},
                    {'id': 'b', 'inputs': {'x': model.exact(2), 'y': model.exact(3)},
                     'outputs': {'z': model.exact(10)}}]
        tol = {'z': (model.exact(0), model.exact(0))}
        result = model.synthesize(observed, ['x', 'y'], 'z', tol,
                                  grammar={'operators': ['mul'], 'constants': ['2']},
                                  max_nodes=4, max_candidates=1000, derived={'s': 'x + y'})
        self.assertEqual(result['status'], 'FIT_FOUND')
        self.assertIn('(2 * s)', [fit['text'] for fit in result['fits']])


class CachedSpanTests(unittest.TestCase):
    """The enumeration reuses child spans; a whole-tree re-evaluation must agree at every point."""

    POINTS = [{'x': Fraction(v)} for v in (0, Fraction(1, 2), 1, 2, 3, 4, 9)]

    def whole(self, node, points):
        out = []
        for env in points:
            try:
                out.append(model._enclose(node, env))
            except (model.DomainError, model.Undecided):
                out.append(None)
        return out

    def composed(self, node, points):
        """The cache's view: leaves are evaluated once, every internal node grows from child spans."""
        if 'const' in node:
            point = Fraction(node['const'])
            return [(point, point)] * len(points)
        if 'var' in node or node['op'] in model.NULLARY or node['op'] in model.TERNARY:
            return self.whole(node, points)  # Not composed by the enumeration, so not cached either.
        held = [self.composed(arg, points) for arg in node['args']]
        out = []
        for index in range(len(points)):
            values = [child[index] for child in held]
            if any(value is None for value in values):
                out.append(None)
                continue
            try:
                out.append(model._step(node['op'], values))
            except (model.DomainError, model.Undecided):
                out.append(None)
        return out

    def test_cached_composition_equals_a_whole_tree_reevaluation(self):
        # sqrt turns points Inexact; dividing by (x - 2) and a half power raise DomainError.
        sources = ('sqrt(x) / (x - 2)', '1 / (x - 2) - sqrt(x)', '(x - 2) ** (1/2)',
                   'sqrt(x) % 2', 'x ** (3/2) + 1 / (x - 2)')
        for source in sources:
            node = model.parse(source)
            self.assertEqual(self.whole(node, self.POINTS), self.composed(node, self.POINTS), source)

    def test_a_span_stays_wide_when_an_argument_is_inexact(self):
        # An Inexact argument must keep the composition an interval; collapsing it to a point is the bug
        # this cache could introduce, so pin the two shapes the enumeration depends on.
        for source, value in (('1 / sqrt(x)', Fraction(1, 2)), ('sqrt(x) - x', Fraction(1, 2)),
                              ('sqrt(x) * sqrt(x)', Fraction(1, 2)), ('(x - 2) ** (1/2)', Fraction(5))):
            lo, hi = model._enclose(model.parse(source), {'x': value})
            self.assertLess(lo, hi, source)
        lo, hi = model._enclose(model.parse('sqrt(x)'), {'x': Fraction(4)})
        self.assertEqual((lo, hi), (Fraction(2), Fraction(2)))

    def test_a_rewritten_candidate_never_reuses_the_pre_rewrite_spans(self):
        # The enumeration hands ``consider`` the spans of the node it built; once ``normalize``
        # rewrites that node the child vectors no longer describe it. Rewrite add(x, 1) into -x and
        # the candidate must be measured as -x: reusing x + 1 would certify the wrong law.
        observed = [{'id': f'x{x}', 'inputs': {'x': Fraction(x)}, 'outputs': {'y': Fraction(x + 1)}}
                    for x in (1, 2, 3)]
        original = model.normalize

        def rewrite(node, variables=None, *, limit=model.MAX_NODES):
            if isinstance(node, dict) and node.get('op') == 'add':
                args = node.get('args', [])
                if {'var': 'x'} in args and {'const': '1'} in args:
                    return {'op': 'neg', 'args': [{'var': 'x'}]}
            return original(node, variables, limit=limit)

        with mock.patch.object(model, 'normalize', rewrite):
            result = model.synthesize(observed, ['x'], 'y', EXACT,
                                      grammar={'operators': ['add'], 'constants': ['1']},
                                      max_nodes=3, max_candidates=400)
        self.assertEqual(result['status'], 'NO_FIT')

    def test_synthesis_over_a_domain_hole_still_reproduces_every_measurement(self):
        observed = [{'id': f'x{x}', 'inputs': {'x': Fraction(x)}, 'outputs': {'y': Fraction(math.isqrt(x))}}
                    for x in (1, 4, 9)]
        result = model.synthesize(observed, ['x'], 'y', EXACT,
                                  grammar={'operators': ['sqrt', 'div'], 'constants': ['1']},
                                  max_nodes=3, max_candidates=3000,
                                  probes=[{'x': Fraction(0)}, {'x': Fraction(2)}])
        self.assertEqual(result['status'], 'FIT_FOUND')
        self.assertIn('sqrt(x)', [fit['text'] for fit in result['fits']])
        law = model.compile_hypothesis({'id': 'f', 'predict': {'y': result['fits'][0]['expression']}},
                                       ['x'], ['y'])
        self.assertEqual(model.replay(law, observed, EXACT)['status'], 'CONSISTENT')


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
