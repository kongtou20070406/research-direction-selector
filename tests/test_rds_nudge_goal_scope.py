import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rds_nudge as nudge


def notes(*items):
    return [dict(id=f'n{i}', type=kind, text=f'{kind} {i}', **fields) for i, (kind, fields) in enumerate(items, 1)]


def rule(items, name):
    return [row for row in nudge.evaluate(items) if row['rule'] == name]


def is_open(items, name):
    return any(row['status'] == 'OPEN' for row in rule(items, name))


class GoalScope(unittest.TestCase):
    def test_proxy_ignores_observations_before_the_goal(self):
        items = notes(('observe', dict(metric='lat', value=1)), ('observe', dict(metric='lat', value=2)),
                      ('goal', dict(metric='acc')))
        self.assertFalse(is_open(items, 'proxy_metric'))

    def test_proxy_still_fires_after_the_goal(self):
        items = notes(('goal', dict(metric='acc')), ('observe', dict(metric='lat', value=1)),
                      ('observe', dict(metric='lat', value=2)))
        self.assertTrue(is_open(items, 'proxy_metric'))

    def test_closing_one_proxy_episode_leaves_the_next_one_open(self):
        first = notes(('goal', dict(metric='acc')), ('observe', dict(metric='lat', value=1)),
                      ('observe', dict(metric='lat', value=2)))
        key = rule(first, 'proxy_metric')[0]['key']
        items = notes(('goal', dict(metric='acc')), ('observe', dict(metric='lat', value=1)),
                      ('observe', dict(metric='lat', value=2)), ('dismiss', dict(key=key, reason='fine here')),
                      ('observe', dict(metric='acc', value=1)), ('observe', dict(metric='lat', value=3)),
                      ('observe', dict(metric='lat', value=4)))
        self.assertTrue(is_open(items, 'proxy_metric'))

    def test_proxy_key_is_stable_while_the_episode_grows(self):
        two = notes(('goal', dict(metric='acc')), ('observe', dict(metric='lat', value=1)),
                    ('observe', dict(metric='lat', value=2)))
        three = two + notes(('observe', dict(metric='lat', value=3)))
        three[-1]['id'] = 'n4'
        self.assertEqual(rule(two, 'proxy_metric')[0]['key'], rule(three, 'proxy_metric')[0]['key'])

    def test_endpoint_of_an_old_goal_does_not_cover_a_new_goal(self):
        items = notes(('goal', {}), ('observe', dict(metric='a', value=1, endpoint=True)), ('goal', {}),
                      ('observe', dict(metric='a', value=2)), ('observe', dict(metric='a', value=3)),
                      ('observe', dict(metric='a', value=4)))
        self.assertTrue(is_open(items, 'endpoint_unchecked'))

    def test_endpoint_after_the_goal_still_closes_the_rule(self):
        items = notes(('goal', {}), ('observe', dict(metric='a', value=1)), ('observe', dict(metric='a', value=2)),
                      ('observe', dict(metric='a', value=3, endpoint=True)))
        self.assertFalse(is_open(items, 'endpoint_unchecked'))

    def test_scale_gap_ignores_observations_before_the_goal(self):
        items = notes(('observe', dict(metric='a', value=1, scale='lab')), ('goal', dict(scale='prod')))
        self.assertFalse(is_open(items, 'scale_gap'))

    def test_scale_gap_still_fires_after_the_goal(self):
        items = notes(('goal', dict(scale='prod')), ('observe', dict(metric='a', value=1, scale='lab')))
        self.assertTrue(is_open(items, 'scale_gap'))


class ExpectationScope(unittest.TestCase):
    def test_newer_expectation_without_tolerance_replaces_the_older_one(self):
        items = notes(('expect', dict(metric='m', value=0, tolerance=1)), ('expect', dict(metric='m', value=100)),
                      ('observe', dict(metric='m', value=100)))
        self.assertFalse(is_open(items, 'expect_mismatch'))
        self.assertTrue(nudge.status(items)['unknown'])

    def test_latest_expectation_with_tolerance_still_fires(self):
        items = notes(('expect', dict(metric='m', value=0, tolerance=1)), ('expect', dict(metric='m', value=50, tolerance=1)),
                      ('observe', dict(metric='m', value=100)))
        self.assertTrue(is_open(items, 'expect_mismatch'))


if __name__ == '__main__':
    unittest.main()
