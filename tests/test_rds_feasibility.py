"""Real CLI completion, denied child launches and applicability boundaries."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore
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
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), *args],
                              capture_output=True, text=True, encoding='utf-8', timeout=25)
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

    def pilots(self):
        results = [self.cli('project', 'advance') for _ in range(3)]
        self.assertEqual([r['receipt']['run_id'] for r in results], ['pilot-slow', 'pilot-fast', 'pilot-verify'])
        self.assertTrue(all(r['receipt']['run_status'] == 'SUCCEEDED' for r in results))
        return results

    def test_complete_plan_denies_slow_before_launch_and_independently_verifies_fast(self):
        self.initialize()
        self.pilots()
        report = feasibility.assess(self.store, self.state())
        plans = {p['id']: p for p in report['plans']}
        self.assertEqual(plans['slow']['status'], 'INFEASIBLE')
        self.assertEqual(plans['fast']['status'], 'FEASIBLE')
        self.assertEqual(report['admitted_runs'], ['fast'])
        self.assertEqual(len(plans['fast']['steps']), 2)
        self.assertGreater(plans['fast']['upper_wall_seconds'], sum(s['upper_wall_seconds'] for s in plans['fast']['steps']))
        slow = self.contract['advisor_policy']['routes'][3]['manifest']
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'did not select|feasibility gate'):
            self.store.register(slow)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        self.assertEqual(self.launches(), ['pilot-slow', 'pilot-fast', 'pilot-verify'])
        self.assertEqual(self.cli('project', 'advance')['receipt']['run_id'], 'fast')
        self.assertEqual(self.cli('project', 'advance')['receipt']['run_id'], 'verify')
        observed = json.loads((self.root / 'outputs/verify.json').read_text())
        self.assertEqual(observed, {'verified': True, 'observed': 4950})
        final = self.cli('project', 'next')
        self.assertIsNone(final['selected_run'])
        self.assertEqual(final['feasibility']['next_action'], 'GOAL_PREDICATES_CONFIRMED')
        self.assertIsNone(final['feasibility']['repair_request'])
        self.assertEqual(self.launches(), ['pilot-slow', 'pilot-fast', 'pilot-verify', 'fast', 'verify'])
        snap = self.store.snapshot()
        self.assertEqual(len(snap['receipts']), 5)
        self.assertEqual(snap['budget']['wall_seconds']['reserved'], 0)
        self.assertGreater(snap['budget']['wall_seconds']['spent_measured'], 0)

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
        self.initialize()
        self.pilots()
        original = self.state()
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
            report = feasibility.assess(self.store, state)
            fast = next(p for p in report['plans'] if p['id'] == 'fast')
            with self.subTest(label=label):
                self.assertEqual(fast['status'], 'UNKNOWN')
                self.assertNotIn('fast', report['admitted_runs'])

    def test_deadline_is_original_campaign_start_and_failed_step_cannot_repeat(self):
        self.initialize()
        self.pilots()
        state = self.state()
        deadline = state['campaign_started']['started_at'] + self.contract['stop_policy']['wall_seconds']
        report = feasibility.assess(self.store, state, now=deadline + 1)
        self.assertEqual(report['admitted_runs'], [])
        self.assertTrue(all(p['available_wall_seconds'] == 0 for p in report['plans']))
        failed = deepcopy(state)
        failed['receipts'].append({'run_id':'fast','run_status':'TIMED_OUT'})
        report = feasibility.assess(self.store, failed)
        self.assertEqual(next(p for p in report['plans'] if p['id']=='fast')['status'], 'REPAIR_REQUIRED')
        self.assertNotIn('fast', report['admitted_runs'])

    def test_final_obligation_unknown_prevents_solver_launch(self):
        self.initialize(lambda c: c['advisor_policy']['feasibility']['models'].pop('verify'))
        self.cli('project', 'advance')
        self.cli('project', 'advance')
        report = self.cli('project', 'next')
        self.assertIsNone(report['selected_run'])
        self.assertEqual(next(p for p in report['feasibility']['plans'] if p['id']=='fast')['status'], 'UNKNOWN')
        self.assertEqual(self.launches(), ['pilot-slow', 'pilot-fast'])

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


if __name__ == '__main__':
    unittest.main()
