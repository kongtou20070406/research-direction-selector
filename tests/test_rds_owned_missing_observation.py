"""Synthetic actual-CLI coverage of absent observations versus invalid evidence."""
import json
import unittest
from unittest.mock import patch

import test_rds_owned_advisor as fixture


class MissingObservationCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.OwnedAdvisorCLITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def initialize(self, *, pointer='/score', diagnostic=True, payload=None):
        payload = {'complete_batch': False, 'items': [7]} if payload is None else payload
        script = fixture.SCRIPT.replace(
            'else:\n    score =',
            'elif mode == "negative":\n'
            '    path.write_text(' + repr(json.dumps(payload)) + ', encoding="utf-8")\n'
            'else:\n    score =')

        def policy(value):
            value['observations'][0]['selector']['pointer'] = pointer
            if diagnostic:
                value['graph']['nodes'][1]['executable']['preconditions'] = [
                    {'fact': 'run.baseline.succeeded', 'op': 'eq', 'value': True}]

        with patch.object(fixture, 'SCRIPT', script):
            self.fixture.initialize(mutate_policy=policy)

    def baseline(self):
        return self.fixture.output('project', 'advance', status_codes=(0, 2))

    def test_missing_key_allows_only_frozen_lifecycle_diagnostic_without_rerun(self):
        self.initialize()
        first = self.baseline()
        self.fixture.assert_owned_receipt(first['receipt'], 'baseline', 'SUCCEEDED')
        original = (self.fixture.root / 'outputs/baseline.json').read_bytes()
        state = self.fixture.snapshot()
        report = self.fixture.output('project', 'next', status_codes=(0, 2))
        self.assertEqual(report['status'], 'REVIEWED')
        self.assertEqual(report['selected_run'], 'repair')
        fact = report['context']['facts']['baseline.score']
        self.assertEqual(fact['kind'], 'UNKNOWN')
        self.assertIsNone(fact['value'])
        self.assertFalse(fact['reliable'])
        self.assertEqual(fact['source']['receipt_id'], first['receipt']['sha256'])
        self.assertEqual(fact['source']['path'], 'outputs/baseline.json')
        self.assertEqual(fact['source']['locator'], 'pointer:/score')
        artifact = next(a for a in first['receipt']['artifacts'] if a['path'] == 'outputs/baseline.json')
        self.assertEqual(fact['source']['sha256'], artifact['sha256'])
        self.assertEqual(report['coverage']['errors'], [])
        gap = next(g for g in report['coverage']['gaps'] if g.get('fact') == 'baseline.score')
        self.assertIn('/score', gap['reason'])
        self.assertEqual(report['coverage']['parsed_observations'], 0)
        self.assertEqual(self.fixture.snapshot()['budget'], state['budget'])
        second = self.fixture.output('project', 'advance')
        self.fixture.assert_owned_receipt(second['receipt'], 'repair', 'SUCCEEDED')
        self.assertEqual(self.fixture.starts(), ['baseline', 'repair'])
        self.assertEqual((self.fixture.root / 'outputs/baseline.json').read_bytes(), original)
        self.assertEqual(len(self.fixture.snapshot()['receipts']), 2)
        self.assertEqual(self.fixture.snapshot()['budget']['cpu_seconds']['charged_estimate'], 2)
        final = self.fixture.output('project', 'next')
        self.assertIsNone(final['selected_run'])
        self.assertFalse(final['context']['facts']['baseline.score']['reliable'])
        self.assertNotEqual(final['next_move']['kind'], 'GOAL_CONFIRMED')

    def test_absent_array_element_is_unknown_with_original_pointer(self):
        self.initialize(pointer='/items/1')
        self.baseline()
        report = self.fixture.output('project', 'next', status_codes=(0, 2))
        self.assertEqual(report['status'], 'REVIEWED')
        self.assertEqual(report['selected_run'], 'repair')
        fact = report['context']['facts']['baseline.score']
        self.assertFalse(fact['reliable'])
        self.assertEqual(fact['source']['locator'], 'pointer:/items/1')
        self.assertEqual(report['coverage']['errors'], [])

    def test_route_depending_on_absent_measurement_remains_blocked(self):
        self.initialize(diagnostic=False)
        self.baseline()
        report = self.fixture.output('project', 'next', status_codes=(0, 2))
        self.assertEqual(report['status'], 'REVIEWED')
        self.assertIsNone(report['selected_run'])
        before = self.fixture.snapshot()
        self.fixture.output('project', 'advance', status_codes=(0, 2))
        self.assertEqual(self.fixture.starts(), ['baseline'])
        self.assertEqual(self.fixture.snapshot()['budget'], before['budget'])

    def test_changed_original_still_blocks_independent_diagnostic(self):
        self.initialize()
        self.baseline()
        (self.fixture.root / 'outputs/baseline.json').write_text('{"score": 1}', encoding='utf-8')
        report = self.fixture.output('project', 'next', status_codes=(2,))
        self.assertEqual(report['status'], 'COLLECTION_FAILED')
        self.assertIsNone(report['selected_run'])
        self.assertTrue(report['coverage']['errors'])
        self.fixture.output('project', 'advance', status_codes=(2,))
        self.assertEqual(self.fixture.starts(), ['baseline'])

    def test_invalid_traversal_and_nonscalar_observation_still_fail_closed(self):
        for payload, pointer in (({'items': 7}, '/items/score'), ({'score': [1]}, '/score')):
            with self.subTest(payload=payload, pointer=pointer):
                local = MissingObservationCLITests()
                local.setUp()
                try:
                    local.initialize(payload=payload, pointer=pointer)
                    result = local.baseline()
                    self.assertEqual(result['advisor']['status'], 'COLLECTION_FAILED')
                    self.assertIsNone(result['advisor']['selected_run'])
                    self.assertTrue(result['advisor']['coverage']['errors'])
                    self.assertEqual(local.fixture.starts(), ['baseline'])
                finally:
                    local.doCleanups()


if __name__ == '__main__':
    unittest.main()
