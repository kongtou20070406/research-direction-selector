"""Exact finite task qualification reads genuine native validation evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_math as assets
import rds_tools as tools
from rds_project import ProjectStore, digest
from rds_method_revision import _code_sha
from rds_tool_application import APPLICATION_DRIVER, driver_sha256
from rds_tool_applicability import application_request, inspect, validate_bindings


CASES = [{'args': [1, 2], 'kwargs': {}, 'expected': 3},
         {'args': [-1, 2], 'kwargs': {}, 'expected': 1}]


class ToolApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-applicability-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = ProjectStore(self.root)
        self.write('original.py', 'def add(a, b):\n    return a + b\n')
        self.write_json('cases.json', CASES)
        tools.extract(self.root, self.root / 'original.py', 'add', 'add-v1')
        validation = tools.validate(self.root, 'add-v1', self.root / 'cases.json', timeout=3)
        self.assertEqual(validation['status'], 'LOCAL_CASES_PASSED', validation)
        tools.register(self.root, 'add-v1', validation['id'])
        tools.command(SimpleNamespace(action='use', root=self.root, name='add-v1', output='qualified.py'))
        self.tool = assets.get(self.root, 'tool:add-v1')
        self.validation = assets.get(self.root, validation['id'])
        self.adoption = assets.get(self.root, 'tool-adoption:add-v1')
        self.write('application_driver.py', APPLICATION_DRIVER)
        self.write_json('inputs.json', [{'args': c['args'], 'kwargs': c['kwargs']} for c in CASES])
        action = {'id': 'apply-add', 'kind': 'PAIRED_TEST', 'target': 'add.status',
                  'operation': 'apply-qualified-add', 'description': 'Apply add to exact finite inputs',
                  'competing_explanations': ['passes declared cases', 'fails declared cases'],
                  'required_observables': ['add.status'],
                  'outcomes': [{'observation': 'PASS', 'next_decision': 'inspect finite results'},
                               {'observation': 'FAIL', 'next_decision': 'review tool'}]}
        decision = {'id': 'next', 'goal_revision': 'finite-add-v1', 'scope': {'domain': 'software-fixture'},
                    'goal_conditions': [{'fact': 'add.status', 'op': 'eq', 'value': 'PASS'}]}
        self.binding = {'candidate': 'apply-add', 'run_id': 'apply-add', 'obligation': 'add.status',
                        'goal_sha256': digest(decision), 'action_sha256': digest(action),
                        'tool': self.ref(self.tool), 'validation': self.ref(self.validation),
                        'adoption': self.ref(self.adoption), 'code_path': 'qualified.py',
                        'qualification': {'domain': 'EXACT_TASK_CASES',
                                          'task_cases': self.file_ref('cases.json'),
                                          'task_inputs': self.file_ref('inputs.json'), 'premises': []},
                        'observation_facts': ['add.status'],
                        'application': {'driver': self.file_ref('application_driver.py'),
                                        'request': {'path': 'application_request.json', 'sha256': '0' * 64},
                                        'output': 'outputs/application.json'}}
        self.write_json('application_request.json', application_request(self.binding, 'add', self.tool['asset']['sha256']))
        self.binding['application']['request'] = self.file_ref('application_request.json')
        bindings = [{'role': role, **self.file_ref(name)} for role, name in
                    [('code', 'qualified.py'), ('code', 'application_driver.py'),
                     ('data', 'inputs.json'), ('evaluator', 'cases.json'), ('config', 'application_request.json')]]
        protocol = {'code_sha256': _code_sha({'bindings': bindings}),
                    'config_sha256': self.sha('application_request.json'), 'data_sha256': self.sha('inputs.json'),
                    'data_split': 'exact-finite-development', 'init': 'none', 'seed': 0, 'checkpoint': 'none',
                    'schedule': 'two function calls', 'sample_work': {'cases': 2}, 'numeric_protocol': 'Python integer'}
        self.write_json('protocol.json', protocol)
        bindings.append({'role': 'protocol', **self.file_ref('protocol.json')})
        argv = [sys.executable, '-B', 'application_driver.py', 'application_request.json']
        manifest = {'schema': 1, 'id': 'apply-add', 'arm': 'tool', 'control_id': None,
                    'protocol': self.file_ref('protocol.json'), 'argv': argv,
                    'outpaths': ['outputs/application.json'],
                    'resource_estimates': {'wall_seconds': 3.2}, 'timeout_seconds': 3}
        self.contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [argv],
                         'output_roots': ['outputs'], 'budget': {'wall_seconds': 20},
                         'advisor_policy': {'schema': 1, 'context': {'decision': decision},
                                            'graph': {'nodes': [{'id': 'apply-add', 'sources': ['fixture'],
                                                                'executable': {'decisions': ['next'], 'preconditions': [],
                                                                               'action': action}}], 'edges': []},
                                            'routes': [{'candidate': 'apply-add', 'manifest': manifest}],
                                            'observations': [{'fact': 'add.status', 'run_id': 'apply-add',
                                                              'path': 'outputs/application.json',
                                                              'selector': {'pointer': '/status'}, 'format': 'json'}]}}
        self.store.initialize(self.contract)

    def write(self, name, raw):
        (self.root / name).write_bytes(raw if isinstance(raw, bytes) else raw.encode('utf-8'))

    def write_json(self, name, value):
        self.write(name, json.dumps(value, allow_nan=False))

    def sha(self, name):
        return hashlib.sha256((self.root / name).read_bytes()).hexdigest()

    def file_ref(self, name):
        return {'path': name, 'sha256': self.sha(name)}

    @staticmethod
    def ref(record):
        return {'id': record['id'], 'sha256': digest(record)}

    def inspect(self, **kwargs):
        return inspect(self.store, self.binding, contract=self.contract, **kwargs)

    def rebound(self, name):
        for bound in self.contract['bindings']:
            if bound['path'] == name:
                bound['sha256'] = self.sha(name)
        qual = self.binding['qualification']
        if name == 'cases.json':
            qual['task_cases'] = self.file_ref(name)
        elif name == 'inputs.json':
            qual['task_inputs'] = self.file_ref(name)
        elif name == 'application_request.json':
            self.binding['application']['request'] = self.file_ref(name)

    def test_exact_current_task_is_applicable_without_dispatch_or_scientific_claim(self):
        before = self.store.snapshot()
        result = self.inspect()
        self.assertEqual(result['status'], 'APPLICABLE')
        self.assertEqual(result['case_count'], 2)
        self.assertEqual(result['code_sha256'], self.tool['asset']['sha256'])
        self.assertFalse(result['used'])
        self.assertFalse(result['result_consumed'])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertGreater(result['qualification_cost']['resources']['wall_seconds']['measured'], 0)
        self.assertFalse(result['qualification_cost']['parent_budget_accounted'])
        self.assertEqual(self.store.snapshot(), before)
        self.assertFalse((self.root / 'outputs/application.json').exists())

    def test_wrong_current_task_cases_are_inapplicable(self):
        self.write_json('cases.json', [{'args': [8, 9], 'expected': 17}])
        self.rebound('cases.json')
        self.assertEqual(self.inspect()['status'], 'INAPPLICABLE')

    def test_extra_args_or_kwargs_cannot_inherit_validation(self):
        for inputs in ([{'args': [1, 2, 3], 'kwargs': {}}, {'args': [-1, 2], 'kwargs': {}}],
                       [{'args': [1, 2], 'kwargs': {'unstated': 1}}, {'args': [-1, 2], 'kwargs': {}}]):
            with self.subTest(inputs=inputs):
                self.write_json('inputs.json', inputs)
                self.rebound('inputs.json')
                self.assertEqual(self.inspect()['status'], 'INAPPLICABLE')

    def test_catalogue_or_broad_domain_stays_unknown(self):
        self.binding['qualification']['domain'] = 'all integers'
        self.assertEqual(self.inspect()['status'], 'UNKNOWN')
        self.binding['qualification'] = self.binding['application'] = None
        self.assertEqual(self.inspect()['status'], 'UNKNOWN')

    def test_equal_python_values_with_different_json_types_are_inapplicable(self):
        for value in (True, 1.0):
            with self.subTest(value=value):
                self.write_json('inputs.json', [{'args': [value, 2], 'kwargs': {}},
                                                {'args': [-1, 2], 'kwargs': {}}])
                self.rebound('inputs.json')
                self.assertEqual(self.inspect()['status'], 'INAPPLICABLE')

    def test_refuted_native_result_is_inapplicable(self):
        assets.put(self.root, 'refute:add', 'refutation', b'counterexample scope review',
                   dependencies=[self.tool['id']], data={'target': self.tool['id']})
        self.assertEqual(self.inspect()['status'], 'INAPPLICABLE')

    def test_stale_native_reference_and_changed_export_are_rejected(self):
        self.binding['tool']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'stale identity'):
            self.inspect()
        self.binding['tool'] = self.ref(self.tool)
        self.write('qualified.py', 'def add(a, b):\n    return a - b\n')
        with self.assertRaisesRegex(ValueError, 'differs from qualified'):
            self.inspect()

    def test_original_native_oracle_and_receipt_artifact_are_rechecked(self):
        job = self.root / self.validation['data']['job_root']
        original = (job / 'outputs/result.json').read_bytes()
        (job / 'outputs/result.json').write_bytes(b'{"status":"PASS"}')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            self.inspect()
        (job / 'outputs/result.json').write_bytes(original)
        (job / 'driver.py').write_bytes(b'print("forged PASS")')
        with self.assertRaisesRegex(ValueError, 'bindings changed'):
            self.inspect()

    def test_fixed_driver_override_and_changed_request_are_rejected(self):
        self.assertEqual(self.binding['application']['driver']['sha256'], driver_sha256())
        with self.assertRaisesRegex(ValueError, 'override differs'):
            self.inspect(expected_driver_sha256='0' * 64)
        request = application_request(self.binding, 'add', self.tool['asset']['sha256'])
        request['entry'] = 'other'
        self.write_json('application_request.json', request)
        self.rebound('application_request.json')
        with self.assertRaisesRegex(ValueError, 'request differs'):
            self.inspect()

    def test_exact_argv_and_output_are_required(self):
        self.contract['advisor_policy']['routes'][0]['manifest']['argv'].append('--extra')
        with self.assertRaisesRegex(ValueError, 'exact authorized'):
            self.inspect()
        self.binding['application']['output'] = 'outside/result.json'
        with self.assertRaisesRegex(ValueError, 'outside its frozen route'):
            self.inspect()

    def test_goal_action_route_and_observation_identities_are_frozen(self):
        for field, value, message in [('goal_sha256', '0' * 64, 'goal/action'),
                                       ('action_sha256', '0' * 64, 'goal/action'),
                                       ('run_id', 'other', 'route/candidate'),
                                       ('obligation', 'other', 'outside the frozen goal'),
                                       ('observation_facts', ['unowned'], 'belong to')]:
            with self.subTest(field=field):
                bound = deepcopy(self.binding)
                bound[field] = value
                with self.assertRaisesRegex(ValueError, message):
                    validate_bindings(self.store, self.contract, [bound])

    def test_outside_paths_evaluator_alias_and_binding_cap_are_rejected(self):
        bound = deepcopy(self.binding)
        bound['code_path'] = '../outside.py'
        with self.assertRaises(ValueError):
            validate_bindings(self.store, self.contract, [bound])
        self.contract['bindings'].append({'role': 'code', **self.file_ref('cases.json')})
        with self.assertRaisesRegex(ValueError, 'exclusively bound as evaluator'):
            self.inspect()
        with self.assertRaisesRegex(ValueError, 'at most 16'):
            validate_bindings(self.store, self.contract, [self.binding] * 17)

    def test_unobserved_premise_stays_unknown_and_self_signed_fact_is_rejected(self):
        self.binding['qualification']['premises'] = [{'fact': 'add.status', 'op': 'eq', 'value': 'PASS'}]
        self.assertEqual(self.inspect()['status'], 'UNKNOWN')
        with self.assertRaisesRegex(ValueError, 'override differs'):
            self.inspect(facts={'add.status': {'value': 'PASS', 'source': 'model'}})


if __name__ == '__main__':
    unittest.main()
