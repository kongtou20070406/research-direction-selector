"""Mocked provider calls, real native execution; no paid model request."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests')]
from prototypes.rds_agent_eval import native_driver as driver
from prototypes.rds_agent_eval.test_study_bridge import StudyBridgeTests
from rds_project import file_sha


class NativeDriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.seed = self.root / 'seed'
        self.seed.mkdir()
        (self.seed / 'task.json').write_text('{"query":"synthetic fixture"}', encoding='utf-8')
        (self.seed / 'table.csv').write_text('synthetic data', encoding='utf-8')
        (self.seed / 'gold.json').write_text('PRIVATE_GOLD', encoding='utf-8')
        self.declaration = {'per_arm_calls': 20, 'deadline_epoch': 1000,
            'completion_protocol': 'finish-function-or-stop-text-v2', 'task_file': 'task.json',
            'agent_files': ['task.json', 'table.csv'], 'model': 'fixture', 'response_model': 'resolved'}
        self.manifest = {'id': 'method', 'argv': ['python', 'worker', 'period_counts', 'out.json'],
                         'outpaths': ['out.json'], 'timeout_seconds': 7.2}
        self.recipe = {'routes': [{'run': self.manifest}], 'budget': {'wall_seconds': 100}}

    def run_response(self, choice):
        raw = json.dumps({'model': 'resolved', 'choices': [choice]}).encode()
        with patch.object(driver.time, 'time', return_value=100), \
             patch.object(driver.urllib.request, 'urlopen', return_value=io.BytesIO(raw)) as opened:
            result = driver.run_arm(self.root / 'arm', self.seed, 'Bare', self.declaration, self.recipe, 'http://fixture')
        return result, opened

    def test_complete_text_retains_raw_request_response_and_private_gold_is_not_sent(self):
        result, opened = self.run_response({'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'answer'}})
        self.assertEqual((result['calls'], result['status'], result['completion_mode']), (1, 'AGENT_FINISHED', 'TEXT'))
        audit = self.root / 'arm/audit'
        self.assertTrue((audit / 'response-0.json').exists())
        self.assertNotIn('PRIVATE_GOLD', (audit / 'request-0.json').read_text(encoding='utf-8'))
        self.assertEqual(opened.call_args.kwargs['timeout'], 100)
        with self.assertRaises(ValueError):
            driver.run_arm(self.root / 'arm', self.seed, 'Bare', self.declaration, self.recipe, 'http://fixture')

    def test_truncated_turn_never_dispatches_and_counts_uncertain_request(self):
        choice = {'finish_reason': 'length', 'message': {'role': 'assistant', 'content': 'truncated'}}
        with patch.object(driver, 'tool', side_effect=AssertionError('Truncated turn dispatched')):
            result, opened = self.run_response(choice)
        self.assertEqual((result['calls'], result['status']), (1, 'UNKNOWN_NO_RETRY'))
        self.assertEqual(opened.call_count, 1)
        self.assertTrue((self.root / 'arm/audit/failure-0.json').exists())

    def test_bare_timeout_preserves_partial_bytes_and_repeated_attempt_never_executes(self):
        with patch.object(driver.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 7.2, b'partial', b'error')) as ran:
            first = driver.tool(self.seed, 'Bare', self.manifest)
            repeated = driver.tool(self.seed, 'Bare', self.manifest)
        self.assertEqual(first['status'], 'TIMEOUT_UNKNOWN')
        self.assertEqual((self.seed / 'audit/method-stdout.bin').read_bytes(), b'partial')
        self.assertEqual(first['phase'], 'BARE_TOOL')
        self.assertTrue(repeated['reused_original'])
        self.assertEqual(ran.call_count, 1)

    def test_real_native_advance_and_original_receipt_feedback_without_rebuy(self):
        fixture = StudyBridgeTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.initialize()
        manifest = fixture.prepared['contract']['advisor_policy']['routes'][0]['manifest']
        result = driver.tool(fixture.store.root, 'RDS', manifest)
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['measurement']['method'], 'period_counts')
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        before = file_sha(fixture.store.path)
        self.assertTrue(driver.tool(fixture.store.root, 'RDS', manifest)['reused_original'])
        self.assertEqual(file_sha(fixture.store.path), before)
