"""Real CLI completion, denied child launches and applicability boundaries."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import os
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, digest
import rds_feasibility as feasibility
from rds_owned_advisor import _state

spec = importlib.util.spec_from_file_location('_forecast_fixture', ROOT / 'examples/predictive-feasibility/prepare.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class FeasibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-feasibility-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.contract = fixture.prepare(self.root)
        self.store = ProjectStore(self.root)

    def initialize(self, mutate=None):
        if mutate:
            mutate(self.contract)
        self.store.initialize(self.contract)

    def cli(self, *args, ok=True):
        argv = [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), *args]
        if hasattr(self, 'cost_premise'):
            argv = [sys.executable, '-B', str(ROOT / 'tests/feasibility_cli_fixture.py'),
                    str(self.root), str(self.cost_premise), '--root', str(self.root), *args]
        proc = subprocess.run(argv,
                              capture_output=True, text=True, encoding='utf-8', timeout=25)
        if os.environ.get('RDS_FEASIBILITY_TEST_LOG'):
            with Path(os.environ['RDS_FEASIBILITY_TEST_LOG']).open('a', encoding='utf-8') as log:
                log.write(json.dumps({'test': self.id(), 'root': str(self.root), 'args': args,
                                      'returncode': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}) + '\n')
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return json.loads(proc.stdout)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def state(self):
        with self.store._db(True) as db:
            return _state(self.store, db)

    def launches(self):
        path = self.root / 'outputs/launches.txt'
        return path.read_text(encoding='utf-8').splitlines() if path.exists() else []

    def declare_cost_premise(self):
        from feasibility_cli_fixture import BOUNDS, declared_estimator
        policy = self.contract['advisor_policy']
        manifests = {r['manifest']['id']:r['manifest'] for r in policy['routes']}
        models = policy['feasibility']['models']
        premise = {'purpose':'cost-feasible-plan-and-prerequisite-test', 'root':str(self.root),
            'contract_sha256':digest(self.contract), 'routes': {
                run_id: {'manifest_sha256':digest(manifests[run_id]), 'model_sha256':digest(models.get(run_id)),
                         'lower_wall_seconds':bounds[0], 'upper_wall_seconds':bounds[1]}
                for run_id,bounds in BOUNDS.items()}}
        self.cost_premise = self.root / 'test-cost-premise.json'
        self.cost_premise.write_text(json.dumps(premise), encoding='utf-8')
        return declared_estimator(premise, feasibility._estimate)

    def test_complete_plan_denies_slow_before_launch_and_independently_verifies_fast(self):
        next(n for n in self.contract['advisor_policy']['graph']['nodes'] if n['id'] == 'verify')['executable']['preconditions'] = [
            {'fact': 'run.fast.succeeded', 'op': 'eq', 'value': True}]
        self.initialize()
        estimator = self.declare_cost_premise()
        with patch.object(feasibility, '_estimate', estimator):
            report = feasibility.assess(self.store, self.state())
        plans = {p['id']: p for p in report['plans']}
        self.assertEqual(plans['slow']['status'], 'INFEASIBLE')
        self.assertEqual(plans['fast']['status'], 'FEASIBLE')
        self.assertEqual(report['admitted_runs'], ['fast'])
        self.assertEqual(len(plans['fast']['steps']), 2)
        self.assertGreater(plans['fast']['upper_wall_seconds'], sum(s['upper_wall_seconds'] for s in plans['fast']['steps']))
        slow = self.contract['advisor_policy']['routes'][3]['manifest']
        before = self.store.snapshot()
        with patch.object(feasibility, '_estimate', estimator), self.assertRaisesRegex(ValueError, 'did not select|feasibility gate'):
            self.store.register(slow)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        self.assertEqual(self.launches(), [])
        self.assertEqual(self.cli('project', 'advance')['receipt']['run_id'], 'fast')
        self.assertEqual(self.cli('project', 'advance')['receipt']['run_id'], 'verify')
        observed = json.loads((self.root / 'outputs/verify.json').read_text())
        self.assertEqual(observed, {'verified': True, 'observed': 4950})
        final = self.cli('project', 'next')
        self.assertIsNone(final['selected_run'])
        self.assertEqual(final['feasibility']['next_action'], 'GOAL_PREDICATES_CONFIRMED')
        self.assertIsNone(final['feasibility']['repair_request'])
        self.assertEqual(self.launches(), ['fast', 'verify'])
        snap = self.store.snapshot()
        self.assertEqual(len(snap['receipts']), 2)  # Exactly the real solver and independent verifier.
        self.assertEqual(snap['budget']['wall_seconds']['reserved'], 0)
        self.assertGreater(snap['budget']['wall_seconds']['spent_measured'], 0)

    def test_cli_rejects_required_success_order_conflict_before_freeze(self):
        policy = self.contract['advisor_policy']
        next(n for n in policy['graph']['nodes'] if n['id'] == 'verify')['executable']['preconditions'] = [
            {'fact': 'run.fast.succeeded', 'op': 'eq', 'value': True}]
        next(p for p in policy['feasibility']['plans'] if p['id'] == 'fast')['steps'] = ['verify', 'fast']
        (self.root / 'contract.json').write_text(json.dumps(self.contract), encoding='utf-8')
        proc = self.cli('project', 'init', '--contract', str(self.root / 'contract.json'), ok=False)
        self.assertIn('Completion plan order conflict', proc.stdout + proc.stderr)
        self.assertEqual(self.launches(), [])

    def test_order_check_preserves_prior_external_and_disjunctive_dependencies(self):
        policy = self.contract['advisor_policy']
        verify = next(n for n in policy['graph']['nodes'] if n['id'] == 'verify')['executable']
        for condition in [
                {'fact':'run.fast.succeeded','op':'eq','value':True},
                {'fact':'run.fast.completed','op':'ne','value':False},
                {'fact':'run.fast.status','op':'in','value':['COMPLETED','FAILED']}]:
            verify['preconditions'] = [condition]
            feasibility.validate(self.store, self.contract, policy)  # Earlier fast is legal; absent fast in slow plan is external.
            with self.assertRaisesRegex(ValueError, 'order conflict'):
                feasibility._validate_step_order(policy, ['verify', 'fast'])
        for condition in [
                {'fact':'run.fast.succeeded','op':'in','value':[True,False]},
                {'fact':'run.fast.status','op':'in','value':['COMPLETED','UNREGISTERED']},
                {'fact':'slow.result','op':'eq','value':4950}]:
            verify['preconditions'] = [condition]
            feasibility._validate_step_order(policy, ['verify', 'fast'])

    def test_real_next_and_advance_keep_cost_feasible_prerequisite_block_explicit(self):
        next(n for n in self.contract['advisor_policy']['graph']['nodes'] if n['id'] == 'fast')['executable']['preconditions'] = [
            {'fact': 'slow.result', 'op': 'eq', 'value': 4950}]
        self.initialize()
        self.declare_cost_premise()
        before = self.store.snapshot()
        for command in ('next', 'advance'):
            report = self.cli('project', command)
            self.assertIsNone(report['selected_run'])
            self.assertIsNotNone(report['next_move'])
            self.assertEqual(report['feasibility']['next_action'], 'RESOLVE_EXECUTION_PREREQUISITES')
            self.assertEqual(report['feasibility']['execution_readiness'], 'BLOCKED')
            self.assertIsNotNone(report['feasibility']['repair_request'])
            self.assertIn('EXECUTION_PREREQUISITE_BLOCK', [w['kind'] for w in report['warnings']])
            self.assertEqual(next(p for p in report['feasibility']['plans'] if p['id']=='fast')['status'], 'FEASIBLE')
            steps = next(p for p in report['feasibility']['plans'] if p['id']=='fast')['steps']
            self.assertTrue(all(s['basis'] == 'TEST_DECLARED_COST_PREMISE' for s in steps))
            self.assertNotIn('receipt', report)
        after = self.store.snapshot()
        self.assertEqual(after['runs'], before['runs'])
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertEqual(self.launches(), [])
        self.assertEqual(after['receipts'], [])
        self.assertEqual(after['budget']['wall_seconds']['spent_measured'], 0)

    def test_unknown_is_explicit_and_only_bounded_pilot_is_selected(self):
        self.initialize()
        report = feasibility.assess(self.store, self.state())
        self.assertEqual({p['status'] for p in report['plans']}, {'UNKNOWN'})
        self.assertTrue(all(p['upper_wall_seconds'] is None for p in report['plans']))
        result = self.cli('project', 'next')
        self.assertEqual(result['selected_run'], 'pilot-slow')
        self.assertEqual(self.launches(), [])

    def test_brief_block_preserves_tool_improvement_entry(self):
        self.initialize(lambda c: [p.update(delivery_wall_seconds=101) for p in c['advisor_policy']['feasibility']['plans']])
        report = self.cli('project', 'next', '--brief')
        self.assertEqual(report['next_move'], 'IMPROVE_TOOL')
        self.assertIn('project improve', report['tool_workbench_command'])
        self.assertEqual(report['feasibility']['next_action'], 'REVISE_METHOD_WITH_NEW_EVIDENCE')
        self.assertEqual(self.store.snapshot()['budget']['wall_seconds']['spent_measured'], 0)

    def test_goal_delivery_verification_and_recovery_are_required(self):
        for mutate in (lambda c: c['advisor_policy']['feasibility']['plans'][0]['steps'].remove('verify'),
                       lambda c: c['advisor_policy']['feasibility']['plans'][0].update(goal_facts=[]),
                       lambda c: c['advisor_policy']['feasibility']['plans'][0].pop('delivery_wall_seconds'),
                       lambda c: c['advisor_policy']['feasibility'].update(max_pilot_wall_seconds=101)):
            candidate = deepcopy(self.contract)
            mutate(candidate)
            with self.subTest(candidate=candidate['advisor_policy']['feasibility']):
                with self.assertRaises(ValueError):
                    feasibility.validate(self.store, candidate, candidate['advisor_policy'])

    def test_complete_plan_overhead_can_block_fast_method_with_executable_repair_request(self):
        self.initialize(lambda c: [p.update(delivery_wall_seconds=101) for p in c['advisor_policy']['feasibility']['plans']])
        report = self.cli('project', 'next')
        self.assertIsNone(report['selected_run'])
        self.assertEqual(report['feasibility']['next_action'], 'REVISE_METHOD_WITH_NEW_EVIDENCE')
        self.assertIn('project revise', report['feasibility']['repair_request']['command'])
        self.assertEqual(self.launches(), [])

    def test_applicability_changes_invalidate_forecast_and_timeout_is_not_measurement(self):
        # Only a real fast pilot is needed to test that estimate's identity.
        self.contract['advisor_policy']['feasibility']['models'].pop('slow')
        next(n for n in self.contract['advisor_policy']['graph']['nodes'] if n['id']=='pilot-fast')['executable']['preconditions'] = []
        self.initialize()
        result = self.cli('project', 'advance')
        self.assertEqual(result['receipt']['run_id'], 'pilot-fast')
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        original = self.state()
        manifests = {r['manifest']['id']:r['manifest'] for r in self.contract['advisor_policy']['routes']}
        model = self.contract['advisor_policy']['feasibility']['models']['fast']
        self.assertEqual(feasibility._estimate(self.store, original, 'fast', manifests['fast'], model)['status'], 'CONDITIONAL_FORECAST')
        before = self.store.snapshot()
        def pilot(s):
            return next(r for r in s['receipts'] if r['run_id'] == 'pilot-fast')
        for label, mutate in [
                ('code', lambda s: s['contract']['bindings'][0].update(sha256='0'*64)),
                ('hardware', lambda s: pilot(s).update(runtime_fingerprint='0'*64)),
                ('precision', lambda s: pilot(s)['protocol']['runtime_model_identity'].update(precision='float')),
                ('algorithm', lambda s: pilot(s)['protocol']['runtime_model_identity'].update(algorithm='renamed')),
                ('input', lambda s: pilot(s)['protocol'].update(data_sha256='0'*64)),
                ('timeout', lambda s: pilot(s).update(timeout=True, run_status='TIMED_OUT'))]:
            state = deepcopy(original)
            mutate(state)
            estimate = feasibility._estimate(self.store, state, 'fast', manifests['fast'], model)
            report = feasibility.assess(self.store, state)
            fast = next(p for p in report['plans'] if p['id'] == 'fast')
            with self.subTest(label=label):
                self.assertEqual(fast['status'], 'UNKNOWN')
                self.assertEqual(estimate['status'], 'UNKNOWN')
                self.assertIsNone(estimate['upper_wall_seconds'])
                self.assertNotIn('fast', report['admitted_runs'])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.launches(), ['pilot-fast'])

    def test_deadline_is_original_campaign_start_and_failed_step_cannot_repeat(self):
        self.initialize()
        # This obligation needs the original campaign start, not three pilots
        # that may legitimately exhaust their separate shared allowance.
        started = self.cli('project', 'advance')
        self.assertIn('receipt', started)
        self.assertEqual(started['receipt']['run_id'], 'pilot-slow')
        self.assertEqual(started['receipt']['run_status'], 'SUCCEEDED')
        state = self.state()
        before = self.store.snapshot()
        deadline = state['campaign_started']['started_at'] + self.contract['stop_policy']['wall_seconds']
        report = feasibility.assess(self.store, state, now=deadline + 1)
        self.assertEqual(report['deadline'], deadline)
        self.assertEqual(report['admitted_runs'], [])
        self.assertTrue(all(p['available_wall_seconds'] == 0 for p in report['plans']))
        failed = deepcopy(state)
        failed['receipts'].append({'run_id':'fast','run_status':'TIMED_OUT'})
        report = feasibility.assess(self.store, failed)
        self.assertEqual(next(p for p in report['plans'] if p['id']=='fast')['status'], 'REPAIR_REQUIRED')
        self.assertNotIn('fast', report['admitted_runs'])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.launches(), ['pilot-slow'])
        self.assertEqual(len(before['receipts']), 1)
        self.assertEqual(before['receipts'][0]['attempt_id'], started['receipt']['attempt_id'])

    def test_pilot_shared_allowance_accepts_boundary_and_rejects_excess(self):
        self.initialize()
        original = self.state()
        before = self.store.snapshot()
        # Explicit synthetic accounting inputs, not persisted or measured runs.
        for spent, allowed in ((2.8, True), (2.801, False), (5.2288159, False)):
            state = deepcopy(original)
            state['receipts'].append({'run_id': 'pilot-slow', 'run_status': 'SUCCEEDED',
                'resources': {'wall_seconds': {'measured': spent, 'charged_estimate': 0}}})
            report = feasibility.assess(self.store, state)
            with self.subTest(spent=spent):
                self.assertEqual(report['pilot_budget']['cap'], 10)
                self.assertEqual(report['pilot_budget']['spent_or_charged'], spent)
                self.assertEqual('pilot-fast' in report['bounded_pilots'], allowed)
                self.assertEqual('pilot-fast' in report['admitted_runs'], allowed)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.launches(), [])

    def test_declared_cost_premise_is_labelled_bound_and_never_creates_receipts(self):
        from feasibility_cli_fixture import declared_estimator, validate_premise
        self.initialize()
        estimate = self.declare_cost_premise()
        premise = json.loads(self.cost_premise.read_text(encoding='utf-8'))
        state = self.state()
        manifests = {r['manifest']['id']:r['manifest'] for r in self.contract['advisor_policy']['routes']}
        model = self.contract['advisor_policy']['feasibility']['models']['fast']
        before = self.store.snapshot()
        result = estimate(self.store, state, 'fast', manifests['fast'], model)
        self.assertEqual(result['basis'], 'TEST_DECLARED_COST_PREMISE')
        self.assertEqual(result['upper_wall_seconds'], 6)
        self.assertEqual(result['native_estimate']['status'], 'UNKNOWN')
        self.assertEqual(result['sources'], [])
        for field in ('root', 'contract', 'manifest', 'model'):
            changed, manifest, changed_model = deepcopy(state), deepcopy(manifests['fast']), deepcopy(model)
            changed_premise = deepcopy(premise)
            if field == 'root':
                changed_premise['root'] = str(self.root / 'other')
            elif field == 'contract':
                changed['contract']['description'] = 'changed'
            elif field == 'manifest':
                manifest['id'] = 'changed'
            else:
                changed_model['safety_factor'] = 4
            with self.subTest(field=field):
                invalid = declared_estimator(changed_premise, feasibility._estimate)(
                    self.store, changed, 'fast', manifest, changed_model)
                self.assertEqual(invalid['status'], 'UNKNOWN')
                self.assertIsNone(invalid['upper_wall_seconds'])
        for value in (7.0, True):
            changed_premise = deepcopy(premise)
            changed_premise['routes']['fast']['upper_wall_seconds'] = value
            with self.subTest(bound=value), self.assertRaises(ValueError):
                validate_premise(changed_premise)
        stale = deepcopy(state)
        stale['receipts'].append({'run_id':'pilot-fast', 'run_status':'FAILED', 'timeout':True})
        self.assertEqual(estimate(self.store, stale, 'fast', manifests['fast'], model)['status'], 'UNKNOWN')
        path = self.root / manifests['fast']['protocol']['path']
        original_bytes = path.read_bytes()
        try:
            path.write_text('{}', encoding='utf-8')
            self.assertEqual(estimate(self.store, state, 'fast', manifests['fast'], model)['status'], 'UNKNOWN')
        finally:
            path.write_bytes(original_bytes)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.launches(), [])

    def test_declared_costs_do_not_override_native_budget_or_deadline_denial(self):
        self.initialize()
        estimate = self.declare_cost_premise()
        original = self.state()
        before = self.store.snapshot()
        for boundary in ('budget', 'deadline'):
            state = deepcopy(original)
            if boundary == 'budget':
                next(b for b in state['budget'] if b['resource']=='wall_seconds')['spent'] = 80
            else:
                state['campaign_started'] = {'started_at':100}
            with self.subTest(boundary=boundary), patch.object(feasibility, '_estimate', estimate):
                report = feasibility.assess(self.store, state, now=281 if boundary=='deadline' else None)
                self.assertNotIn('fast', report['admitted_runs'])
                self.assertEqual(next(p for p in report['plans'] if p['id']=='fast')['status'], 'INFEASIBLE')
                with patch('rds_owned_advisor._state', return_value=state):
                    with self.store._db(True) as db, self.assertRaisesRegex(ValueError, 'before child launch'):
                        feasibility.check_start(self.store, db, 'fast')
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.launches(), [])

    def test_final_obligation_unknown_prevents_solver_launch(self):
        self.initialize(lambda c: c['advisor_policy']['feasibility']['models'].pop('verify'))
        self.declare_cost_premise()
        before = self.store.snapshot()
        for command in ('next', 'advance'):
            report = self.cli('project', command)
            self.assertIsNone(report['selected_run'])
            fast = next(p for p in report['feasibility']['plans'] if p['id']=='fast')
            self.assertEqual(fast['status'], 'UNKNOWN')
            self.assertEqual(fast['steps'][0]['basis'], 'TEST_DECLARED_COST_PREMISE')
            self.assertEqual(fast['steps'][1]['status'], 'UNKNOWN')
            self.assertNotIn('receipt', report)
        self.assertEqual(self.launches(), [])
        self.assertEqual(self.store.snapshot()['receipts'], [])
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])

    def test_shared_pilot_cap_counts_prior_failures_and_reserved_attempts(self):
        self.initialize()
        state = self.state()
        fake = {'run_id':'pilot-slow','run_status':'FAILED',
                'resources':{'wall_seconds':{'measured':3.1,'charged_estimate':0}}}
        state['receipts'].append(fake)
        report = feasibility.assess(self.store, state)
        self.assertEqual(report['pilot_budget']['spent_or_charged'], 3.1)
        self.assertEqual(report['bounded_pilots'], [])  # Each new reservation needs 7.2 of the original 10.
        state['receipts'][0]['run_id'] = 'historical-pilot'
        state['contract_history'][0]['contract']['advisor_policy']['feasibility']['pilots'].append('historical-pilot')
        self.assertEqual(feasibility.assess(self.store, state)['bounded_pilots'], [])

    def test_full_plan_nonwall_allowances_block_solver_before_launch(self):
        def configure(c):
            c['budget']['cpu_seconds'] = 10
            for route in c['advisor_policy']['routes']:
                route['manifest']['resource_estimates']['cpu_seconds'] = 8 if route['manifest']['id'] in {'slow','fast','verify'} else 0
        self.initialize(configure)
        report = self.cli('project', 'next')
        self.assertIsNone(report['selected_run'])
        self.assertEqual({p['status'] for p in report['feasibility']['plans']}, {'INFEASIBLE'})
        self.assertEqual(self.launches(), [])
        self.assertEqual(self.store.snapshot()['budget']['cpu_seconds']['charged_estimate'], 0)

    def test_future_verifier_hard_wall_reservation_blocks_before_any_launch(self):
        def configure(c):
            verify = next(r['manifest'] for r in c['advisor_policy']['routes'] if r['manifest']['id']=='verify')
            verify['timeout_seconds'] = 101
            verify['resource_estimates']['wall_seconds'] = 102
        self.initialize(configure)
        report = self.cli('project', 'next')
        self.assertIsNone(report['selected_run'])
        self.assertEqual({p['status'] for p in report['feasibility']['plans']}, {'INFEASIBLE'})
        self.assertEqual(report['feasibility']['repair_request']['kind'], 'IMPROVE_TOOL')
        self.assertEqual(self.launches(), [])

    def test_hard_reservation_is_not_substituted_for_remaining_deadline_eta(self):
        self.initialize()
        state = self.state()
        state['campaign_started'] = {'started_at':100}
        def tiny_forecast(store, state, run_id, manifest, model):
            return {'status':'CONDITIONAL_FORECAST','run_id':run_id,
                    'lower_wall_seconds':.1,'upper_wall_seconds':.1}
        with patch.object(feasibility, '_estimate', side_effect=tiny_forecast):
            report = feasibility.assess(self.store, state, now=278)  # Original deadline280; two seconds remain.
        self.assertEqual(next(p for p in report['plans'] if p['id']=='fast')['status'], 'FEASIBLE')
        self.assertIn('fast', report['admitted_runs'])

    def test_hosted_pilot_values_are_rejected_by_original_hard_timeout_gate(self):
        self.initialize()
        state = self.state()
        manifests = {r['manifest']['id']: r['manifest'] for r in self.contract['advisor_policy']['routes']}
        before = deepcopy(state)
        # Synthetic estimator inputs reproduce the numeric admission branch.
        # These declarations are not imported receipts or measured new attempts.
        def pilot(run_id, seconds):
            protocol = json.loads((self.root / manifests[run_id]['protocol']['path']).read_text(encoding='utf-8'))
            return {'run_id': run_id, 'run_status': 'SUCCEEDED', 'timeout': False,
                    'runtime_fingerprint': feasibility.runtime_fingerprint(),
                    'bindings_before': state['contract']['bindings'], 'bindings_after': state['contract']['bindings'],
                    'protocol': protocol, 'sha256': 'synthetic-unit-input',
                    'resources': {'wall_seconds': {'measured': seconds}}}
        for measured, upper in ((4.6477538, 27.8865228), (5.406, 32.436)):
            with self.subTest(measured=measured):
                state['receipts'] = [pilot('pilot-fast', measured), pilot('pilot-verify', .1)]
                report = feasibility.assess(self.store, state)
                fast = next(p for p in report['plans'] if p['id'] == 'fast')
                self.assertAlmostEqual(fast['steps'][0]['upper_wall_seconds'], upper)
                self.assertEqual(fast['status'], 'INFEASIBLE')
                self.assertEqual(fast['reason'], 'Conditional completion forecast exceeds a required step hard timeout')
                self.assertNotIn('fast', report['admitted_runs'])
                self.assertEqual(state['runs'], before['runs'])
                self.assertEqual(state['budget'], before['budget'])
                self.assertEqual(self.store.snapshot()['receipts'], [])
                self.assertEqual(self.launches(), [])


if __name__ == '__main__':
    unittest.main()
