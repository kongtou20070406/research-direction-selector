"""F2: Compass proposes grid-refinement points inside the declared domain.

The critique: rank_experiments only ever consumed the caller's table. The
refinement helper offers the midpoint of every smallest declared gap as a fresh
candidacy for EIG scoring -- the declaration bounds the world, it does not fix
the ruler. Proposed points are scored against live rivals like any candidate;
a declared duplicate is never offered twice.
"""
from fractions import Fraction
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass as compass
import rds_compass_model as model


def _state(xs):
    return {'plan': {'inputs': {'x': {'min': min(xs), 'max': max(xs), 'integer': False}},
                     'candidates': [{'id': 'x%d' % i, 'inputs': {'x': str(x)}, 'cost': '1'}
                                    for i, x in enumerate(xs)]},
            'observations': {}, 'tools': {}}


class RefinementTests(unittest.TestCase):
    def test_midpoints_of_the_smallest_gap_are_proposed(self):
        state = _state([0, 4, 8])
        proposed = compass._refine_candidates(state, state['plan']['candidates'])
        inputs = sorted(model.exact(r['inputs']['x']) for r in proposed)
        self.assertEqual(inputs, [Fraction(2), Fraction(6)])
        self.assertTrue(all(r['proposed'] for r in proposed))

    def test_only_the_smallest_gap_is_refined(self):
        state = _state([0, 1, 8])
        proposed = compass._refine_candidates(state, state['plan']['candidates'])
        self.assertEqual([model.exact(r['inputs']['x']) for r in proposed], [Fraction(1, 2)])

    def test_a_declared_duplicate_is_never_proposed(self):
        state = _state([0, 2, 4])
        proposed = compass._refine_candidates(state, state['plan']['candidates'])
        self.assertEqual(len(proposed), 2)
        # Gap of 1 is the smallest; its midpoints (1/2, 3/2, ...) are undeclared,
        # so refinement keeps proposing -- that is the point of a finer ruler.
        state = _state([0, 1, 2, 3, 4])
        self.assertEqual(len(compass._refine_candidates(state, state['plan']['candidates'])), 4)
        # But a midpoint that IS declared is never offered again.
        state = _state([0, Fraction(1, 2), 2])
        offered = [model.exact(r['inputs']['x']) for r in compass._refine_candidates(state, state['plan']['candidates'])]
        self.assertNotIn(Fraction(1, 2), offered)

    def test_proposed_points_are_scored_by_eig(self):
        hyps = [model.compile_hypothesis({'id': 'lo', 'predict': {'y': 'x'}}, ['x'], ['y']),
                model.compile_hypothesis({'id': 'hi', 'predict': {'y': 'x + 1'}}, ['x'], ['y'])]
        declared = [{'id': 'x%d' % x, 'inputs': {'x': Fraction(x)}, 'cost': Fraction(1)} for x in (0, 4)]
        state = _state([0, 4])
        proposed = [{**r, 'inputs': {k: model.exact(v) for k, v in r['inputs'].items()}}
                    for r in compass._refine_candidates(state, state['plan']['candidates'])]
        tol = {'y': model.tolerance({'abs': 0})}
        ranking = model.rank_experiments(hyps, declared + proposed, tol, observed=[], limit=4)
        self.assertTrue(ranking['ranked'])
        self.assertIn(proposed[0]['id'], [r['id'] for r in ranking['ranked']])


if __name__ == '__main__':
    unittest.main()
