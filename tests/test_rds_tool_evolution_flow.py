"""Actual CLI: infeasible plan -> editable tool -> adoption -> fresh pilots -> frozen verifier.

All inputs are the public integer-sum fixture. Initial verifier and post-adoption runtime
forecasts use labelled, fixed test premises; real estimates remain in reports.
Real CLI execution, resource accounting, hard limits and the verifier remain live.
A verified result establishes this workflow, not runtime scaling or research gain.
Set RDS_EVOLUTION_ARTIFACT_ROOT to retain raw CLI transcripts and original logs.
"""
from copy import deepcopy
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import canonical, digest, file_sha

_spec = importlib.util.spec_from_file_location('_tool_evolution_public', ROOT / 'examples/predictive-feasibility/prepare.py')
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


class ToolEvolutionFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-tool-evolution-flow-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.addCleanup(self.retain_evidence)

    def retain_evidence(self):
        destination = os.environ.get('RDS_EVOLUTION_ARTIFACT_ROOT')
        if not destination:
            return
        target = Path(destination).resolve() / self._testMethodName
        target.mkdir(parents=True, exist_ok=True)
        (target / 'cli-transcript.json').write_text(canonical(self.calls), encoding='utf-8')
        for relative in ('.rds/project-artifacts', 'outputs'):
            source = self.root / relative
            if source.exists():
                shutil.copytree(source, target / relative, dirs_exist_ok=True)
        for name in ('contract.json', 'proposal-final.json', 'snapshot-final.json', 'snapshot-before-revision.json',
                     'test-forecast-premise.json', 'test-initial-verifier-premise.json'):
            if (self.root / name).exists():
                shutil.copyfile(self.root / name, target / name)

    def cli(self, *args, ok=True):
        argv = [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), *map(str, args)]
        if hasattr(self, 'forecast_premise'):
            argv = [sys.executable, '-B', str(ROOT / 'tests/tool_evolution_cli_fixture.py'),
                    str(self.root), str(self.forecast_premise), '--root', str(self.root), *map(str, args)]
        env = {**os.environ, 'RDS_USAGE_DB': str(self.root / '.rds/usage-flow.sqlite3')}
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=35, env=env)
        self.calls.append({'argv': argv, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def write(self, name, value):
        path = self.root / name
        path.write_text(canonical(value), encoding='utf-8')
        return path

    def launches(self):
        path = self.root / 'outputs/launches.txt'
        return path.read_text(encoding='utf-8').splitlines() if path.exists() else []

    def genesis_record(self):
        with closing(sqlite3.connect(self.root / '.rds/project.sqlite3')) as db:
            return db.execute('SELECT id,sha256,body FROM contract').fetchall()

    def campaign(self):
        with closing(sqlite3.connect(self.root / '.rds/project.sqlite3')) as db:
            return db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED'").fetchall()

    def initialize_slow_only(self):
        contract = fixture.prepare(self.root)
        self.template = deepcopy(contract['advisor_policy'])
        # At genesis the fast command is authorized but its implementation/route
        # is unavailable. The edited candidate must actually introduce it.
        old = (self.root / 'solver.py').read_text(encoding='utf-8')
        old = old.replace('elif mode == "fast":\n    result = {"value": 99*100//2}\n', '')
        (self.root / 'solver.py').write_text(old, encoding='utf-8')
        self.old_code = (self.root / 'solver.py').read_bytes()
        code_sha = file_sha(self.root / 'solver.py')
        for binding in contract['bindings']:
            if binding['role'] == 'code':
                binding['sha256'] = code_sha
            if binding['role'] == 'protocol':
                path = self.root / binding['path']
                protocol = json.loads(path.read_text(encoding='utf-8'))
                protocol['code_sha256'] = code_sha
                path.write_text(canonical(protocol), encoding='utf-8')
                binding['sha256'] = file_sha(path)
        for route in self.template['routes']:
            ref = route['manifest']['protocol']
            ref['sha256'] = file_sha(self.root / ref['path'])
        self.verify2 = deepcopy(next(r for r in self.template['routes'] if r['manifest']['id'] == 'pilot-verify'))
        self.verify2['candidate'] = 'pilot-verify2'
        self.verify2['manifest'].update(id='pilot-verify2',
                                      argv=[sys.executable, '-B', 'verifier.py', 'pilot-verify2', 'pilot-verify', 'outputs/pilot-verify2.json'],
                                      outpaths=['outputs/pilot-verify2.json'])
        # This exact future argv is granted before init, reusing a bound protocol.
        contract['allowed_commands'].append(self.verify2['manifest']['argv'])
        policy = deepcopy(self.template)
        removed = {'pilot-fast', 'fast'}
        policy['routes'] = [r for r in policy['routes'] if r['manifest']['id'] not in removed]
        policy['observations'] = [o for o in policy['observations'] if o['run_id'] not in removed]
        policy['graph']['nodes'] = [n for n in policy['graph']['nodes'] if n['id'] not in removed]
        next(n for n in policy['graph']['nodes'] if n['id'] == 'pilot-verify')['executable']['preconditions'] = []
        next(n for n in policy['graph']['nodes'] if n['id'] == 'pilot-slow')['executable']['preconditions'] = [
            {'fact': 'run.pilot-verify.succeeded', 'op': 'eq', 'value': True}]
        config = policy['feasibility']
        config['pilots'] = ['pilot-verify', 'pilot-slow']
        config['models'].pop('fast')
        config['plans'] = [p for p in config['plans'] if p['id'] == 'slow']
        config['max_pilot_wall_seconds'] = 20
        contract['budget']['wall_seconds'] = 200
        contract['advisor_policy'] = policy
        self.genesis = deepcopy(contract)
        self.evaluator = (self.root / 'verifier.py').read_bytes()
        self.cli('project', 'init', '--contract', self.write('contract.json', contract))

    def install_initial_verifier_premise(self):
        policy = self.genesis['advisor_policy']
        manifest = next(r['manifest'] for r in policy['routes'] if r['manifest']['id'] == 'verify')
        self.forecast_premise = self.write('test-initial-verifier-premise.json', {
            'phase': 'initial-verifier', 'root': str(self.root),
            'contract_sha256': digest(self.genesis), 'routes': {'verify': {
                'manifest_sha256': digest(manifest),
                'model_sha256': digest(policy['feasibility']['models']['verify']),
                'lower_wall_seconds': 1.0, 'upper_wall_seconds': 3.0}}})

    def evolve_and_verify(self, correct):
        self.initialize_slow_only()
        self.install_initial_verifier_premise()
        for expected in ('pilot-verify', 'pilot-slow'):
            if expected == 'pilot-slow':
                preview = self.cli('project', 'next')
                estimate = next(e for e in preview['feasibility']['plans'][0]['steps'] if e['run_id'] == 'verify')
                self.assertEqual(estimate['basis'], 'TEST_SYNTHETIC_CONDITIONAL_FORECAST')
                self.assertEqual(estimate['test_premise_phase'], 'initial-verifier')
                self.assertEqual(estimate['real_forecast']['status'], 'CONDITIONAL_FORECAST')
                self.assertEqual(estimate['real_forecast']['sources'][0]['run_id'], 'pilot-verify')
            result = self.cli('project', 'advance')
            self.assertIn('receipt', result, result)
            self.assertEqual(result['receipt']['run_id'], expected)
            self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED', result)
        blocked = self.cli('project', 'next')
        self.assertIsNone(blocked['selected_run'])
        self.assertEqual(blocked['feasibility']['plans'][0]['status'], 'INFEASIBLE')
        self.assertGreater(blocked['feasibility']['plans'][0]['lower_wall_seconds'],
                           blocked['feasibility']['plans'][0]['available_wall_seconds'])
        self.assertIn('project improve', blocked['feasibility']['repair_request']['tool_workbench_command'])
        before = self.cli('project', 'status')
        self.write('snapshot-before-revision.json', before)
        row, campaign = self.genesis_record(), self.campaign()
        slow = next(r['manifest'] for r in self.genesis['advisor_policy']['routes'] if r['manifest']['id'] == 'slow')
        self.cli('project', 'create', '--manifest', self.write('slow-denied.json', slow), ok=False)
        self.assertEqual(self.launches(), ['pilot-verify', 'pilot-slow'])
        bundle = self.cli('project', 'improve', '--code-path', 'solver.py', '--id', 'closed-form-tool')
        request_bytes = Path(bundle['request']).read_bytes()
        self.assertFalse(bundle['execution_started'])
        self.assertEqual((self.root / 'solver.py').read_bytes(), self.old_code)
        self.assertEqual(self.launches(), ['pilot-verify', 'pilot-slow'])
        self.assertEqual(self.cli('project', 'status')['budget'], before['budget'])
        candidate = Path(bundle['candidate_source'])
        body = self.old_code.decode('utf-8')
        # Model-like tool construction: new closed-form function, no 15 s wait,
        # and a future fast entrypoint. The verifier is never edited.
        formula = 'n*(n-1)//2' if correct else 'n*(n+1)//2'
        body = 'def closed_form_sum(n):\n    return ' + formula + '\n' + body
        body = body.replace('    time.sleep(15)\n', '').replace('sum(range(100))', 'closed_form_sum(100)')
        body = body.replace('pathlib.Path(output).write_text',
                            'elif mode == "fast":\n    result = {"value": closed_form_sum(100), "verified": True}\npathlib.Path(output).write_text')
        candidate.write_text(body, encoding='utf-8')
        proposal_path = Path(bundle['proposal'])
        proposal = json.loads(proposal_path.read_text(encoding='utf-8'))
        policy = proposal['policy']
        for run_id in ('pilot-fast', 'fast'):
            policy['routes'].append(deepcopy(next(r for r in self.template['routes'] if r['manifest']['id'] == run_id)))
            policy['graph']['nodes'].append(deepcopy(next(n for n in self.template['graph']['nodes'] if n['id'] == run_id)))
            policy['observations'].append(deepcopy(next(o for o in self.template['observations'] if o['run_id'] == run_id)))
        policy['routes'].append(deepcopy(self.verify2))
        node = deepcopy(next(n for n in self.template['graph']['nodes'] if n['id'] == 'pilot-verify'))
        node['id'] = node['executable']['action']['id'] = 'pilot-verify2'
        node['executable']['preconditions'] = [{'fact': 'run.pilot-fast.succeeded', 'op': 'eq', 'value': True}]
        node['executable']['action']['required_observables'] = ['pilot-verify2.result']
        policy['graph']['nodes'].append(node)
        policy['observations'].append({'fact': 'pilot-verify2.result', 'run_id': 'pilot-verify2',
                                       'path': 'outputs/pilot-verify2.json', 'selector': {'pointer': '/score'}})
        config = policy['feasibility']
        config['pilots'].extend(['pilot-fast', 'pilot-verify2'])
        config['models']['fast'] = deepcopy(self.template['feasibility']['models']['fast'])
        config['models']['verify']['pilot_runs'] = ['pilot-verify2']
        config['plans'].append(deepcopy(next(p for p in self.template['feasibility']['plans'] if p['id'] == 'fast')))
        proposal['reason'] = 'Introduce a closed-form tool after the measured enumeration plan exceeded the same remaining allowance'
        proposal_path.write_text(canonical(proposal), encoding='utf-8')
        refresh = self.cli('project', 'improve', '--code-path', 'solver.py', '--id', 'closed-form-tool')
        self.assertEqual(Path(refresh['request']).read_bytes(), request_bytes)
        self.assertEqual(refresh['candidate_sha256'], file_sha(candidate))
        self.assertEqual((self.root / 'solver.py').read_bytes(), self.old_code)
        self.assertEqual(self.launches(), ['pilot-verify', 'pilot-slow'])
        adopted = self.cli('project', 'revise', '--proposal', refresh['proposal'])
        self.assertEqual(adopted['status'], 'ADOPTED')
        self.write('proposal-final.json', json.loads(proposal_path.read_text(encoding='utf-8')))
        revised = self.cli('project', 'status')
        self.assertEqual(revised['budget'], before['budget'])
        self.assertEqual(revised['receipts'], before['receipts'])
        self.assertEqual(revised['runs'], before['runs'])
        self.assertEqual(revised['exposures'], before['exposures'])
        self.assertEqual(self.genesis_record(), row)
        self.assertEqual(self.campaign(), campaign)
        self.assertEqual(len(revised['contract_history']), 2)
        self.assertEqual(revised['contract']['allowed_commands'], self.genesis['allowed_commands'])
        self.assertEqual(revised['contract']['budget'], {'wall_seconds': 200})
        self.assertEqual(revised['contract']['stop_policy']['wall_seconds'], 180)
        self.assertEqual(revised['contract']['advisor_policy']['context'], self.genesis['advisor_policy']['context'])
        self.assertEqual((self.root / 'verifier.py').read_bytes(), self.evaluator)
        # This test checks actual method revision and independent acceptance.
        # Forecast rejection is covered separately with the original estimator.
        # Keep identity/applicability checks and every real resource gate live.
        active = revised['contract']
        manifests = {r['manifest']['id']: r['manifest'] for r in active['advisor_policy']['routes']}
        models = active['advisor_policy']['feasibility']['models']
        self.forecast_premise = self.write('test-forecast-premise.json', {
            'phase': 'post-adoption', 'root': str(self.root), 'contract_sha256': digest(active),
            'routes': {run_id: {'manifest_sha256': digest(manifests[run_id]),
                                'model_sha256': digest(models[run_id]),
                                'lower_wall_seconds': bounds[0], 'upper_wall_seconds': bounds[1]}
                       for run_id, bounds in {'fast': (2.0, 6.0), 'verify': (1.0, 3.0)}.items()}})
        fresh = self.cli('project', 'next')
        self.assertEqual(fresh['selected_run'], 'pilot-fast')
        fast_plan = next(p for p in fresh['feasibility']['plans'] if p['id'] == 'fast')
        self.assertEqual(fast_plan['status'], 'UNKNOWN')
        self.assertNotIn('fast', fresh['feasibility']['admitted_runs'])
        estimates = []
        for expected in ('pilot-fast', 'pilot-verify2', 'fast', 'verify'):
            if expected in {'fast', 'verify'}:
                preview = self.cli('project', 'next')
                self.assertEqual(preview['selected_run'], expected, preview)
                estimates.extend(e for plan in preview['feasibility']['plans'] for e in plan['steps']
                                 if e.get('basis') == 'TEST_SYNTHETIC_CONDITIONAL_FORECAST')
            result = self.cli('project', 'advance')
            self.assertIn('receipt', result, result)
            self.assertEqual(result['receipt']['run_id'], expected, result)
            self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED', result)
        observed = json.loads((self.root / 'outputs/verify.json').read_text(encoding='utf-8'))
        self.assertEqual(observed['verified'], correct)
        self.assertEqual(observed['observed'], 4950 if correct else 5050)
        self.assertTrue(json.loads((self.root / 'outputs/fast.json').read_text(encoding='utf-8'))['verified'])
        final = self.cli('project', 'next')
        self.assertTrue(estimates, self.calls)
        self.assertTrue(all(e['real_forecast']['status'] == 'CONDITIONAL_FORECAST' and not e['sources']
                            for e in estimates))
        if correct:
            self.assertEqual(final['feasibility']['next_action'], 'GOAL_PREDICATES_CONFIRMED')
            self.assertIsNone(final['selected_run'])
        else:
            self.assertNotEqual(final['feasibility']['next_action'], 'GOAL_PREDICATES_CONFIRMED')
        snap = self.cli('project', 'status')
        self.write('snapshot-final.json', snap)
        self.assertEqual(len(snap['receipts']), 6)
        self.assertEqual(snap['budget']['wall_seconds']['reserved'], 0)
        self.assertGreater(snap['budget']['wall_seconds']['spent_measured'], before['budget']['wall_seconds']['spent_measured'])
        self.assertEqual(self.campaign(), campaign)
        self.assertEqual(self.genesis_record(), row)
        self.assertEqual(self.launches(), ['pilot-verify', 'pilot-slow', 'pilot-fast', 'pilot-verify2', 'fast', 'verify'])
        self.assertFalse((self.root / 'outputs/slow.json').exists())
        self.assertEqual((self.root / 'verifier.py').read_bytes(), self.evaluator)
        with closing(sqlite3.connect(self.root / '.rds/project.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM contract').fetchone()[0], 1)
        costs = {r['run_id']: r['resources']['wall_seconds']['measured'] for r in snap['receipts']}
        self.assertAlmostEqual(final['feasibility']['pilot_budget']['spent_or_charged'],
                               sum(v for k, v in costs.items() if k.startswith('pilot')), places=6)
        self.assertEqual(final['feasibility']['pilot_budget']['cap'], 20)

    def test_complete_evolution_and_independent_verification_same_ledger(self):
        self.evolve_and_verify(correct=True)

    def test_wrong_new_tool_cannot_self_certify_the_goal(self):
        self.evolve_and_verify(correct=False)

    def test_initial_unknown_and_hosted_numeric_hard_timeout_denial_do_not_launch(self):
        from rds_project import ProjectStore
        from rds_owned_advisor import _state
        import rds_feasibility
        self.initialize_slow_only()
        self.install_initial_verifier_premise()
        unknown = self.cli('project', 'next')
        verify = next(e for e in unknown['feasibility']['plans'][0]['steps'] if e['run_id'] == 'verify')
        self.assertEqual(verify['status'], 'UNKNOWN')
        self.assertNotIn('real_forecast', verify)
        self.assertEqual(self.launches(), [])
        first = self.cli('project', 'advance')
        self.assertEqual(first['receipt']['run_id'], 'pilot-verify')
        self.assertEqual(first['receipt']['run_status'], 'SUCCEEDED')
        store = ProjectStore(self.root)
        before = store.snapshot()
        with store._db(True) as db:
            original = _state(store, db)
        state = deepcopy(original)
        # Unpersisted numeric control from the original hosted return. This is
        # not a measured local receipt: the actual ledger/costs stay untouched.
        state['receipts'][0]['resources']['wall_seconds']['measured'] = 8.25
        report = rds_feasibility.assess(store, state)
        plan = report['plans'][0]
        estimate = next(e for e in plan['steps'] if e['run_id'] == 'verify')
        self.assertEqual(estimate['lower_wall_seconds'], 8.25)
        self.assertEqual(estimate['upper_wall_seconds'], 24.75)
        self.assertEqual(plan['status'], 'INFEASIBLE')
        self.assertIn('hard timeout', plan['reason'])
        self.assertEqual(report['pilot_budget'], {'cap':20, 'spent_or_charged':8.25,
                                                'reserved':0, 'remaining':11.75})
        self.assertEqual(report['admitted_runs'], [])
        self.assertEqual(report['bounded_pilots'], [])
        self.assertGreater(plan['available_wall_seconds'], 24.75)
        manifest = next(r['manifest'] for r in self.genesis['advisor_policy']['routes']
                        if r['manifest']['id'] == 'pilot-slow')
        with patch('rds_owned_advisor._state', return_value=state):
            with store._db(True) as db, self.assertRaisesRegex(ValueError, 'before child launch'):
                rds_feasibility.check_start(store, db, manifest['id'])
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(self.launches(), ['pilot-verify'])


class FixtureRootIdentityTests(unittest.TestCase):
    def setUp(self):
        from tool_evolution_cli_fixture import validate_fixture_root
        self.validate = validate_fixture_root
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-root-identity-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.premise = self.root / 'test-forecast-premise.json'
        self.premise.write_text('{}', encoding='utf-8')

    def test_same_directory_alias_is_accepted(self):
        child = self.root / 'child'
        child.mkdir()
        aliases = [str(self.root), str(child / '..')]
        if os.name == 'nt':
            aliases.append(str(self.root).swapcase())
        for alias in aliases:
            with self.subTest(alias=alias):
                self.assertEqual(Path(alias).resolve(), self.root)
                self.validate(self.root, self.premise, ['--root', alias, 'project', 'next'])

    def test_another_directory_is_rejected(self):
        other = self.root / 'other'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'exact explicit fixture root'):
            self.validate(self.root, self.premise, ['--root', str(other), 'project', 'next'])

    def test_premise_outside_fixture_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exact explicit fixture root'):
            self.validate(self.root, self.root.parent / self.premise.name,
                          ['--root', str(self.root), 'project', 'next'])

    def test_explicit_root_prefix_is_required(self):
        for args in ([], ['--root'], ['project', 'next'], ['--other', str(self.root)],
                     ['project', 'next', '--root', str(self.root)]):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'exact explicit fixture root'):
                self.validate(self.root, self.premise, args)


