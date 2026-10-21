"""Tests for the inverse-decomposition search path."""
import sys
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rds_compass_decompose as decompose_module  # noqa: E402
import rds_compass_model as model  # noqa: E402

ZERO = model.tolerance({'abs': 0, 'rel': 0})


def _point(**values):
    return {'id': 'p' + str(len(values)), 'inputs': {k: str(v) for k, v in values.items()},
            'outputs': {'y': None}}


def _rows(pairs, observable='y'):
    rows = []
    for index, (inputs, output) in enumerate(pairs):
        rows.append({'id': 'pt_%d' % index,
                     'inputs': {k: str(v) for k, v in inputs.items()},
                     'outputs': {observable: str(output)}})
    return rows


def _pairwise(names):
    derived = {}
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            derived[left + '_plus_' + right] = left + ' + ' + right
            derived[left + '_times_' + right] = left + ' * ' + right
    return derived


def _grammar(names, operators, constants=('0', '1', '2', '-1')):
    return {'variables': list(names), 'constants': list(constants),
            'operators': sorted(set(operators))}


def _all_fits_match(rows, result, observable='y', derived=None):
    """Every reported fit must reproduce every observed point inside the zero tolerance."""
    points = [{k: model.exact(v) for k, v in row['inputs'].items()} for row in rows]
    if derived:
        nodes = {name: model.parse(expr, set(points[0])) for name, expr in derived.items()}
        expanded = []
        for env in points:
            values = {}
            for name, node in nodes.items():
                try:
                    values[name] = model._evaluate(node, env)
                except (model.DomainError, model.Undecided):
                    pass
            expanded.append({**env, **values})
        points = expanded
    targets = [model.exact(row['outputs'][observable]) for row in rows]
    for fit in result['fits']:
        for env, target in zip(points, targets):
            value = model._evaluate(fit['expression'], env)
            if not model.matches(value, target, ZERO):
                return False
    return True


BINARY_ONLY = ('add', 'sub', 'mul', 'div')


