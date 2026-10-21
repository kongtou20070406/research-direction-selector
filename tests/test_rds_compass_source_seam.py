"""A declared source must reach laws the enumerator cannot, and no further.

The size ceiling is structural: `feynman-I.41.16`'s true law has AST size 24 and
`synthesis.max_nodes` is 7, so no budget reaches it.  A source hands in the law
instead; the kernel still has to reproduce every measurement with it.
"""
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / 'scripts', ROOT / 'benchmark' / 'newtonbench', ROOT / 'tests'):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import compass_fixture as fx  # noqa: E402
import rds_compass as compass  # noqa: E402
import rds_compass_declaration as declaration  # noqa: E402
import rds_compass_model as model  # noqa: E402
import source_seam  # noqa: E402


def rows(pairs):
    return [{'id': f'p{index}', 'inputs': {'x': str(x)}, 'outputs': {'y': str(y)}}
            for index, (x, y) in enumerate(pairs)]


TIGHT = {'y': (Fraction(0), Fraction(0))}


class SeedReachTests(unittest.TestCase):
    def test_a_seed_larger_than_the_enumeration_bound_still_fits(self):
        """`x*x*x*x + 1` has AST size 9 while max_nodes is 3: only the seed can deliver it."""
        data = rows([(0, 1), (1, 2), (2, 17), (3, 82)])
        searched = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=3, max_candidates=5000)
        self.assertEqual(searched['fits'], [])
        seeded = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=3, max_candidates=5000,
                                  seeds=['x*x*x*x + 1'])
        self.assertEqual([fit['text'] for fit in seeded['fits']], ['((((x * x) * x) * x) + 1)'])

    def test_a_seed_that_misses_a_measurement_is_not_a_fit(self):
        data = rows([(0, 1), (4, 5)])
        result = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=3, max_candidates=5000,
                                  seeds=['x + 7'])
        self.assertNotIn('x + 7', [fit['text'] for fit in result['fits']])
        self.assertTrue(result['fits'], 'the search itself should still find x + 1')

    def test_an_empty_seed_list_is_the_old_call(self):
        data = rows([(0, 1), (1, 2), (2, 3)])
        plain = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=2, max_candidates=5000)
        same = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=2, max_candidates=5000, seeds=())
        self.assertEqual(plain, same)

    def test_two_spellings_of_one_law_take_one_fit_slot(self):
        """`x + 1` and `x + 0 + 1` are one law: the second must not buy a second slot."""
        data = rows([(0, 1), (1, 2), (2, 3), (3, 4)])
        one = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=7, max_candidates=200000,
                               seeds=['x + 1'])
        two = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=7, max_candidates=200000,
                               seeds=['x + 1', 'x + 0 + 1'])
        self.assertEqual(one['fits'], two['fits'])

    def test_a_seeded_law_does_not_stop_the_enumeration(self):
        """Seeds occupy their own slots: the search must still visit the same candidates.

        Before this guard the seeds were never deduplicated against the search, so three
        equivalent seeds plus one enumerated repeat filled ``keep=4`` at the leaf layer
        and ``attempted`` collapsed from 7381 to 8.
        """
        data = rows([(0, 1), (1, 2), (2, 3), (3, 4)])
        plain = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=7, max_candidates=200000)
        seeded = model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=7, max_candidates=200000,
                                  seeds=['x + 1', '(x*2+2)/2', '(x+2)-1'])
        self.assertEqual(plain['attempted'], seeded['attempted'])

    def test_a_seed_naming_an_undeclared_input_is_refused(self):
        data = rows([(0, 1), (1, 2)])
        with self.assertRaises(ValueError):
            model.synthesize(data, ['x'], 'y', TIGHT, max_nodes=3, max_candidates=100,
                             seeds=['q + 1'])


class SourceBoundaryTests(unittest.TestCase):
    def test_a_fit_keeps_all_source_ids_in_its_origin(self):
        sources = [{'id': 'pysr-3', 'laws': ['x + 1']}, {'id': 'cas-2', 'laws': ['x + 1']}]
        origins = compass._source_origins(sources, ['x'])
        state = {
            'plan': {'goal': {'observable': 'y'}, 'observables': ['y'], 'inputs': ['x'],
                     'tolerances': {'y': {'abs': 0, 'rel': 0}}, 'grammar': {}, 'synthesis': {'max_nodes': 3, 'max_candidates': 10},
                     'sources': sources},
            'tools': {}, 'hypotheses': {},
        }
        fit = {'expression': model.parse('x + 1', {'x'}), 'text': '(1 + x)'}
        with patch.object(compass, '_fits_synthesize',
                          return_value={'fits': [fit], 'status': 'FIT_FOUND', 'attempted': 1}):
            found, _ = compass._fits(state, rows=rows([(0, 1)]), probes=[])
        mode = found[0][1]
        hypothesis = compass._hypothesis_from_fit(state, found[0][0], mode, None, 'r1')
        self.assertEqual(mode, 'SOURCE:["pysr-3","cas-2"]')
        self.assertEqual(hypothesis['origin'], mode)
        self.assertEqual(compass._source_fit_mode({'text': 'x'}, origins), 'WHOLE_LAW')

    def test_source_origin_filter_keeps_ids_after_jump_prefix(self):
        state = {'hypotheses': {
            'h1': {'origin': 'jump:j01:SOURCE:["preregistered-truth"]'},
            'h2': {'origin': 'rival-synthesis:WHOLE_LAW'},
        }}
        self.assertEqual(source_seam.source_origins_from_state(state),
                         ['jump:j01:SOURCE:["preregistered-truth"]'])

    def test_too_many_source_laws_are_refused_at_ingest(self):
        """A declaration is not a sweep: the total number of seeded laws is bounded."""
        laws = ['x + %d' % index for index in range(declaration.MAX_SOURCE_LAWS + 1)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = fx.law_campaign(root, 'x + 1', range(3),
                                   sources=[{'id': 'flood', 'laws': laws}])
            with self.assertRaises(ValueError):
                declaration.initialize(root, path)

    def test_a_source_over_the_rulebook_limit_is_rejected_at_ingest(self):
        terms = (model.MAX_RULEBOOK_NODES + 2) // 2
        law = '+'.join(['x'] * terms)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = fx.law_campaign(root, 'x + 1', range(3),
                                   sources=[{'id': 'oversize', 'laws': [law]}])
            with self.assertRaises(ValueError):
                declaration.initialize(root, path)


if __name__ == '__main__':
    unittest.main()