class ControlledForecastPremiseTests(unittest.TestCase):
    def setUp(self):
        from tool_evolution_cli_fixture import controlled_estimator
        self.bind = controlled_estimator
        self.store = SimpleNamespace(root=ROOT)
        self.state, self.manifest, self.model = {'contract': {'schema': 1}}, {'id': 'fast'}, {'exponent': 1}
        self.real = {'status': 'CONDITIONAL_FORECAST', 'run_id': 'fast',
                     'lower_wall_seconds': 9.2955076, 'upper_wall_seconds': 27.8865228,
                     'sources': [{'measured_wall_seconds': 4.6477538}]}
        self.original = Mock(return_value=self.real)
        self.route, self.upper = 'fast', 6
        self.premise = {'phase': 'post-adoption', 'root': str(ROOT), 'contract_sha256': digest(self.state['contract']), 'routes': {
            'fast': {'manifest_sha256': digest(self.manifest), 'model_sha256': digest(self.model),
                     'lower_wall_seconds': 2.0, 'upper_wall_seconds': 6.0},
            'verify': {'manifest_sha256': 'unused', 'model_sha256': 'unused',
                       'lower_wall_seconds': 1.0, 'upper_wall_seconds': 3.0}}}

    def estimate(self):
        return self.bind(self.premise, self.original)(self.store, self.state, self.route, self.manifest, self.model)

    def test_labelled_premise_retains_original_estimate_and_does_not_mutate_it(self):
        before = deepcopy(self.real)
        result = self.estimate()
        self.assertEqual(result['basis'], 'TEST_SYNTHETIC_CONDITIONAL_FORECAST')
        self.assertEqual(result['upper_wall_seconds'], self.upper)
        self.assertEqual(result['real_forecast'], before)
        self.assertEqual(result['sources'], [])
        self.assertEqual(self.real, before)
        self.original.assert_called_once_with(self.store, self.state, self.route, self.manifest, self.model)

    def test_missing_or_stale_pilot_remains_unknown(self):
        self.original.return_value = {'status': 'UNKNOWN', 'run_id': 'fast', 'reason': 'Pilot identity is stale'}
        self.assertIs(self.estimate(), self.original.return_value)

    def test_changed_root_contract_manifest_or_model_cannot_get_synthetic_forecast(self):
        for field in ('root', 'contract', 'manifest', 'model'):
            with self.subTest(field=field):
                saved = deepcopy((self.state, self.manifest, self.model))
                self.store.root = ROOT / 'unexpected' if field == 'root' else ROOT
                if field == 'contract':
                    self.state['contract']['schema'] = 2
                if field == 'manifest':
                    self.manifest['id'] = 'changed'
                if field == 'model':
                    self.model['exponent'] = 2
                result = self.estimate()
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIsNone(result['upper_wall_seconds'])
                self.state, self.manifest, self.model = saved

    def test_other_route_keeps_original_estimator(self):
        result = self.bind(self.premise, self.original)(self.store, self.state, 'slow', {'id': 'slow'}, None)
        self.assertIs(result, self.real)