class InverseDecompositionTests(unittest.TestCase):
    def test_exact_rational_law_is_found_at_size_five(self):
        """(a*b + c*d) / (a+b) needs the size-5 tree bottom-up cannot reach at half=3."""
        names = ['a', 'b', 'c', 'd']
        derived = _pairwise(names)
        rows = _rows([({'a': 3, 'b': 2, 'c': 1, 'd': 4}, Fraction(2, 1)),
                      ({'a': 2, 'b': 5, 'c': 3, 'd': 1}, Fraction(13, 7)),
                      ({'a': 4, 'b': 1, 'c': 1, 'd': 3}, Fraction(7, 5)),
                      ({'a': 5, 'b': 3, 'c': 4, 'd': 2}, Fraction(23, 8))])
        result = decompose_module.decompose(
            rows, names, 'y', {'y': ZERO}, grammar=_grammar(names, BINARY_ONLY),
            max_nodes=7, max_candidates=200000, derived=derived)
        self.assertEqual(result['status'], 'FIT_FOUND')
        self.assertEqual(result['mode'], 'INVERSE_DECOMPOSITION')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertIn(5, [fit['size'] for fit in result['fits']])
        self.assertTrue(_all_fits_match(rows, result, derived=derived))
        law = [fit for fit in result['fits'] if fit['size'] == 5][0]
        for fragment in ('a_times_b', 'c_times_d', 'a_plus_b'):
            self.assertIn(fragment, law['text'])
        self.assertEqual(law['correction'], None)

    def test_division_inverse_skips_undefined_points(self):
        """A 0/0 in the inverse must skip that side, not raise."""
        names = ['a', 'b']
        rows = _rows([({'a': 0, 'b': 1}, Fraction(0, 1)),
                      ({'a': 3, 'b': 2}, Fraction(3, 2)),
                      ({'a': 5, 'b': 4}, Fraction(5, 4))])
        result = decompose_module.decompose(
            rows, names, 'y', {'y': ZERO}, grammar=_grammar(names, BINARY_ONLY),
            max_nodes=5, max_candidates=200000)
        self.assertEqual(result['status'], 'FIT_FOUND')
        self.assertTrue(_all_fits_match(rows, result))
        self.assertGreater(result['notes']['lookups'], 0)

    def test_wrong_inverse_is_rejected_by_whole_tree_verification(self):
        """Sabotage the sub inverse; the safety net must drop the bogus candidate."""
        names = ['a', 'b']
        rows = _rows([({'a': 1, 'b': 1}, Fraction(0, 1)),
                      ({'a': 3, 'b': 2}, Fraction(1, 1))])
        grammar = _grammar(names, BINARY_ONLY)
        clean = decompose_module.decompose(rows, names, 'y', {'y': ZERO}, grammar=grammar,
                                           max_nodes=5, max_candidates=200000)
        self.assertEqual(clean['status'], 'FIT_FOUND')
        self.assertTrue(_all_fits_match(rows, clean))

        honest = decompose_module._inverse

        def sabotaged(op, other_is_left, targets, other_values):
            if op == 'sub' and not other_is_left:
                return tuple(other - target for target, other in zip(targets, other_values))
            return honest(op, other_is_left, targets, other_values)

        with mock.patch.object(decompose_module, '_inverse', side_effect=sabotaged) as spy:
            sabotaged_result = decompose_module.decompose(
                rows, names, 'y', {'y': ZERO}, grammar=grammar,
                max_nodes=5, max_candidates=200000)
        self.assertGreater(spy.call_count, 0)
        self.assertTrue(_all_fits_match(rows, sabotaged_result))
        self.assertFalse(any(fit['text'] == '(1 - b)' for fit in sabotaged_result['fits']))

    def test_verify_rejects_a_candidate_that_misses_one_point(self):
        """The verification step itself is the safety net: point 2 does not match."""
        rows = _rows([({'a': 1, 'b': 1}, Fraction(0, 1)),
                      ({'a': 3, 'b': 2}, Fraction(1, 1))])
        points = [{k: model.exact(v) for k, v in row['inputs'].items()} for row in rows]
        targets = [model.exact(row['outputs']['y']) for row in rows]
        bogus = {'op': 'sub', 'args': [{'const': '1'}, {'var': 'b'}]}
        self.assertIsNone(decompose_module._verify(
            'sub', {'const': '1'}, {'var': 'b'}, points, targets, 2, ZERO, 5, set('ab')))
        honest = decompose_module._verify(
            'sub', {'var': 'a'}, {'var': 'b'}, points, targets, 2, ZERO, 5, set('ab'))
        self.assertIsNotNone(honest)
        self.assertEqual(model.render(honest[0]), '(a - b)')

    def test_inexact_targets_are_refused_rather_than_rounded(self):
        names = ['a', 'b']
        inexact = model._evaluate(model.parse('sqrt(2)', set()), {})
        self.assertTrue(hasattr(inexact, 'radius'))
        rows = [{'id': 'pt_0', 'inputs': {'a': '1', 'b': '2'},
                 'outputs': {'y': inexact}}]
        result = decompose_module.decompose(
            rows, names, 'y', {'y': ZERO}, grammar=_grammar(names, BINARY_ONLY),
            max_nodes=5, max_candidates=200000)
        self.assertEqual(result['status'], 'INEXACT_UNSUPPORTED')
        self.assertEqual(result['fits'], [])
        self.assertEqual(result['notes']['pools'], {})

    def test_lookups_stay_far_below_the_size_five_space(self):
        """The probe count must reflect hashing, not bottom-up enumeration of size 5."""
        names = ['a', 'b', 'c', 'd']
        derived = _pairwise(names)
        rows = _rows([({'a': 3, 'b': 2, 'c': 1, 'd': 4}, Fraction(2, 1)),
                      ({'a': 2, 'b': 5, 'c': 3, 'd': 1}, Fraction(13, 7)),
                      ({'a': 4, 'b': 1, 'c': 1, 'd': 3}, Fraction(7, 5)),
                      ({'a': 5, 'b': 3, 'c': 4, 'd': 2}, Fraction(23, 8))])
        result = decompose_module.decompose(
            rows, names, 'y', {'y': ZERO}, grammar=_grammar(names, BINARY_ONLY),
            max_nodes=7, max_candidates=200000, derived=derived)
        third = result['notes']['pools'][3]
        leaves = len(names) + 4 + len(derived) + 1
        self.assertLess(result['notes']['lookups'], third * leaves * len(BINARY_ONLY))
        self.assertGreater(result['notes']['lookups'], 0)
        self.assertLess(result['notes']['lookups'], 200000)

    def test_truncated_pool_is_reported_as_truncated_not_no_fit(self):
        names = ['a', 'b', 'c', 'd']
        derived = _pairwise(names)
        rows = _rows([({'a': 3, 'b': 2, 'c': 1, 'd': 4}, Fraction(2, 1)),
                      ({'a': 2, 'b': 5, 'c': 3, 'd': 1}, Fraction(13, 7))])
        result = decompose_module.decompose(
            rows, names, 'y', {'y': ZERO}, grammar=_grammar(names, BINARY_ONLY),
            max_nodes=7, max_candidates=8, derived=derived)
        self.assertEqual(result['status'], 'SEARCH_TRUNCATED')
        self.assertEqual(result['fits'], [])


if __name__ == '__main__':
    unittest.main()
