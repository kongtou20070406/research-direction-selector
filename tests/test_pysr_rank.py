"""Tests for ranking PySR frontier candidates when replay has nothing to say.

Covers the three required boundaries: a frontier that is entirely REFUTED still
orders, two candidates that predict the same thing on every unmeasured point merge
instead of ranking twice, and an empty frontier yields an empty ranking. Also pins
the two things that would silently rot the criterion: replay status must stay an
evidence column, and a probe point that was already measured must never be probed.
"""
from pathlib import Path
import math
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'benchmark' / 'newtonbench'))

import rds_compass_model as model
import pysr_rank


def measurements(pairs=(('1', '1'), ('2', '2'), ('3', '3'))):
    return [{'id': 'p%d' % index, 'inputs': {'x': model.exact(x), 'z': model.exact(z)},
             'outputs': {'y': model.exact(0)}} for index, (x, z) in enumerate(pairs)]


def frontier(*entries):
    return {'equations': [entry['equation'] for entry in entries], 'results': list(entries)}


class RankCandidatesTests(unittest.TestCase):
    def test_all_refuted_still_ranks(self):
        record = frontier({'equation': 'x', 'rds': '(x)', 'replay': 'REFUTED'},
                          {'equation': 'x + x', 'rds': '(x + x)', 'replay': 'REFUTED'},
                          {'equation': 'z', 'rds': '(z)', 'replay': 'REFUTED'})
        result = pysr_rank.rank_candidates(record, measurements())
        self.assertEqual(result['ranked_total'], 3)
        self.assertEqual([row['replay'] for row in result['ranked']], ['REFUTED'] * 3)
        bits = [row['discrimination_bits'] for row in result['ranked']]
        self.assertEqual(bits, sorted(bits, reverse=True))
        self.assertGreater(bits[0], bits[-1])
        # x is a lone class on all six probes; x+x and z collide on one, so they tie behind it.
        self.assertAlmostEqual(bits[0], 6 * math.log2(3), places=9)
        self.assertAlmostEqual(bits[1], 5 * math.log2(3) + math.log2(3 / 2), places=9)
        measured = [{name: model.text(value) for name, value in row['inputs'].items()}
                    for row in measurements()]
        self.assertEqual(len(result['probes']), 6)
        self.assertTrue(all(probe['inputs'] not in measured for probe in result['probes']))

    def test_equivalent_candidates_merge(self):
        record = frontier({'equation': 'x * 2', 'rds': '(x * 2)', 'replay': 'REFUTED'},
                          {'equation': 'x + x', 'rds': '(x + x)', 'replay': 'REFUTED'},
                          {'equation': 'z * 10', 'rds': '(z * 10)', 'replay': 'REFUTED'})
        result = pysr_rank.rank_candidates(record, measurements())
        self.assertEqual(result['ranked_total'], 2)
        self.assertEqual([row['id'] for row in result['ranked']], ['c2', 'c0'])
        self.assertEqual(result['merged'], [{'id': 'c1', 'equivalent_to': 'c0',
                                             'reason': 'EQUIVALENT_PARTITION'}])

    def test_empty_candidates(self):
        result = pysr_rank.rank_candidates({'equations': [], 'results': []}, measurements())
        self.assertEqual(result['ranked'], [])
        self.assertEqual(result['ranked_total'], 0)
        self.assertEqual(result['merged'], [])
        self.assertEqual(result['note'], 'NO_CANDIDATES')

    def test_recorded_replay_is_evidence_not_key(self):
        record = frontier({'equation': 'x', 'rds': '(x)', 'replay': 'CONSISTENT'},
                          {'equation': 'x + 0.001', 'rds': '(x + 1/1000)', 'replay': 'REFUTED'},
                          {'equation': 'z * 10', 'rds': '(z * 10)', 'replay': 'REFUTED'})
        result = pysr_rank.rank_candidates(record, measurements())
        self.assertEqual([row['id'] for row in result['ranked']], ['c2', 'c0'])
        self.assertEqual(result['ranked'][0]['replay'], 'REFUTED')
        self.assertEqual(result['ranked'][1]['replay'], 'CONSISTENT')
        self.assertEqual(result['merged'][0]['id'], 'c1')

    def test_computed_replay_when_record_omits_it(self):
        record = frontier({'equation': 'x * 10', 'rds': '(x * 10)'})
        result = pysr_rank.rank_candidates(record, measurements())
        self.assertEqual(result['ranked'][0]['replay'], 'REFUTED')
        self.assertEqual(result['ranked'][0]['replay_source'], 'computed')
        self.assertEqual(result['ranked'][0]['counterexamples'], 3)

    def test_no_unmeasured_points_fails_closed(self):
        record = frontier({'equation': 'x', 'rds': '(x)', 'replay': 'REFUTED'})
        result = pysr_rank.rank_candidates(record, measurements(), points=[])
        self.assertEqual(result['ranked'], [])
        self.assertEqual(result['note'], 'NO_UNMEASURED_POINTS')
        self.assertEqual(result['excluded'], [{'id': 'c0', 'reason': 'NO_UNMEASURED_POINTS'}])


if __name__ == '__main__':
    unittest.main()