class InitialVerifierPremiseTests(ControlledForecastPremiseTests):
    def setUp(self):
        super().setUp()
        self.route, self.upper = 'verify', 3
        self.model = {'pilot_runs':['pilot-verify'], 'exponent':1}
        self.manifest = {'id':'verify'}
        self.state['contract']['advisor_policy'] = {'feasibility': {
            'models': {'slow': {}, 'verify':deepcopy(self.model)}}}
        self.real.update(run_id='verify', lower_wall_seconds=8.25, upper_wall_seconds=24.75,
                         sources=[{'run_id':'pilot-verify', 'measured_wall_seconds':8.25}])
        self.premise = {'phase':'initial-verifier', 'root':str(ROOT),
            'contract_sha256':digest(self.state['contract']), 'routes': {'verify': {
                'manifest_sha256':digest(self.manifest), 'model_sha256':digest(self.model),
                'lower_wall_seconds':1.0, 'upper_wall_seconds':3.0}}}

    def test_phase_cannot_borrow_post_adoption_models(self):
        self.state['contract']['advisor_policy']['feasibility']['models']['fast'] = {}
        self.premise['contract_sha256'] = digest(self.state['contract'])
        self.assertEqual(self.estimate()['status'], 'UNKNOWN')

    def test_phase_routes_and_fixed_bounds_cannot_be_tampered(self):
        from tool_evolution_cli_fixture import validate_premise
        for change in ('phase', 'route', 'lower', 'upper'):
            premise = deepcopy(self.premise)
            if change == 'phase':
                premise['phase'] = 'post-adoption'
            elif change == 'route':
                premise['routes']['fast'] = deepcopy(premise['routes']['verify'])
            else:
                premise['routes']['verify'][change + '_wall_seconds'] = 20
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_premise(premise)


if __name__ == '__main__':
    unittest.main()
