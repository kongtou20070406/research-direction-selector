"""A sweep result is only an acceptance signal if a repeat can be compared to it.

The three findings here came from an independent review of the stage 2 protocol:
wall-clock and temp paths differ every run, a crashed cell used to read exactly
like an honest null, and an empty sweep used to read like "no false
certifications were found". Each is a fail-open in its own way, so each gets a
test that fails when the property is removed.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'benchmark' / 'newtonbench'))
import devset_run as runner
import pareto


def run(equation, budget, status=None, error=None):
    row = {'equation': equation, 'B': budget, 'seconds': 4.1, 'claim_exact': False}
    if status is not None:
        row['status'] = status
    if error is not None:
        row['error'] = error
    return row


class ComparableTests(unittest.TestCase):
    def test_the_unrepeatable_fields_are_the_only_ones_dropped(self):
        a = {'schema': 1, 'campaign_root': '/tmp/a', 'elapsed_seconds': 1.0,
             'runs': [{**run('feynman-I.12.1', 8, 'GOAL_IDENTIFIED'), 'seconds': 4.1}]}
        b = {'schema': 1, 'campaign_root': '/tmp/b', 'elapsed_seconds': 9.0,
             'runs': [{**run('feynman-I.12.1', 8, 'GOAL_IDENTIFIED'), 'seconds': 5.2}]}
        self.assertEqual(runner.comparable(a), runner.comparable(b))

    def test_a_changed_verdict_still_differs(self):
        a = {'runs': [run('feynman-I.12.1', 8, 'GOAL_IDENTIFIED')]}
        b = {'runs': [run('feynman-I.12.1', 8, 'SEARCH_EXHAUSTED')]}
        self.assertNotEqual(runner.comparable(a), runner.comparable(b))

    def test_a_crashed_row_is_not_stripped_into_a_match(self):
        a = {'runs': [run('feynman-I.12.1', 8, 'GOAL_IDENTIFIED')]}
        b = {'runs': [run('feynman-I.12.1', 8, error='RuntimeError: boom')]}
        self.assertNotEqual(runner.comparable(a), runner.comparable(b))


class DegradedSweepTests(unittest.TestCase):
    def test_an_empty_sweep_is_not_a_clean_verdict(self):
        self.assertEqual(runner.summarize([])['false_certification_status'], 'NO_RUNS')

    def test_a_sweep_that_lost_most_cells_reports_degraded(self):
        rows = [run('e', 8, error='RuntimeError: boom') for _ in range(3)]
        summary = runner.summarize(rows)
        self.assertEqual(summary['degraded_count'], 3)
        self.assertEqual(summary['false_certification_status'], 'DEGRADED')
        self.assertIsNone(summary['false_certification_rate'])

    def test_one_lost_cell_does_not_outvote_a_real_sweep(self):
        rows = [run('e', 8, 'GOAL_IDENTIFIED'), run('e', 16, 'GOAL_IDENTIFIED'),
                run('e', 32, 'GOAL_IDENTIFIED'), run('d', 8, error='boom')]
        for row in rows[:3]:
            row['claimed'] = True
            row['false_certification'] = False
        summary = runner.summarize(rows)
        self.assertEqual(summary['degraded_count'], 1)
        self.assertEqual(summary['false_certification_status'], 'ZERO_FALSE_CERTIFICATIONS')

    def test_a_zero_claim_sweep_still_reads_as_no_claims(self):
        rows = [run('e', 8, 'SEARCH_EXHAUSTED')]
        for row in rows:
            row['claimed'] = False
            row['false_certification'] = None
        self.assertEqual(runner.summarize(rows)['false_certification_status'], 'NO_CLAIMS')


class ParetoComparabilityTests(unittest.TestCase):
    def test_an_empty_pareto_file_is_not_a_clean_verdict(self):
        self.assertEqual(pareto.summarize([])['false_certification_status'], 'NO_RUNS')


if __name__ == '__main__':
    unittest.main()
