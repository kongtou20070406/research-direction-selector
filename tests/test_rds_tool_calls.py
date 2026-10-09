"""Finite CLI composition checks; no model traffic or scientific evaluation."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_rds_owned_tools as fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore
from rds_tool_calls import SCHEMA, compose, discover


class ToolCallsTests(unittest.TestCase):
    setUp = fixture.OwnedToolsCLITests.setUp
    write = fixture.OwnedToolsCLITests.write
    call = fixture.OwnedToolsCLITests.call
    setup_campaign = fixture.OwnedToolsCLITests.setup_campaign
    rows = fixture.OwnedToolsCLITests.rows

    def request(self, steps=None, **changes):
        value = {'schema': SCHEMA, 'contract_sha256': self.store.snapshot()['contract_sha256'],
                 'max_wall_seconds': 60, 'steps': steps or [{'op': 'execute_tool', 'run_id': 'apply'}]}
        value.update(changes)
        self.write('composition.json', value)
        return value

    def test_empty_discovery_and_limits(self):
        self.assertEqual(discover(self.root, name='missing')['status'], 'CAPABILITY_GAP')
        self.assertFalse(self.store.path.exists())
        for limit in (0, 33, True):
            with self.assertRaises(ValueError):
                discover(self.root, limit=limit)

    def test_default_cli_discloses_only_requested_local_signature(self):
        self.write('source.py', 'def selected(values, *, scale=2):\n    return sum(values) * scale\n')
        self.call('rsi', 'extract', '--source', str(self.root / 'source.py'), '--entry', 'selected', '--name', 'local')
        p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
                            'rsi', 'discover', '--name', 'local'], capture_output=True, text=True,
                           encoding='utf-8', timeout=10, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual(result['tools'][0]['parameters'], [
            {'name': 'values', 'kind': 'positional-or-keyword', 'required': True},
            {'name': 'scale', 'kind': 'keyword-only', 'required': False}])
        self.assertEqual(result['tools'][0]['applicability'], 'NOT_CHECKED')
        self.assertEqual(result['tools'][0]['routes'], [])
        self.assertFalse(result['execution_started'])
        self.assertEqual(discover(self.root, obligation='absent')['status'], 'CAPABILITY_GAP')

    def test_real_cli_composition_and_duplicate_preserve_attempt_budget_and_consumption(self):
        self.setup_campaign(negative_first=True)
        found = self.call('rsi', 'discover', '--obligation', 'task.status')
        self.assertEqual([t['name'] for t in found['tools']], ['squares'])
        self.assertEqual(found['contract_sha256'], self.store.snapshot()['contract_sha256'])
        self.request([{'op': 'status'}, {'op': 'collect'}, {'op': 'execute_tool', 'run_id': 'apply'},
                      {'op': 'collect'}, {'op': 'costs'}])
        result = self.call('project', 'compose-tools', '--request', 'composition.json')
        self.assertEqual(result['status'], 'COMPLETED')
        applied = result['steps'][2]
        self.assertEqual(applied['status'], 'SUCCEEDED')
        self.assertEqual(applied['after']['tool_counts']['consumed'], 1)
        state = self.store.snapshot()
        self.assertEqual(applied['receipt_sha256'], state['receipts'][0]['sha256'])
        self.assertIsNone(result['model_round_trips'])
        self.assertIsNone(result['token_savings'])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        again = self.call('project', 'compose-tools', '--request', 'composition.json')
        self.assertTrue(again['steps'][2]['observed_existing'])
        after = self.store.snapshot()
        self.assertEqual(after['budget'], state['budget'])
        self.assertEqual(after['runs'], state['runs'])
        self.assertEqual(len(self.rows('checkpoints')), 2)
        self.assertEqual(sum(e['kind'] == 'TOOL_RESULT_CONSUMED' for e in self.rows('events')), 1)
        original = self.root / result['original']['path']
        self.assertEqual(json.loads(original.read_text(encoding='utf-8'))['status'], 'COMPLETED')

    def test_entire_request_rejected_before_any_dispatch(self):
        self.setup_campaign()
        initial = self.store.snapshot()
        good = self.request()
        bad = [dict(good, schema='other'), dict(good, contract_sha256='0' * 64),
               dict(good, max_wall_seconds=True), dict(good, max_wall_seconds=301),
               dict(good, steps=[]), dict(good, steps=[{'op': 'status'}] * 17),
               dict(good, steps=good['steps'] * 2),
               dict(good, steps=good['steps'] + [{'op': 'shell', 'code': 'print(1)'}]),
               dict(good, steps=good['steps'] + [{'op': 'collect', 'argv': ['python']}]),
               dict(good, steps=[{'op': 'execute_tool', 'run_id': 'new-id'}])]
        for request in bad:
            with self.subTest(request=request):
                self.write('composition.json', request)
                with self.assertRaises(ValueError):
                    compose(self.root, 'composition.json')
        self.assertEqual(self.store.snapshot(), initial)

    def test_stale_input_stops_and_cli_signals_handoff(self):
        self.setup_campaign()
        self.request([{'op': 'collect'}, {'op': 'execute_tool', 'run_id': 'apply'}])
        initial = self.store.snapshot()
        self.write('inputs.json', [{'args': [[100]], 'kwargs': {}}])
        p = self.call('project', 'compose-tools', '--request', 'composition.json', ok=False)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual(result['status'], 'HANDOFF')
        self.assertEqual(result['steps'][0]['status'], 'UNKNOWN')
        self.assertIn('binding changed', result['steps'][0]['error'])
        self.assertEqual(result['remaining_steps'], 1)
        self.assertEqual(self.store.snapshot()['runs'], initial['runs'])
        self.assertEqual(self.store.snapshot()['budget'], initial['budget'])

    def test_unqualified_route_does_not_dispatch(self):
        self.setup_campaign(qualification=False)
        self.request()
        initial = self.store.snapshot()
        result = compose(self.root, 'composition.json')
        self.assertEqual(result['status'], 'HANDOFF')
        self.assertEqual(result['steps'][0]['status'], 'NOT_DISPATCHED')
        self.assertEqual(self.store.snapshot()['budget'], initial['budget'])
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_original_budget_remains_authoritative(self):
        self.setup_campaign(initialize=False)
        self.contract['budget']['wall_seconds'] = 10
        self.write('contract.json', self.contract)
        self.call('project', 'init', '--contract', str(self.root / 'contract.json'))
        self.request(max_wall_seconds=300)
        result = compose(self.root, 'composition.json')
        self.assertEqual(result['status'], 'HANDOFF')
        self.assertEqual(result['steps'][0]['status'], 'NOT_DISPATCHED')
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_window_and_nonselected_route_stop_before_reservation(self):
        self.setup_campaign()
        self.request(max_wall_seconds=0.001)
        initial = self.store.snapshot()
        result = compose(self.root, 'composition.json')
        self.assertEqual(result['status'], 'HANDOFF')
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.store.snapshot()['budget'], initial['budget'])
        self.request()
        from rds_owned_advisor import review
        advice = review(self.store)
        advice['selected_run'] = 'a-different-selection'
        with patch('rds_owned_advisor.review', return_value=advice):
            result = compose(self.root, 'composition.json')
        self.assertEqual(result['steps'][0]['status'], 'NOT_DISPATCHED')
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_completed_attempt_with_missing_output_is_not_reexecuted(self):
        self.setup_campaign()
        self.request()
        self.assertEqual(compose(self.root, 'composition.json')['status'], 'COMPLETED')
        initial = self.store.snapshot()
        (self.root / 'outputs/application.json').unlink()
        result = compose(self.root, 'composition.json')
        self.assertEqual(result['status'], 'HANDOFF')
        self.assertTrue(result['steps'][0]['observed_existing'])
        self.assertEqual(result['steps'][0]['after']['status'], 'COLLECTION_FAILED')
        self.assertEqual(self.store.snapshot()['runs'], initial['runs'])
        self.assertEqual(self.store.snapshot()['budget'], initial['budget'])

    def test_uncertain_dispatch_recovers_original_attempt_without_retry(self):
        self.setup_campaign()
        self.request()
        # Inject loss after the real admission transaction assigned its attempt.
        with patch.object(ProjectStore, '_execute_claim', side_effect=OSError('injected controller loss')):
            result = compose(self.root, 'composition.json')
        self.assertEqual(result['status'], 'HANDOFF')
        initial = self.store.snapshot()['runs'][0]
        self.assertIsNotNone(initial['attempt_id'])
        active = compose(self.root, 'composition.json')
        self.assertTrue(active['steps'][0]['observed_existing'])
        self.assertEqual(active['status'], 'HANDOFF')
        self.assertEqual(self.store.snapshot()['receipts'], [])
        with patch('rds_project._alive', return_value=False):
            recovered = compose(self.root, 'composition.json')
        self.assertEqual(recovered['steps'][0]['status'], 'INTERRUPTED')
        self.assertEqual(recovered['steps'][0]['attempt_id'], initial['attempt_id'])
        final = self.store.snapshot()
        self.assertEqual(len(final['receipts']), 1)
        self.assertFalse((self.root / 'outputs/application.json').exists())
        compose(self.root, 'composition.json')
        self.assertEqual(self.store.snapshot()['budget'], final['budget'])
        self.assertEqual(self.store.snapshot()['receipts'], final['receipts'])


if __name__ == '__main__':
    unittest.main()
