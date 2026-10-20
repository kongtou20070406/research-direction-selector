"""Tests for ranking candidates by continuous relative prediction divergence.

Pins the three properties the continuous criterion exists for: a wide prediction
gap scores higher than a narrow one, near-identical predictions score near zero
without being merged (a small gap is still a real disagreement), and laws whose
RDS partitions coincide merge instead of ranking twice.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'benchmark' / 'newtonbench'))

import rds_compass_model as model
import pysr_rank_continuous


def measurements(pairs=(('1', '1'), ('2', '2'), ('3', '3'))):
    return [{'id': 'p%d' % index, 'inputs': {'x': model.exact(x), 'z': model.exact(z)},
             'outputs': {'y': model.exact(0)}} for index, (x, z) in enumerate(pairs)]


def frontier(*entries):
    return {'equations': [entry['equation'] for entry in entries], 'results': list(entries)}


class ContinuousRankTests(unittest.TestCase):
    def test_wide_gap_scores_higher_than_narrow_gap(self):
        # Pairs: (x, 3x) and (3x, 9x) both gap 2/3; (x, 9x) gaps 8/9.
        record = frontier({'equation': 'x', 'rds': '(x)', 'replay': 'REFUTED'},
                          {'equation': 'x * 3', 'rds': '(x * 3)', 'replay': 'REFUTED'},
                          {'equation': 'x * 9', 'rds': '(x * 9)', 'replay': 'REFUTED'})
        result = pysr_rank_continuous.rank_candidates(record, measurements())
        self.assertEqual(result['ranked_total'], 3)
        self.assertEqual([row['replay'] for row in result['ranked']], ['REFUTED'] * 3)
        # The middle law is equidistant from both others, so it scores lowest.
        self.assertEqual([row['id'] for row in result['ranked']], ['c0', 'c2', 'c1'])
        self.assertAlmostEqual(result['ranked'][0]['divergence'], 7 / 9, places=9)
        self.assertAlmostEqual(result['ranked'][2]['divergence'], 2 / 3, places=9)
        self.assertEqual(result['ranked'][0]['comparable_rivals'], 2)
        self.assertAlmostEqual(result['ranked'][0]['worst_point_gap'], 8 / 9, places=9)

    def test_close_candidates_score_low_without_merging(self):
        # 1% apart, tolerance 0.1%: a real but tiny disagreement, not equivalence.
        record = frontier({'equation': 'x', 'rds': '(x)', 'replay': 'REFUTED'},
                          {'equation': 'x * 1.01', 'rds': '(x * 1.01)', 'replay': 'REFUTED'})
        result = pysr_rank_continuous.rank_candidates(
            record, measurements(), tolerance={'rel': '1/1000'})
        self.assertEqual(result['ranked_total'], 2)
        self.assertEqual(result['merged'], [])
        self.assertGreater(result['ranked'][0]['divergence'], 0.0)
        self.assertAlmostEqual(result['ranked'][0]['divergence'], 0.01 / 1.01, places=9)

    def test_relative_gap_is_scale_free(self):
        gap = pysr_rank_continuous.relative_gap
        self.assertAlmostEqual(float(gap(model.exact('1'), model.exact('10'))), 0.9, places=9)
        self.assertEqual(gap(model.exact('7'), model.exact('7')), 0)
        self.assertLess(float(gap(model.exact('1'), model.exact('1.000001'))), 1e-5)
        # Unclipped: a sign flip reaches 2, so it cannot be mistaken for a 100% error.
        self.assertEqual(gap(model.exact('1'), model.exact('-1')), 2)

    def test_score_invariant_to_common_output_scale(self):
        # Rescaling every law by the same factor is a unit change, not new evidence.
        plain = pysr_rank_continuous.rank_candidates(
            frontier({'equation': 'x', 'rds': '(x)', 'replay': 'REFUTED'},
                     {'equation': 'x * 3', 'rds': '(x * 3)', 'replay': 'REFUTED'},
                     {'equation': 'x * 9', 'rds': '(x * 9)', 'replay': 'REFUTED'}), measurements())
        scaled = pysr_rank_continuous.rank_candidates(
            frontier({'equation': 'x * 1000', 'rds': '(x * 1000)', 'replay': 'REFUTED'},
                     {'equation': 'x * 3000', 'rds': '(x * 3000)', 'replay': 'REFUTED'},
                     {'equation': 'x * 9000', 'rds': '(x * 9000)', 'replay': 'REFUTED'}), measurements())
        self.assertEqual([row['id'] for row in scaled['ranked']],
                         [row['id'] for row in plain['ranked']])
        self.assertEqual([row['divergence'] for row in scaled['ranked']],
                         [row['divergence'] for row in plain['ranked']])

    def test_equivalent_candidates_merge(self):
        # c0 and c1 differ by 1% (< 2% tolerance), so they share a partition and merge.
        # The duplicated law must not dilute c0's score by adding a zero-gap rival.
        entries = [{'equation': 'x', 'rds': '(x)', 'replay': 'REFUTED'},
                   {'equation': 'x * 1.01', 'rds': '(x * 1.01)', 'replay': 'REFUTED'},
                   {'equation': 'z * 10', 'rds': '(z * 10)', 'replay': 'REFUTED'},
                   {'equation': 'z * 100', 'rds': '(z * 100)', 'replay': 'REFUTED'}]
        result = pysr_rank_continuous.rank_candidates(frontier(*entries), measurements())
        self.assertEqual([row['id'] for row in result['ranked']], ['c3', 'c0', 'c2'])
        self.assertEqual(result['merged'], [{'id': 'c1', 'equivalent_to': 'c0',
                                             'reason': 'EQUIVALENT_PARTITION'}])
        by_id = {row['id']: row for row in result['ranked']}
        self.assertAlmostEqual(by_id['c0']['divergence'], 139 / 150, places=9)
        self.assertAlmostEqual(by_id['c2']['divergence'], 53 / 60, places=9)
        self.assertAlmostEqual(by_id['c3']['divergence'], 283 / 300, places=9)
        self.assertAlmostEqual(by_id['c3']['worst_point_gap'], 299 / 300, places=9)
        self.assertEqual(by_id['c0']['comparable_rivals'], 2)
        solo = pysr_rank_continuous.rank_candidates(
            frontier(entries[0], entries[2], entries[3]), measurements())
        self.assertAlmostEqual({row['id']: row['divergence'] for row in solo['ranked']}['c0'],
                               139 / 150, places=9)

    def test_empty_candidates(self):
        result = pysr_rank_continuous.rank_candidates(
            {'equations': [], 'results': []}, measurements())
        self.assertEqual(result['ranked'], [])
        self.assertEqual(result['ranked_total'], 0)
        self.assertEqual(result['merged'], [])
        self.assertEqual(result['note'], 'NO_CANDIDATES')


if __name__ == '__main__':
    unittest.main()
