"""The open-task card: one question, a few facts, no tool and no model."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass as compass
import rds_compass_store as cstore
from rds_compass_declaration import initialize


DECL = {
    'schema': 'rds-compass-declaration-v1',
    'goal': {'kind': 'OPEN', 'statement': 'Raise the score of the fixed checker without changing it.',
             'observable': 'score'},
    'observables': {'score': {'tolerance': {'abs': '0.01'}, 'higher_is_better': True}},
    'stop': {'rule': 'Stop after the declared trial cap or two identical failures.'},
    'caps': {'max_trials': 4, 'max_agent_calls': 4},
    'budget': {'wall_seconds': 60},
}


class OpenCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def declare(self, extra=None, drop=()):
        value = {**DECL, **(extra or {})}
        for key in drop:
            value.pop(key, None)
        path = self.root / 'compass.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return initialize(self.root, path)

    def test_a_missing_tolerance_stops_without_starting_a_process(self):
        value = json.loads(json.dumps(DECL))
        value['observables']['score'].pop('tolerance')
        path = self.root / 'compass.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(initialize(self.root, path)['status'], 'INITIALIZED')
        card = compass.open_card(compass.fold(cstore.store_for(self.root)))
        self.assertEqual(card['next'], 'STOP')
        self.assertIn('tolerance', card['question'])
        self.assertEqual(card['scientific_support'], 'UNKNOWN')
        self.assertLessEqual(len(json.dumps(card).encode('utf-8')), 4096)

    def test_the_first_card_asks_before_any_tool(self):
        self.assertEqual(self.declare()['status'], 'INITIALIZED')
        card = compass.workset(compass.fold(cstore.store_for(self.root)))
        self.assertEqual(card['next'], 'ANSWER')
        self.assertTrue(card['question'])
        self.assertEqual(card['tried'], [])
        self.assertNotIn('grammar', card)

    def test_recording_does_not_run_and_a_repeat_route_is_reused(self):
        self.declare()
        store = cstore.store_for(self.root)
        note = {'kind': 'trial', 'id': 't1', 'route': 'python checker.py',
                'receipt_sha256': 'a' * 64, 'metric': '0.2'}
        first = compass.record_note(store, note)
        self.assertEqual(first['status'], 'RECORDED')
        self.assertFalse(first['execution_started'])
        self.assertEqual(first['card']['next'], 'RECORD')
        second = compass.record_note(store, {**note, 'id': 't2', 'metric': '0.3'})
        self.assertEqual(second['status'], 'REUSE')
        self.assertEqual(len(second['card']['tried']), 2)

    def test_two_identical_failures_stop_the_card(self):
        self.declare()
        store = cstore.store_for(self.root)
        base = {'kind': 'trial', 'route': 'python checker.py', 'receipt_sha256': 'b' * 64, 'failed': True}
        compass.record_note(store, {**base, 'id': 'f1'})
        second = compass.record_note(store, {**base, 'id': 'f2'})
        self.assertEqual(second['card']['next'], 'STOP')
        self.assertIn('failed twice', second['card']['question'])


if __name__ == '__main__':
    unittest.main()
