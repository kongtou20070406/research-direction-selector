"""Same-ledger qualification/init exclusion and original-attempt recovery."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_tools as tools
from rds_project import ProjectStore


class ToolPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-preparation-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = ProjectStore(self.root)
        self.write('source.py', 'def add(a, b):\n    return a + b\n')
        self.write('code.py', 'print("bounded fixture")\n')
        self.write('config.json', '{}')
        self.write('data.json', '[1]')
        self.write_json('cases.json', [{'args': [1, 2], 'expected': 3}])
        tools.extract(self.root, self.root / 'source.py', 'add', 'add-v1')
        files = [('code', 'code.py'), ('config', 'config.json'), ('data', 'data.json'),
                 ('evaluator', 'cases.json')]
        protocol = {'code_sha256': self.sha('code.py'), 'config_sha256': self.sha('config.json'),
                    'data_sha256': self.sha('data.json'), 'data_split': 'software-fixture',
                    'init': 'none', 'seed': 0, 'checkpoint': 'none', 'schedule': 'one invocation',
                    'sample_work': {'cases': 1}, 'numeric_protocol': 'Python integer'}
        self.write_json('protocol.json', protocol)
        files.append(('protocol', 'protocol.json'))
        argv = [sys.executable, '-B', 'code.py']
        action = {'id': 'noop', 'kind': 'PAIRED_TEST', 'target': 'run.noop.succeeded',
                  'operation': 'bounded-fixture', 'description': 'Run the software fixture',
                  'competing_explanations': ['returns zero', 'returns nonzero'],
                  'required_observables': ['run.noop.succeeded'],
                  'outcomes': [{'observation': 'zero', 'next_decision': 'inspect successful receipt'},
                               {'observation': 'nonzero', 'next_decision': 'inspect failed receipt'}]}
        manifest = {'schema': 1, 'id': 'noop', 'arm': 'tool', 'control_id': None,
                    'protocol': {'path': 'protocol.json', 'sha256': self.sha('protocol.json')},
                    'argv': argv, 'outpaths': [], 'resource_estimates': {'wall_seconds': 1}, 'timeout_seconds': 1}
        self.contract = {'schema': 1,
                         'bindings': [{'role': role, 'path': name, 'sha256': self.sha(name)} for role, name in files],
                         'allowed_commands': [argv], 'output_roots': ['outputs'], 'budget': {'wall_seconds': 20},
                         'advisor_policy': {'schema': 1,
                                            'context': {'decision': {'id': 'next', 'goal_revision': 'fixture-v1',
                                                                     'scope': {'domain': 'software-fixture'},
                                                                     'goal_conditions': [{'fact': 'run.noop.succeeded', 'op': 'eq', 'value': True}]}},
                                            'graph': {'nodes': [{'id': 'noop', 'sources': ['fixture'],
                                                                'executable': {'decisions': ['next'], 'preconditions': [],
                                                                               'action': action}}], 'edges': []},
                                            'routes': [{'candidate': 'noop', 'manifest': manifest}],
                                            'observations': [], 'tool_bindings': []}}

    def write(self, name, raw):
        (self.root / name).write_text(raw, encoding='utf-8')

    def write_json(self, name, value):
        self.write(name, json.dumps(value, allow_nan=False))

    def sha(self, name):
        return hashlib.sha256((self.root / name).read_bytes()).hexdigest()

    def qualify(self, name='add-v1'):
        return tools.validate(self.root, name, self.root / 'cases.json', timeout=3)

    def intents(self):
        with self.store._db(True) as db:
            return [json.loads(row['body']) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_STARTED'")]

    def native_receipt(self):
        job = self.root / self.intents()[0]['request']['job_root']
        return ProjectStore(job).snapshot()['receipts'][0]

    def assert_not_initialized(self):
        with self.store._db(True) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM contract WHERE id=1').fetchone())

    def test_failed_init_empty_contract_table_still_allows_original_qualification_retry(self):
        qualified = self.qualify()
        receipt = self.native_receipt()
        too_small = deepcopy(self.contract)
        too_small['budget']['wall_seconds'] = 0
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            self.store.initialize(too_small)
        self.assert_not_initialized()
        retried = self.qualify()
        self.assertEqual(retried['id'], qualified['id'])
        self.assertFalse(retried['execution_started'])
        self.assertEqual(self.native_receipt(), receipt)
        self.assertEqual(len(self.intents()), 1)
        state = self.store.initialize(self.contract)
        self.assertEqual(state['budget']['wall_seconds']['charged_estimate'], receipt['resources']['wall_seconds']['measured'])

    def test_post_child_interruption_blocks_init_and_retry_preserves_receipt_and_attempt(self):
        original_put = tools.put
        def interrupt(*args, **kwargs):
            if args[2] == 'tool-validation':
                raise RuntimeError('synthetic interruption after child settlement')
            return original_put(*args, **kwargs)
        with patch('rds_tools.put', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'after child settlement'):
                self.qualify()
        receipt = self.native_receipt()
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.store.initialize(self.contract)
        self.assert_not_initialized()
        retried = self.qualify()
        self.assertFalse(retried['execution_started'])
        self.assertEqual(self.native_receipt()['sha256'], receipt['sha256'])
        self.assertEqual(self.native_receipt()['attempt_id'], receipt['attempt_id'])
        state = self.store.initialize(self.contract)
        self.assertEqual(state['budget']['wall_seconds']['charged_estimate'], receipt['resources']['wall_seconds']['measured'])
        again = self.store.initialize(self.contract)
        self.assertEqual(again['budget'], state['budget'])

    def test_qualification_marker_wins_concurrent_init_and_no_cost_is_omitted(self):
        entered, release = threading.Event(), threading.Event()
        original_execute = tools.execute
        def paused(args):
            entered.set()
            if not release.wait(15):
                raise RuntimeError('synchronized qualifier timed out')
            return original_execute(args)
        with ThreadPoolExecutor(max_workers=1) as pool, patch('rds_tools.execute', side_effect=paused):
            pending = pool.submit(self.qualify)
            self.assertTrue(entered.wait(15))
            try:
                with self.assertRaisesRegex(ValueError, 'unresolved'):
                    self.store.initialize(self.contract)
                self.assert_not_initialized()
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=15)['status'], 'LOCAL_CASES_PASSED')
        receipt = self.native_receipt()
        state = self.store.initialize(self.contract)
        self.assertEqual(state['budget']['wall_seconds']['charged_estimate'], receipt['resources']['wall_seconds']['measured'])

    def test_init_wins_concurrent_qualification_and_refuses_before_launch(self):
        entered, release = threading.Event(), threading.Event()
        original_begin = tools._begin_preparation
        def paused(*args):
            entered.set()
            if not release.wait(15):
                raise RuntimeError('synchronized init timed out')
            return original_begin(*args)
        with ThreadPoolExecutor(max_workers=1) as pool, patch('rds_tools._begin_preparation', side_effect=paused), \
                patch('rds_tools.execute', wraps=tools.execute) as launch:
            pending = pool.submit(self.qualify)
            self.assertTrue(entered.wait(15))
            try:
                self.store.initialize(self.contract)
            finally:
                release.set()
            with self.assertRaisesRegex(ValueError, 'frozen budget'):
                pending.result(timeout=15)
            launch.assert_not_called()
        self.assertEqual(self.intents(), [])
        self.assertFalse((self.root / '.rds/rsi/tool-checks').exists())

    def test_legacy_pre_intent_orphan_job_blocks_init_until_original_receipt_reconciled(self):
        qualified = self.qualify()
        receipt = self.native_receipt()
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Simulate a pre-intent version that completed its child and died
            # before publishing. Production records remain append-only.
            db.execute('DROP TRIGGER events_no_delete')
            db.execute('DROP TRIGGER research_records_no_delete')
            db.execute("DELETE FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_STARTED'")
            db.execute('DELETE FROM research_records WHERE id=?', (qualified['id'],))
        with self.assertRaisesRegex(ValueError, 'Unpublished native preparation'):
            self.store.initialize(self.contract)
        retried = self.qualify()
        self.assertFalse(retried['execution_started'])
        self.assertEqual(self.native_receipt(), receipt)
        self.assertGreater(self.store.initialize(self.contract)['budget']['wall_seconds']['charged_estimate'], 0)

    def test_failed_and_unknown_original_wall_costs_are_retained(self):
        self.write('bad.py', 'def wrong(a, b):\n    return 0\n')
        tools.extract(self.root, self.root / 'bad.py', 'wrong', 'wrong-v1')
        self.assertEqual(self.qualify('wrong-v1')['status'], 'FAILED')
        def interrupted(store, run_id, **kwargs):
            return store._finish(run_id, None, 'INTERRUPTED', None, None, False,
                                 ['Synthetic unavailable dispatch; wall cost unknown'], only_unstarted=True)
        with patch.object(ProjectStore, 'execute', interrupted):
            unknown = self.qualify()
        self.assertEqual(unknown['status'], 'UNKNOWN')
        state = self.store.initialize(self.contract)
        with self.store._db(True) as db:
            charge = json.loads(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_COST'").fetchone()['body'])
        self.assertEqual({r['status'] for r in charge['validations']}, {'FAILED', 'INTERRUPTED'})
        self.assertGreater(charge['wall_seconds'], 3)
        self.assertEqual(state['budget']['wall_seconds']['charged_estimate'], charge['wall_seconds'])

    def test_legacy_init_without_optional_interface_does_not_require_preparation_reconciliation(self):
        def interrupted(*args, **kwargs):
            raise RuntimeError('synthetic interruption')
        with patch('rds_tools.execute', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.qualify()
        legacy = deepcopy(self.contract)
        del legacy['advisor_policy']['tool_bindings']
        state = self.store.initialize(legacy)
        self.assertEqual(state['budget']['wall_seconds']['charged_estimate'], 0)

    def test_initialized_legacy_native_validation_keeps_its_existing_entry(self):
        legacy = deepcopy(self.contract)
        del legacy['advisor_policy']['tool_bindings']
        self.store.initialize(legacy)
        self.assertEqual(self.qualify()['status'], 'LOCAL_CASES_PASSED')
        self.assertEqual(self.intents(), [])


if __name__ == '__main__':
    unittest.main()
