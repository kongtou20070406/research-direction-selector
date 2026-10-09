"""Issue #235: finite declarations assemble the original owned project model.

The CPU workloads and native qualification cases are synthetic software
regressions. Their receipts, predicates and costs do not establish scientific
gain. Only the compiler's file hashing is mocked, to count unique reads.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_project_assembly as assembly
from rds_project import ProjectStore
from rds_tms_store import current


SCRIPT = '''import json, pathlib, sys
run_id, score, output = sys.argv[1:]
with pathlib.Path('outputs/launches.txt').open('a', encoding='utf-8') as stream:
    stream.write(run_id + '\\n')
pathlib.Path(output).write_text(json.dumps({'score': int(score), 'run_id': run_id}), encoding='utf-8')
'''


def raw_digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_bytes(value):
    # Independent byte oracle: this is the documented canonical protocol format.
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


class ProjectAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-assembly-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = ProjectStore(self.root)
        self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}
        self.trace = []
        self.addCleanup(self.export_evidence)
        for name, text in [('code.py', SCRIPT), ('config.json', '{}'),
                           ('data.json', '[1]'), ('evaluator.json', '{}')]:
            self.write(name, text)
        self.recipe = self.make_recipe()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value.encode('utf-8') if isinstance(value, str) else value
        path.write_bytes(raw)
        return path

    def write_json(self, name, value):
        return self.write(name, canonical_bytes(value) + b'\n')

    def sha(self, name):
        return raw_digest((self.root / name).read_bytes())

    def action(self, run_id, fact):
        return {'id': run_id, 'kind': 'PAIRED_TEST', 'target': fact,
                'operation': 'observe-' + run_id,
                'description': 'Read the explicitly declared synthetic scalar',
                'competing_explanations': ['positive scalar', 'negative scalar'],
                'required_observables': [fact],
                'outcomes': [{'observation': 'positive', 'next_decision': 'inspect the scoped goal'},
                             {'observation': 'negative', 'next_decision': 'inspect bounded repair'}]}

    def make_recipe(self):
        routes = []
        for run_id, score in [('baseline', '-1'), ('repair', '1')]:
            fact, output = run_id + '.score', 'outputs/' + run_id + '.json'
            routes.append({'action': self.action(run_id, 'baseline.score'),
                           'run': {'id': run_id, 'arm': 'control',
                                   'argv': [sys.executable, '-B', 'code.py', run_id, score, output],
                                   'outpaths': [output],
                                   'resource_estimates': {'wall_seconds': 5, 'cpu_seconds': 1, 'gpu_seconds': 0},
                                   'timeout_seconds': 5},
                           'preconditions': [] if run_id == 'baseline' else
                           [{'fact': 'baseline.score', 'op': 'lt', 'value': 0}],
                           'observations': [{'fact': fact, 'path': output,
                                             'selector': {'pointer': '/score'}}]})
        return {'schema': 1,
                'files': {'code': ['code.py'], 'config': ['config.json'],
                          'data': ['data.json'], 'evaluator': ['evaluator.json']},
                'protocol': {'path': 'protocol.json',
                             'metadata': {'data_split': 'synthetic-development', 'init': 'none',
                                          'seed': 0, 'checkpoint': 'none', 'schedule': 'two finite observations',
                                          'sample_work': {'rows': 1}, 'numeric_protocol': 'Python integer'}},
                'context': {'decision': {'id': 'next', 'goal_revision': 'assembly-v1',
                                         'scope': {'domain': 'software-acceptance'},
                                         'goal_conditions': [{'fact': 'baseline.score', 'op': 'gte', 'value': .01}]}},
                'routes': routes, 'output_roots': ['outputs'],
                'budget': {'wall_seconds': 30, 'cpu_seconds': 10, 'gpu_seconds': 0}}

    def call(self, *args, ok=True):
        if args and args[0] == 'rsi':
            args = (*args, '--json')
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), *args],
            capture_output=True, text=True, encoding='utf-8', env=self.env, timeout=40)
        self.trace.append({'argv': list(args), 'returncode': result.returncode,
                           'stdout': result.stdout, 'stderr': result.stderr})
        if ok:
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-6000:])
            return json.loads(result.stdout)
        return result

    def export_evidence(self):
        # Opt-in original transcript and synthetic workspace, before tmp cleanup.
        # An outer frozen RDS run can preserve failures as well as passes.
        destination = os.environ.get('RDS_TEST_EVIDENCE_ROOT')
        if not destination:
            return
        target = Path(destination) / self._testMethodName
        target.mkdir(parents=True, exist_ok=False)
        (target / 'cli-transcript.json').write_bytes(canonical_bytes(self.trace) + b'\n')
        shutil.copytree(self.root, target / 'original-workspace')

    def init(self, recipe=None):
        path = self.write_json('recipe.json', self.recipe if recipe is None else recipe)
        return self.call('project', 'init', '--recipe', str(path))

    def reject(self, recipe):
        with self.assertRaises(ValueError):
            assembly.compile_recipe(self.store, recipe)
        if self.store.path.exists():
            with self.store._db(True) as db:
                table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
                if table:
                    self.assertIsNone(db.execute('SELECT 1 FROM contract WHERE id=1').fetchone())

    def starts(self):
        path = self.root / 'outputs/launches.txt'
        return path.read_text(encoding='utf-8').splitlines() if path.exists() else []

    def test_compile_preserves_declared_semantics_and_original_contract_shape(self):
        before = deepcopy(self.recipe)
        contract, raw, metadata = assembly.compile_recipe(self.store, self.recipe)
        self.assertEqual(self.recipe, before)
        self.assertEqual(contract['schema'], 1)
        self.assertEqual(contract['budget'], before['budget'])
        self.assertEqual(contract['output_roots'], ['outputs'])
        self.assertEqual(contract['advisor_policy']['context'], before['context'])
        self.assertEqual(contract['allowed_commands'], [r['run']['argv'] for r in before['routes']])
        expected_protocol = {**before['protocol']['metadata'],
                             **{role + '_sha256': self.sha(before['files'][role][0])
                                for role in ('code', 'config', 'data')}}
        self.assertEqual(raw, canonical_bytes(expected_protocol) + b'\n')
        policy = contract['advisor_policy']
        self.assertEqual([n['id'] for n in policy['graph']['nodes']], ['baseline-recipe', 'repair-recipe'])
        self.assertEqual(policy['graph']['edges'], [])
        for node, route, source in zip(policy['graph']['nodes'], policy['routes'], before['routes']):
            self.assertEqual(node['executable']['action'], source['action'])
            self.assertEqual(node['executable']['decisions'], ['next'])
            self.assertEqual(node['executable']['preconditions'], source['preconditions'])
            self.assertEqual(route['candidate'], source['action']['id'])
            self.assertEqual(route['manifest'], {**source['run'], 'control_id': None, 'schema': 1,
                                               'protocol': {'path': 'protocol.json', 'sha256': raw_digest(raw)}})
        self.assertEqual(policy['observations'],
                         [{**r['observations'][0], 'run_id': r['run']['id']} for r in before['routes']])
        self.assertIsInstance(metadata, dict)
        self.assertEqual(self.starts(), [])

    def test_unicode_multi_source_role_hashes_and_each_file_is_hashed_once(self):
        extra = self.write('计算/辅助.py', '# explicit second code source\n')
        self.recipe['files']['code'].insert(0, '计算/辅助.py')
        original = assembly.file_sha
        with patch.object(assembly, 'file_sha', wraps=original) as hashed:
            contract, raw, _ = assembly.compile_recipe(self.store, self.recipe)
        observed = [Path(c.args[0]).resolve() for c in hashed.call_args_list]
        expected = [self.root / name for values in self.recipe['files'].values() for name in values]
        self.assertEqual(sorted(map(str, observed)), sorted(map(str, expected)))
        self.assertEqual(len(observed), len(set(observed)))
        self.assertIn(extra.resolve(), observed)
        code_refs = sorted([{'path': name, 'sha256': self.sha(name)}
                            for name in self.recipe['files']['code']], key=lambda b: b['path'])
        self.assertEqual(json.loads(raw)['code_sha256'], raw_digest(canonical_bytes(code_refs)))
        self.assertIn('计算/辅助.py', [b['path'] for b in contract['bindings']])
        self.assertEqual(raw[-1:], b'\n')

    def test_one_source_used_in_two_roles_is_hashed_once(self):
        self.recipe['files']['evaluator'].append('config.json')
        original = assembly.file_sha
        with patch.object(assembly, 'file_sha', wraps=original) as hashed:
            contract, _, metadata = assembly.compile_recipe(self.store, self.recipe)
        self.assertEqual(hashed.call_count, 4)
        self.assertEqual(metadata['unique_input_files'], 4)
        self.assertIn({'role': 'config', 'path': 'config.json', 'sha256': self.sha('config.json')}, contract['bindings'])
        self.assertIn({'role': 'evaluator', 'path': 'config.json', 'sha256': self.sha('config.json')}, contract['bindings'])

    def test_reordered_file_inventory_preserves_contract_and_live_budget(self):
        self.write('extra.py', '# same explicit code inventory\n')
        self.recipe['files']['code'].append('extra.py')
        original, raw, _ = assembly.compile_recipe(self.store, self.recipe)
        self.init()
        self.call('project', 'advance')
        before = self.store.snapshot()
        reordered = deepcopy(self.recipe)
        reordered['files'] = {role: list(reversed(paths))
                              for role, paths in reversed(list(reordered['files'].items()))}
        compiled, repeated, _ = assembly.compile_recipe(self.store, reordered)
        self.assertEqual(compiled, original)
        self.assertEqual(repeated, raw)
        self.init(reordered)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.starts(), ['baseline'])

    def test_real_cli_two_routes_preserve_goal_values_cost_attempts_and_recovery(self):
        initialized = self.init()
        self.assertIn('assembly', initialized)
        self.assertEqual(initialized['runs'], [])
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.call('project', 'next')['selected_run'], 'baseline')
        first = self.call('project', 'advance')
        self.assertEqual(first['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(first['advisor']['selected_run'], 'repair')
        self.assertEqual(first['advisor']['context']['facts']['baseline.score']['value'], -1)
        second = self.call('project', 'advance')
        self.assertEqual(second['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline', 'repair'])
        report = self.call('project', 'next')
        self.assertIsNone(report['selected_run'])
        self.assertEqual(report['context']['facts']['baseline.score']['value'], -1)
        self.assertEqual(report['context']['facts']['repair.score']['value'], 1)
        self.assertEqual(report['context']['decision']['goal_conditions'],
                         [{'fact': 'baseline.score', 'op': 'gte', 'value': .01}])
        goal = next(n for n in current(self.root)['dependency_map']['nodes'] if n['id'] == 'owned:goal:0')
        self.assertEqual(goal['predicate']['truth'], 'FALSE')  # Original goal remains NOT_MET.
        self.assertEqual(goal['scientific_support'], 'UNKNOWN')
        before = self.store.snapshot()
        self.assertEqual(before['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(before['budget']['cpu_seconds']['reserved'], 0)
        self.assertEqual(len(before['receipts']), 2)
        attempts = [r['attempt_id'] for r in before['runs']]
        self.assertEqual(len(set(attempts)), 2)
        self.assertTrue(all(attempts))
        for ident in ('baseline', 'repair'):
            self.call('project', 'recover', '--id', ident)
        self.init()
        self.call('project', 'advance')
        after = self.store.snapshot()
        for key in ('budget', 'runs', 'receipts', 'exposures', 'contract_sha256'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(self.starts(), ['baseline', 'repair'])

    def test_existing_contract_cli_remains_usable_and_options_are_exclusive(self):
        contract, raw, _ = assembly.compile_recipe(self.store, self.recipe)
        self.write('protocol.json', raw)
        path = self.write_json('contract.json', contract)
        result = self.call('project', 'init', '--contract', str(path))
        self.assertEqual(result['contract'], contract)
        recipe = self.write_json('recipe.json', self.recipe)
        both = self.call('project', 'init', '--recipe', str(recipe), '--contract', str(path), ok=False)
        self.assertNotEqual(both.returncode, 0)
        neither = self.call('project', 'init', ok=False)
        self.assertNotEqual(neither.returncode, 0)

    def test_recipe_cannot_supersede_another_project(self):
        recipe = self.write_json('recipe.json', self.recipe)
        result = self.call('project', 'init', '--recipe', str(recipe), '--supersedes', str(self.root), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.starts(), [])
        self.assertFalse((self.root / 'protocol.json').exists())

    def test_changed_live_recipe_cannot_reset_budget_or_contract(self):
        self.init()
        self.call('project', 'advance')
        before = self.store.snapshot()
        changed = deepcopy(self.recipe)
        changed['budget']['wall_seconds'] = 300
        path = self.write_json('recipe.json', changed)
        result = self.call('project', 'init', '--recipe', str(path), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.starts(), ['baseline'])

    def test_changed_bound_bytes_cannot_be_rebound_in_live_project(self):
        self.init()
        before = self.store.snapshot()
        self.write('evaluator.json', '{"changed":true}')
        result = self.call('project', 'init', '--recipe', str(self.root / 'recipe.json'), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.starts(), [])

    def test_changed_live_protocol_metadata_fails_before_recreating_missing_protocol(self):
        self.init()
        before = self.store.snapshot()
        original = (self.root / 'protocol.json').read_bytes()
        (self.root / 'protocol.json').unlink()
        changed = deepcopy(self.recipe)
        changed['protocol']['metadata']['seed'] = 1
        path = self.write_json('recipe.json', changed)
        result = self.call('project', 'init', '--recipe', str(path), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'protocol.json').exists())
        self.assertEqual(self.store.snapshot(), before)
        self.init()
        self.assertEqual((self.root / 'protocol.json').read_bytes(), original)
        self.assertEqual(self.store.snapshot(), before)

    def test_protocol_collision_is_not_overwritten_but_same_bytes_are_allowed(self):
        _, raw, _ = assembly.compile_recipe(self.store, self.recipe)
        self.write('protocol.json', b'{"existing":"different"}\n')
        original = (self.root / 'protocol.json').read_bytes()
        self.write_json('recipe.json', self.recipe)
        result = self.call('project', 'init', '--recipe', str(self.root / 'recipe.json'), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'protocol.json').read_bytes(), original)
        self.write('protocol.json', raw)
        self.init()
        self.assertEqual((self.root / 'protocol.json').read_bytes(), raw)

    def test_invalid_policy_does_not_publish_generated_protocol(self):
        bad = deepcopy(self.recipe)
        bad['context']['decision']['goal_conditions'][0]['fact'] = 'unbound.goal'
        path = self.write_json('recipe.json', bad)
        result = self.call('project', 'init', '--recipe', str(path), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'protocol.json').exists())
        self.assertEqual(self.starts(), [])

    def test_strict_bounded_recipe_input_rejects_duplicate_nonfinite_and_invalid_json(self):
        canonical = canonical_bytes(self.recipe)
        payloads = [b'{broken', b'[]', canonical.replace(b'"schema":1', b'"schema":1,"schema":1', 1),
                    canonical.replace(b'"value":0.01', b'"value":NaN'),
                    b' ' * (2 * 1024 * 1024 + 1)]
        for raw in payloads:
            with self.subTest(prefix=raw[:30]):
                path = self.write('recipe.json', raw)
                with self.assertRaises(ValueError):
                    assembly.initialize(self.store, path)
                self.assertFalse((self.root / 'protocol.json').exists())
                self.assertEqual(self.starts(), [])

    def test_declaration_route_observation_and_material_limits(self):
        bad = deepcopy(self.recipe)
        bad['description'] = 'x' * (2 * 1024 * 1024)
        self.reject(bad)
        bad = deepcopy(self.recipe)
        bad['routes'] = [deepcopy(bad['routes'][0]) for _ in range(65)]
        self.reject(bad)
        bad = deepcopy(self.recipe)
        bad['routes'][0]['observations'] = [deepcopy(bad['routes'][0]['observations'][0]) for _ in range(129)]
        self.reject(bad)

    def test_top_level_required_fields_and_no_arbitrary_policies(self):
        for field in ('schema', 'files', 'protocol', 'context', 'routes', 'output_roots', 'budget'):
            with self.subTest(missing=field):
                bad = deepcopy(self.recipe)
                del bad[field]
                self.reject(bad)
        for field in ('advisor_policy', 'allowed_commands', 'bindings', 'autonomy', 'method_evolution', 'unknown'):
            with self.subTest(extra=field):
                self.reject({**deepcopy(self.recipe), field: {}})
        for schema in (True, 0, 2, '1'):
            with self.subTest(schema=schema):
                self.reject({**deepcopy(self.recipe), 'schema': schema})

    def test_explicit_budget_and_goals_are_required_and_not_inferred(self):
        for budget in ({}, {'wall_seconds': -1}, {'wall_seconds': True},
                       {'wall_seconds': float('inf')}, {'wall_seconds': '30'}):
            with self.subTest(budget=budget):
                self.reject({**deepcopy(self.recipe), 'budget': budget})
        for value in (None, [], 'reach score'):
            with self.subTest(goals=value):
                bad = deepcopy(self.recipe)
                bad['context']['decision']['goal_conditions'] = value
                self.reject(bad)
        bad = deepcopy(self.recipe)
        bad['context']['facts'] = {'baseline.score': {'value': 1, 'reliable': True}}
        self.reject(bad)

    def test_predicate_operators_reject_malformed_syntax_before_protocol_publication(self):
        for location in ('goal', 'precondition'):
            for operator in ('gtt', [], True, False, 'unknown-operator'):
                with self.subTest(location=location, invalid_operator=operator):
                    recipe = deepcopy(self.recipe)
                    condition = (recipe['context']['decision']['goal_conditions'][0]
                                 if location == 'goal' else recipe['routes'][1]['preconditions'][0])
                    condition['op'] = operator
                    path = self.write_json('recipe.json', recipe)
                    with self.assertRaises(ValueError):
                        assembly.initialize(self.store, path)
                    self.assertFalse((self.root / 'protocol.json').exists())
                    self.assertEqual(self.starts(), [])
        for operator in (None, 'eq', 'ne', 'in', 'lt', 'lte', 'gt', 'gte'):
            with self.subTest(valid_operator=operator):
                recipe = deepcopy(self.recipe)
                goal = recipe['context']['decision']['goal_conditions'][0]
                prerequisite = recipe['routes'][1]['preconditions'][0]
                for condition in (goal, prerequisite):
                    if operator is None:
                        condition.pop('op')
                    else:
                        condition['op'] = operator
                    if operator == 'in':
                        condition['value'] = [-1, 1]
                contract, _, _ = assembly.compile_recipe(self.store, recipe)
                policy = contract['advisor_policy']
                self.assertEqual(policy['context']['decision']['goal_conditions'], [goal])
                self.assertEqual(policy['graph']['nodes'][1]['executable']['preconditions'], [prerequisite])
                self.assertFalse((self.root / 'protocol.json').exists())
                self.assertEqual(self.starts(), [])

    def test_protocol_metadata_cannot_self_supply_identity_hashes_or_omit_semantics(self):
        for field in self.recipe['protocol']['metadata']:
            with self.subTest(missing=field):
                bad = deepcopy(self.recipe)
                del bad['protocol']['metadata'][field]
                self.reject(bad)
        for field in ('code_sha256', 'config_sha256', 'data_sha256', 'path', 'sha256'):
            with self.subTest(extra=field):
                bad = deepcopy(self.recipe)
                bad['protocol']['metadata'][field] = '0' * 64
                self.reject(bad)

    def test_explicit_additional_protocol_semantics_survive_without_expanding_policy(self):
        self.recipe['protocol']['metadata']['metric'] = 'synthetic-score'
        self.recipe['protocol']['metadata']['direction'] = 'maximize'
        contract, raw, summary = assembly.compile_recipe(self.store, self.recipe)
        self.assertEqual(json.loads(raw)['metric'], 'synthetic-score')
        self.assertEqual(json.loads(raw)['direction'], 'maximize')
        self.assertEqual(contract['budget'], self.recipe['budget'])
        self.assertEqual(contract['allowed_commands'], [r['run']['argv'] for r in self.recipe['routes']])
        self.assertNotIn('autonomy', contract['advisor_policy'])
        self.assertEqual(summary['scientific_support'], 'UNKNOWN')
        self.assertFalse(summary['execution_started'])

    def test_invalid_file_roles_duplicates_missing_files_and_empty_roles(self):
        for files in ({}, {'protocol': ['code.py']}, {'code': []}, {'code': 'code.py'},
                      {**self.recipe['files'], 'data': ['missing.json']},
                      {**self.recipe['files'], 'code': ['code.py', 'code.py']}):
            with self.subTest(files=files):
                self.reject({**deepcopy(self.recipe), 'files': files})

    def test_paths_cannot_escape_and_generated_protocol_cannot_alias_inputs(self):
        for name in ('../escape.py', str((self.root.parent / 'escape.py').resolve())):
            with self.subTest(path=name):
                bad = deepcopy(self.recipe)
                bad['files']['code'] = [name]
                self.reject(bad)
        for name in ('../protocol.json', str((self.root / 'absolute.json').resolve()), 'code.py'):
            with self.subTest(protocol=name):
                bad = deepcopy(self.recipe)
                bad['protocol']['path'] = name
                self.reject(bad)

    def test_duplicate_run_action_fact_and_output_are_rejected(self):
        for part in ('run', 'action', 'fact', 'output'):
            with self.subTest(duplicate=part):
                bad = deepcopy(self.recipe)
                first, second = bad['routes']
                if part == 'run':
                    second['run']['id'] = first['run']['id']
                elif part == 'action':
                    second['action']['id'] = first['action']['id']
                elif part == 'fact':
                    second['observations'][0]['fact'] = first['observations'][0]['fact']
                else:
                    second['run']['outpaths'] = first['run']['outpaths'][:]
                    second['observations'][0]['path'] = first['observations'][0]['path']
                self.reject(bad)

    def test_route_shape_unbound_observations_and_command_authority(self):
        mutations = [('missing observation', lambda r: r.pop('observations')),
                     ('foreign output', lambda r: r['observations'][0].update(path='outputs/foreign.json')),
                     ('supplied run id', lambda r: r['observations'][0].update(run_id='repair')),
                     ('invalid pointer', lambda r: r['observations'][0].update(selector={'pointer': '/bad~2'})),
                     ('format', lambda r: r['observations'][0].update(format='yaml')),
                     ('empty command', lambda r: r['run'].update(argv=[])),
                     ('supplied protocol', lambda r: r['run'].update(protocol={})),
                     ('unknown route field', lambda r: r.update(autonomy={})),
                     ('unknown run field', lambda r: r['run'].update(scheduler='windows')),
                     ('estimates missing dimension', lambda r: r['run'].update(resource_estimates={'wall_seconds': 5})),
                     ('timeout beyond estimate', lambda r: r['run'].update(timeout_seconds=6)),
                     ('missing action id', lambda r: r['action'].pop('id'))]
        for name, mutate in mutations:
            with self.subTest(invalid=name):
                bad = deepcopy(self.recipe)
                mutate(bad['routes'][0])
                self.reject(bad)

    def test_outputs_cannot_alias_recipe_protocol_or_bound_sources(self):
        self.write_json('recipe.json', self.recipe)
        for name in ('recipe.json', 'protocol.json', 'code.py', 'config.json', 'data.json', 'evaluator.json'):
            with self.subTest(output=name):
                bad = deepcopy(self.recipe)
                bad['output_roots'] = ['.']
                bad['routes'][0]['run']['outpaths'] = [name]
                bad['routes'][0]['observations'][0]['path'] = name
                path = self.write_json('recipe.json', bad)
                before = path.read_bytes()
                result = self.call('project', 'init', '--recipe', str(path), ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.starts(), [])

    def test_explicit_output_files_cannot_alias_protected_inputs(self):
        for name in ('recipe.json', 'protocol.json', 'code.py', 'evaluator.json'):
            with self.subTest(output_file=name):
                bad = deepcopy(self.recipe)
                bad['output_files'] = [name]
                path = self.write_json('recipe.json', bad)
                result = self.call('project', 'init', '--recipe', str(path), ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'protocol.json').exists())
                self.assertEqual(self.starts(), [])

    def native_recipe(self):
        self.write('source.py', 'def add(a, b):\n    return a + b\n')
        self.write_json('cases.json', [{'args': [1, 2], 'expected': 3}])
        self.write_json('inputs.json', [{'args': [1, 2], 'kwargs': {}}])
        self.call('rsi', 'extract', '--source', str(self.root / 'source.py'), '--entry', 'add', '--name', 'add')
        validation = self.call('rsi', 'validate', '--name', 'add', '--cases', str(self.root / 'cases.json'), '--timeout', '5')
        self.call('rsi', 'register', '--name', 'add', '--validation', validation['id'])
        decision = {'id': 'next', 'goal_revision': 'finite-v1', 'scope': {'domain': 'finite-software'},
                    'goal_conditions': [{'fact': 'task.status', 'op': 'eq', 'value': 'PASS'}]}
        action = self.action('apply', 'task.status')
        self.write_json('decision.json', decision)
        self.write_json('action.json', action)
        prepared = self.call('rsi', 'prepare-application', '--name', 'add', '--inputs', 'inputs.json',
                             '--cases', 'cases.json', '--code-path', 'qualified.py', '--driver', 'apply.py',
                             '--request', 'request.json', '--output', 'outputs/application.json',
                             '--decision', str(self.root / 'decision.json'), '--action-file', str(self.root / 'action.json'),
                             '--candidate', 'apply', '--run-id', 'apply', '--obligation', 'task.status',
                             '--observation-fact', 'task.status')
        self.write_json('prepared.json', prepared)
        recipe = deepcopy(self.recipe)
        recipe['files'] = {}
        recipe['budget'] = {'wall_seconds': 40}
        recipe['context'] = {'decision': decision}
        recipe['routes'] = [{'action': action, 'prepared_application': 'prepared.json',
                             'run': {'resource_estimates': {'wall_seconds': 10}, 'timeout_seconds': 10},
                             'observations': [{'fact': 'task.status', 'path': 'outputs/application.json',
                                               'selector': {'pointer': '/status'}}]}]
        return recipe, prepared

    def test_real_native_preparation_is_compiled_applied_and_consumed_with_original_costs(self):
        recipe, prepared = self.native_recipe()
        contract, _, _ = assembly.compile_recipe(self.store, recipe)
        route = contract['advisor_policy']['routes'][0]
        self.assertEqual(route['manifest']['id'], 'apply')
        self.assertEqual(route['manifest']['arm'], 'tool')
        self.assertIsNone(route['manifest']['control_id'])
        self.assertEqual(route['manifest']['argv'], prepared['argv'])
        self.assertEqual(route['manifest']['outpaths'], ['outputs/application.json'])
        self.assertEqual(contract['advisor_policy']['tool_bindings'], [prepared['binding']])
        for binding in prepared['required_bindings']:
            self.assertIn(binding, contract['bindings'])
        state = self.init(recipe)
        self.assertGreater(state['budget']['wall_seconds']['charged_estimate'], 0)
        self.assertEqual(state['runs'], [])
        self.assertFalse((self.root / 'outputs/application.json').exists())
        next_report = self.call('project', 'next')
        self.assertEqual(next_report['tool_utilization']['counts']['applicable'], 1)
        self.assertEqual(next_report['tool_utilization']['counts']['used'], 0)
        completed = self.call('project', 'advance')
        self.assertEqual(completed['receipt']['run_status'], 'SUCCEEDED')
        use = completed['advisor']['tool_utilization']
        self.assertEqual(use['counts']['used'], 1)
        self.assertEqual(use['counts']['consumed'], 1)
        self.assertEqual(use['tools'][0]['scientific_support'], 'UNKNOWN')
        self.assertEqual(json.loads((self.root / 'outputs/application.json').read_bytes())['cases'][0]['value'], 3)

    def test_native_reports_are_original_inputs_not_self_signed_applicability(self):
        recipe, prepared = self.native_recipe()
        for field, value in [('tool', []), ('validation', None), ('adoption', {}),
                             ('qualification', {'premises': []}),
                             ('application', {'output': 'outputs/application.json', 'driver': None})]:
            with self.subTest(binding=field):
                bad_report = deepcopy(prepared)
                bad_report['binding'][field] = value
                self.write_json('prepared.json', bad_report)
                self.reject(recipe)
        self.write_json('prepared.json', prepared)
        for field, value in [('status', 'APPLICABLE'), ('execution_started', True),
                             ('binding', {}), ('required_bindings', []), ('argv', [sys.executable, '-c', 'pass'])]:
            with self.subTest(report=field):
                bad_report = deepcopy(prepared)
                bad_report[field] = value
                self.write_json('prepared.json', bad_report)
                self.reject(recipe)
        self.write_json('prepared.json', prepared)
        for field, value in [('id', 'other'), ('arm', 'control'), ('argv', prepared['argv']),
                             ('outpaths', ['outputs/other.json']), ('control_id', None)]:
            with self.subTest(run_override=field):
                bad = deepcopy(recipe)
                bad['routes'][0]['run'][field] = value
                self.reject(bad)
        self.write('prepared.json', '{broken')
        self.reject(recipe)

    def test_native_report_binding_changes_and_goal_mismatch_are_rejected(self):
        recipe, prepared = self.native_recipe()
        bad = deepcopy(recipe)
        bad['context']['decision']['goal_conditions'][0]['value'] = 'FAIL'
        self.reject(bad)
        bad = deepcopy(recipe)
        bad['routes'][0]['action']['operation'] = 'different-task'
        self.reject(bad)
        original = (self.root / 'inputs.json').read_bytes()
        self.write_json('inputs.json', [{'args': [2, 3], 'kwargs': {}}])
        self.reject(recipe)
        self.write('inputs.json', original)
        changed = deepcopy(prepared)
        changed['required_bindings'][0]['sha256'] = '0' * 64
        self.write_json('prepared.json', changed)
        self.reject(recipe)

    def test_native_report_cannot_be_an_output_destination(self):
        recipe, _ = self.native_recipe()
        recipe['output_files'] = ['prepared.json']
        path = self.write_json('recipe.json', recipe)
        original = (self.root / 'prepared.json').read_bytes()
        result = self.call('project', 'init', '--recipe', str(path), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'prepared.json').read_bytes(), original)
        self.assertFalse((self.root / 'outputs/application.json').exists())


if __name__ == '__main__':
    unittest.main()
