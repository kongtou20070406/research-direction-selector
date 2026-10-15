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
from prototypes.rds_agent_eval import test_study_bridge as fixtures
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
        self.recipe = {'routes': [{'run': self.manifest}], 'budget': {'wall_seconds': 100}, 'files': {'data': ['table.csv']}}
        self.pins = {str(Path(driver.__file__).resolve()): driver.sha(driver.__file__)}

    def run_response(self, choice):
        raw = json.dumps({'model': 'resolved', 'choices': [choice]}).encode()
        with patch.object(driver.time, 'time', return_value=100), \
             patch.object(driver.urllib.request, 'urlopen', return_value=io.BytesIO(raw)) as opened:
            result = driver.run_arm(self.root / 'arm', self.seed, 'Bare', self.declaration, self.recipe, 'http://fixture', self.pins)
        return result, opened

    def test_early_complete_text_is_retained_without_acceptance_or_private_gold(self):
        result, opened = self.run_response({'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'answer'}})
        self.assertEqual((result['calls'], result['status'], result['final_answer']), (1, 'UNKNOWN_NO_RETRY', None))
        audit = self.root / 'arm/audit'
        self.assertTrue((audit / 'response-0.json').exists())
        self.assertNotIn('PRIVATE_GOLD', (audit / 'request-0.json').read_text(encoding='utf-8'))
        self.assertEqual(opened.call_args.kwargs['timeout'], 100)
        with self.assertRaises(ValueError):
            driver.run_arm(self.root / 'arm', self.seed, 'Bare', self.declaration, self.recipe, 'http://fixture', self.pins)
        with patch.object(driver.time, 'time', return_value=100), \
             patch.object(driver.urllib.request, 'urlopen', side_effect=AssertionError('Unpinned driver requested')), self.assertRaises(ValueError):
            driver.run_arm(self.root / 'bad-pin', self.seed, 'Bare', self.declaration, self.recipe,
                           'http://fixture', {str(Path(driver.__file__).resolve()): '0' * 64})

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
        fixture = fixtures.StudyBridgeTests('runTest')
        fixture.setUp()
        fixture.fixture._testMethodName = self._testMethodName
        self.addCleanup(fixture.doCleanups)
        fixture.initialize()
        manifest = fixture.prepared['contract']['advisor_policy']['routes'][0]['manifest']
        result = driver.tool(fixture.store.root, 'RDS', manifest)
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['measurement']['method'], 'period_counts')
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(result['native_advice']['selected_method'], 'gardening_dominance')
        before = file_sha(fixture.store.path)
        self.assertTrue(driver.tool(fixture.store.root, 'RDS', manifest)['reused_original'])
        self.assertEqual(file_sha(fixture.store.path), before)


class SurveyDriverTests(unittest.TestCase):
    def setUp(self):
        from prototypes.rds_agent_eval import test_domain_bridge as domain_fixtures, domain_bridge
        self.fixture = domain_fixtures.DomainBridgeTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.study = self.fixture.study
        self.seed = self.fixture.store.root
        self.arms_tmp = tempfile.TemporaryDirectory(prefix='rds-native-driver-arms-')
        self.addCleanup(self.arms_tmp.cleanup)
        self.arm_base = Path(self.arms_tmp.name).resolve()
        self.recipe = self.study.recipe
        self.recipe['budget'] = {'wall_seconds': 100, 'cpu_seconds': 100, 'gpu_seconds': 0}
        for route in self.recipe['routes']:
            route['run']['timeout_seconds'] = 7.2
            route['run']['resource_estimates'] = {'wall_seconds': 7.2, 'cpu_seconds': 7.2, 'gpu_seconds': 0}
        self.fixture.native.write_json('recipe.json', self.recipe)
        self.pins = {str(Path(p).resolve()): driver.sha(p) for p in (driver.__file__, domain_bridge.__file__)}
        self.fixture.native.write('task.json', '{"query":"Finish with a JSON object; retain this task suffix."}')
        self.final = self.fixture.fixture.final
        self.domain = domain_bridge.specification()['domain']

    def choice(self, name, value):
        if name == 'text':
            return {'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': value}}
        return {'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'tool_calls': [
            {'id': name, 'function': {'name': name, 'arguments': json.dumps({
                'method' if name == 'execute' else 'answer': value})}}]}}

    def run_choices(self, arm, name, choices, mutate=None):
        root = self.arm_base / name
        calls = []
        def response(req, **kwargs):
            index = len(calls)
            calls.append(json.loads(req.data))
            if mutate is not None:
                mutate(root, index)
            return io.BytesIO(json.dumps({'model': 'resolved', 'choices': [choices[index]]}).encode())
        with patch.object(driver.time, 'time', return_value=100), \
             patch.object(driver.urllib.request, 'urlopen', side_effect=response) as opened:
            result = driver.run_arm(root, self.seed, arm, self.study.declaration, self.recipe,
                                    'http://fixture', self.pins, domain=self.domain)
        return root, result, calls, opened

    def test_both_original_methods_are_required_for_text_and_function_in_both_arms(self):
        for arm in ('Bare', 'RDS'):
            for mode in ('text', 'finish'):
                with self.subTest(arm=arm, mode=mode):
                    choices = [self.choice('execute', m) for m in ('role_inventory', 'quoted_fractions')]
                    root, result, calls, opened = self.run_choices(arm, arm + mode, choices + [self.choice(mode, self.final)])
                    self.assertEqual((result['status'], result['calls']), ('AGENT_FINISHED', 3))
                    self.assertEqual(result['final_answer'], self.final)
                    self.assertEqual({p['method'] for p in result['method_completion']}, {'role_inventory', 'quoted_fractions'})
                    self.assertEqual(result['scientific_support'], 'UNKNOWN')
                    instruction = calls[0]['messages'][0]['content']
                    self.assertIn('bootstrap_ci', instruction)
                    self.assertIn('retain this task suffix', instruction)
                    self.assertNotIn('earliest_observed_main_period', instruction)
                    self.assertNotIn('PRIVATE_GOLD', json.dumps(calls))
                    self.assertEqual(opened.call_count, 3)
                    self.assertEqual(len(list((root/'audit').glob('*-raw.json'))), 2)

    def test_early_finish_or_only_one_method_stops_without_extra_dispatch_or_request(self):
        for arm, mode, one in (('Bare', 'text', False), ('RDS', 'finish', True)):
            with self.subTest(arm=arm):
                choices = ([self.choice('execute', 'role_inventory')] if one else []) + [self.choice(mode, self.final)]
                root, result, calls, opened = self.run_choices(arm, 'early' + arm, choices)
                self.assertEqual(result['status'], 'UNKNOWN_NO_RETRY')
                self.assertIsNone(result['final_answer'])
                self.assertEqual(opened.call_count, len(choices))
                self.assertEqual(len(list((root/'audit').glob('*-raw.json'))), int(one))
                self.assertEqual(json.loads((root/'audit'/f'failure-{len(choices)-1}.json').read_text())['paid_cost'], 'UNKNOWN')

    def test_changed_original_outputs_and_forged_native_status_cannot_complete(self):
        for arm in ('Bare', 'RDS'):
            def mutate(root, index):
                if index != 2:
                    return
                if arm == 'Bare':
                    (root/'outputs/baseline.json').write_bytes(b'changed')
                else:
                    p = root/'audit/baseline-tool.json'
                    value = json.loads(p.read_text())
                    value['status'] = 'FAILED'
                    driver.save(p, value)
            with self.subTest(arm=arm):
                choices = [self.choice('execute', m) for m in ('role_inventory', 'quoted_fractions')]
                root, result, _, opened = self.run_choices(arm, 'changed'+arm, choices+[self.choice('text', self.final)], mutate)
                self.assertEqual(result['status'], 'UNKNOWN_NO_RETRY')
                self.assertEqual(opened.call_count, 3)
                self.assertTrue((root/'audit/response-2.json').is_file())

    def test_unpinned_slot_rejects_before_request_and_wrong_final_keeps_original(self):
        with patch.object(driver.time, 'time', return_value=100), \
             patch.object(driver.urllib.request, 'urlopen', side_effect=AssertionError('Requested')), self.assertRaises(ValueError):
            driver.run_arm(self.arm_base/'unpinned', self.seed, 'Bare', self.study.declaration,
                self.recipe, 'http://fixture', {str(Path(driver.__file__).resolve()): driver.sha(driver.__file__)}, domain=self.domain)
        choices = [self.choice('execute', m) for m in ('role_inventory', 'quoted_fractions')]
        root, result, _, opened = self.run_choices('Bare', 'wrong-final', choices+[self.choice('finish', '{"answer":"missing fields"}')])
        self.assertEqual((result['status'], opened.call_count), ('UNKNOWN_NO_RETRY', 3))
        self.assertIsNone(result['final_answer'])
        self.assertTrue((root/'audit/response-2.json').is_file())

    def test_actual_failed_native_receipt_cannot_supply_completion_or_be_rebought(self):
        self.recipe['routes'][0]['run']['argv'][-2] = 'invalid-method'
        self.fixture.native.write_json('recipe.json', self.recipe)
        with patch.object(driver.time, 'time', return_value=100):
            from rds_project import ProjectStore
            from rds_project_assembly import initialize
            initialize(self.fixture.store, self.seed/'recipe.json')
        with self.fixture.store._db(readonly=True) as db:
            manifest = ProjectStore._contract(db)['advisor_policy']['routes'][0]['manifest']
        result = driver.tool(self.seed, 'RDS', manifest)
        self.assertEqual(result['status'], 'FAILED')
        before = file_sha(self.fixture.store.path)
        with patch.object(ProjectStore, 'execute', side_effect=AssertionError('Rebought')), self.assertRaises(ValueError):
            driver.completed_methods(self.seed, 'RDS', {'invalid-method': manifest})
        self.assertEqual(file_sha(self.fixture.store.path), before)
        original = driver.read_observations(self.fixture.store, [{'run_id': result['receipt']['run_id'],
            'receipt_sha256': result['receipt']['sha256']}])['observations'][0]['receipt']
        self.assertTrue(original['errors'])
        self.assertTrue(original['resources']['cpu_seconds']['unknown'])

    def test_real_request_feedback_is_compact_and_arguments_have_finite_choices(self):
        for arm in ('Bare', 'RDS'):
            with self.subTest(arm=arm):
                choices = [self.choice('execute', m) for m in ('role_inventory', 'quoted_fractions')]
                root, result, calls, opened = self.run_choices(arm, 'compact'+arm, choices+[self.choice('finish', self.final)])
                self.assertEqual((result['status'], opened.call_count), ('AGENT_FINISHED', 3))
                functions = {s['function']['name']: s['function']['parameters'] for s in calls[0]['tools']}
                self.assertEqual(functions['execute']['properties']['method']['enum'], ['role_inventory', 'quoted_fractions'])
                self.assertEqual(functions['read']['properties']['file']['enum'], self.study.declaration['agent_files'])
                self.assertTrue(all(len(s['required']) == 1 for s in functions.values()))
                sent = [json.loads(m['content']) for m in calls[-1]['messages'] if m['role']=='tool']
                for run_id, feedback in zip(('baseline', 'repair'), sent):
                    original = json.loads((root/'audit'/f'{run_id}-tool.json').read_text())
                    self.assertEqual(feedback, driver.model_feedback(original, feedback['method']))
                    self.assertEqual(feedback['measurement'], original['measurement'])
                    self.assertNotIn('argv', feedback)
                    if arm == 'RDS':
                        self.assertEqual(feedback['receipt']['resources'], original['receipt']['resources'])
                        self.assertNotIn('bindings_before', feedback['receipt'])
                        self.assertIn('bindings_before', original['receipt'])
                        self.assertLess(len(json.dumps(feedback)), len(json.dumps(original)))

    def test_failed_original_projection_preserves_unknown_charges_errors_and_assessment(self):
        self.recipe['routes'][0]['run']['argv'][-2] = 'invalid-method'
        self.fixture.native.write_json('recipe.json', self.recipe)
        from rds_project import ProjectStore
        from rds_project_assembly import initialize
        with patch.object(driver.time, 'time', return_value=100):
            initialize(self.fixture.store, self.seed/'recipe.json')
        with self.fixture.store._db(readonly=True) as db:
            manifest = ProjectStore._contract(db)['advisor_policy']['routes'][0]['manifest']
        original = driver.tool(self.seed, 'RDS', manifest)
        before = file_sha(self.fixture.store.path)
        with patch.object(ProjectStore, 'execute', side_effect=AssertionError('Projection dispatched')):
            compact = driver.model_feedback(original, 'invalid-method')
        self.assertEqual(compact['status'], 'FAILED')
        for key in ('resources', 'errors', 'assessment', 'exit_code', 'timeout'):
            self.assertEqual(compact['receipt'][key], original['receipt'][key])
        self.assertTrue(compact['receipt']['errors'])
        self.assertTrue(compact['receipt']['resources']['cpu_seconds']['unknown'])
        self.assertEqual(compact['scientific_support'], 'UNKNOWN')
        self.assertEqual(file_sha(self.fixture.store.path), before)

    def test_full_unicode_measurement_and_unknown_control_fields_are_not_truncated(self):
        original = {'status':'TIMEOUT_UNKNOWN', 'measurement':{'text':'测量'*10000, 'mask':None},
            'cost_status':'UNKNOWN', 'phase':'BARE_TOOL', 'attempt':'baseline',
            'exit_code':None, 'wall_seconds':7.2, 'reused_original':True, 'argv':['private-host-path']}
        before = json.dumps(original)
        compact = driver.model_feedback(original, 'role_inventory')
        self.assertEqual(compact['measurement'], original['measurement'])
        self.assertGreater(len(compact['measurement']['text']), 12000)
        for key in ('status', 'cost_status', 'phase', 'attempt', 'exit_code', 'wall_seconds', 'reused_original'):
            self.assertEqual(compact[key], original[key])
        self.assertEqual(json.dumps(original), before)

    def test_projection_failure_keeps_original_response_receipt_and_stops_without_rebuy(self):
        with patch.object(driver, 'model_feedback', side_effect=ValueError('Projection failed')):
            root, result, _, opened = self.run_choices('RDS', 'projection-failed', [self.choice('execute', 'role_inventory')])
        self.assertEqual((result['status'], opened.call_count), ('UNKNOWN_NO_RETRY', 1))
        self.assertTrue((root/'audit/response-0.json').is_file())
        original = json.loads((root/'audit/baseline-tool.json').read_text())
        self.assertEqual(original['receipt']['run_status'], 'SUCCEEDED')
        from rds_project import ProjectStore
        with patch.object(ProjectStore, 'execute', side_effect=AssertionError('Rebought')):
            observed = driver.read_observations(ProjectStore(root), [{'run_id': original['receipt']['run_id'],
                'receipt_sha256': original['receipt']['sha256']}])
        self.assertEqual(observed['observations'][0]['receipt']['sha256'], original['receipt']['sha256'])
        self.assertIsNone(result['final_answer'])
