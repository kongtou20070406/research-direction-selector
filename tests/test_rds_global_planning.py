"""Actual CLI planning, preserved admission and original-evidence boundaries.

Synthetic public cases test software behavior. They do not measure research gain.
"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import test_rds_owned_advisor as fixtures
from rds_global_planning import inspect_plan
from rds_project import ProjectStore
from rds_tms_store import current, save


class GlobalPlanningTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OwnedAdvisorCLITests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    @staticmethod
    def independent(policy):
        for node in policy['graph']['nodes']:
            node['executable']['preconditions'] = []
        policy['context']['decision']['goal_conditions'] = [
            {'fact': 'baseline.score', 'op': 'gte', 'value': .5},
            {'fact': 'repair.score', 'op': 'gte', 'value': .5}]

    def test_actual_cli_can_suggest_a_different_goal_connected_action_without_changing_selection(self):
        def mutate(policy):
            self.independent(policy)
            policy['observations'].append({**deepcopy(policy['observations'][1]), 'fact': 'repair.second'})
            policy['context']['decision']['goal_conditions'].append({'fact': 'repair.second', 'op': 'gte', 'value': .5})
        self.f.initialize(mode='positive', mutate_policy=mutate)
        store = ProjectStore(self.f.root)
        before = self.f.snapshot()
        with store._db(True) as db:
            events_before = db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        plan = self.f.output('project', 'plan', '--shadow')
        self.assertEqual(plan['status'], 'CURRENT')
        self.assertEqual(plan['summary']['open_goal_count'], 3)
        self.assertEqual(plan['comparison']['current_advisor_run'], 'baseline')
        self.assertEqual(plan['comparison']['shadow_suggested_run'], 'repair')
        self.assertTrue(plan['comparison']['different'])
        self.assertFalse(plan['selection_applied'])
        self.assertEqual(plan['next_small_check']['open_goal_links'], ['repair.score', 'repair.second'])
        self.assertEqual(self.f.snapshot()['runs'], before['runs'])
        self.assertEqual(self.f.snapshot()['budget'], before['budget'])
        self.assertIsNone(current(self.f.root))
        with store._db(True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], events_before)
        self.assertEqual(self.f.starts(), [])
        self.assertEqual(self.f.output('project', 'next')['selected_run'], 'baseline')
        brief = self.f.output('advise', '--working-set', '--brief')
        self.assertEqual(brief['shadow_plan']['summary']['suggested_run'], 'repair')
        self.assertEqual(brief['working_set']['shadow_plan']['comparison']['current_advisor_run'], 'baseline')

    def test_real_feedback_refreshes_milestones_and_keeps_open_and_goal_after_local_success(self):
        self.f.initialize(mode='positive', mutate_policy=self.independent)
        original = self.f.output('project', 'plan', '--shadow')
        self.f.output('project', 'advance')
        updated = self.f.output('project', 'plan', '--shadow')
        self.assertEqual(updated['summary']['open_goal_count'], 1)
        goals = {m['fact']: m for m in updated['milestones']['items']}
        self.assertEqual(goals['baseline.score']['status'], 'TRUE')
        self.assertEqual(goals['repair.score']['status'], 'UNKNOWN')
        self.assertEqual(updated['next_small_check']['run_id'], 'repair')
        self.assertNotEqual(updated['source']['fingerprint'], original['source']['fingerprint'])
        receipt_sha = self.f.snapshot()['receipts'][0]['sha256']
        self.f.output('project', 'advance')
        final = self.f.output('project', 'plan', '--shadow')
        self.assertEqual(final['summary']['open_goal_count'], 0)
        self.assertIsNone(final['next_small_check'])
        self.assertEqual(final['scientific_support'], 'UNKNOWN')
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertEqual(next(r for r in self.f.snapshot()['receipts'] if r['run_id'] == 'baseline')['sha256'], receipt_sha)

    def test_exhausted_producers_request_goal_bridge_repair_without_rerunning_or_refuting(self):
        self.f.initialize()
        self.f.output('project', 'advance')
        self.f.output('project', 'advance')
        before = self.f.snapshot()
        plan = self.f.output('project', 'plan', '--shadow')
        self.assertIn('REPAIR_GOAL_BRIDGE', plan['summary']['request_kinds'])
        self.assertIn('RESOLVE_CURRENT_BLOCKER', plan['summary']['request_kinds'])
        self.assertEqual(plan['summary']['open_goal_count'], 1)
        self.assertIsNone(plan['next_small_check'])
        self.assertEqual(self.f.starts(), ['baseline', 'repair'])
        self.assertEqual(self.f.snapshot()['budget'], before['budget'])
        self.assertFalse(plan['execution_authorized'])

    def test_original_collection_failure_makes_shadow_unavailable(self):
        self.f.initialize(mode='badjson')
        self.f.create()
        self.f.execute(ok=False)
        before = self.f.snapshot()
        plan = self.f.output('project', 'plan', '--shadow')
        self.assertEqual(plan['status'], 'UNAVAILABLE')
        self.assertEqual(plan['owned_status'], 'COLLECTION_FAILED')
        self.assertEqual(self.f.snapshot()['receipts'], before['receipts'])
        self.assertEqual(self.f.starts(), ['baseline'])

    def test_goal_focus_uses_current_admitted_routes_and_rejects_unfrozen_goal(self):
        self.f.initialize(mode='positive', mutate_policy=self.independent)
        plan = self.f.output('project', 'plan', '--shadow', '--goal', 'repair.score')
        self.assertEqual(plan['milestones']['total'], 1)
        self.assertEqual(plan['source']['focused_goal'], 'repair.score')
        self.assertEqual(plan['next_small_check']['run_id'], 'repair')
        self.assertEqual(plan['goal_cone']['roots'], ['owned:goal:1'])
        self.assertNotEqual(self.f.call('project', 'plan', '--shadow', '--goal', 'invented', ok=False).returncode, 0)
        self.assertNotEqual(self.f.call('project', 'plan', '--goal', 'repair.score', ok=False).returncode, 0)
        self.assertNotEqual(self.f.call('project', 'plan', '--shadow', '--save-as', 'silent-write', ok=False).returncode, 0)

    def test_pause_and_current_human_preference_keep_precedence_over_shadow_heuristic(self):
        from rds_steering import submit
        self.f.initialize(mode='positive', mutate_policy=self.independent)
        store = ProjectStore(self.f.root)
        snap = self.f.snapshot()
        submit(store, {'id': 'pause', 'kind': 'pause', 'message': 'Pause new work',
               'contract_sha256': snap['contract_sha256'], 'expected_revision': None},
               user_directed=True, source='synthetic direct user instruction')
        plan = self.f.output('project', 'plan', '--shadow')
        self.assertIsNone(plan['next_small_check'])
        self.assertEqual(plan['comparison']['precedence'], 'CURRENT_USER_INSTRUCTION')
        self.assertEqual(self.f.starts(), [])
        snap = self.f.snapshot()
        submit(store, {'id': 'resume', 'kind': 'resume', 'message': 'Resume work',
               'contract_sha256': snap['contract_sha256'], 'expected_revision': snap['steering']['revision']},
               user_directed=True, source='synthetic direct user instruction')
        snap = self.f.snapshot()
        submit(store, {'id': 'prefer', 'kind': 'redirect', 'message': 'Prefer baseline',
               'prefer': ['baseline'], 'contract_sha256': snap['contract_sha256'],
               'expected_revision': snap['steering']['revision']},
               user_directed=True, source='synthetic direct user instruction')
        plan = self.f.output('project', 'plan', '--shadow', '--goal', 'repair.score')
        self.assertEqual(plan['comparison']['shadow_suggested_run'], 'baseline')
        self.assertEqual(plan['comparison']['precedence'], 'CURRENT_USER_INSTRUCTION')

    def test_goal_cone_bounds_are_visible_through_actual_cli(self):
        self.f.initialize()
        self.f.output('project', 'next')
        saved = current(self.f.root)
        spec = deepcopy(saved['dependency_map'])
        names = [f'synthetic-premise-{i}' for i in range(40)]
        spec['nodes'].extend({'id': n, 'status': 'UNKNOWN', 'source': {'locator': 'synthetic public input'}} for n in names)
        spec['hyperedges'].append({'id': 'synthetic-wide-and', 'premises': names,
            'conclusion': 'owned:fact:baseline.score', 'status': 'PROPOSED',
            'source': {'locator': 'synthetic public AND dependency'}})
        save(self.f.root, spec, expected=saved['sha256'])
        plan = self.f.output('project', 'plan', '--shadow')
        self.assertTrue(plan['goal_cone']['truncated'])
        self.assertLessEqual(plan['goal_cone']['included_nodes'], 32)
        self.assertGreater(plan['goal_cone']['input_nodes'], 40)
        self.assertIn('INSPECT_OMITTED_SCOPE', plan['summary']['request_kinds'])
        self.assertFalse(plan['goal_cone']['complete_global_search'])

    def test_complete_forecast_alternatives_remain_conditional_and_do_not_start(self):
        spec = importlib.util.spec_from_file_location('_global_forecast_fixture', ROOT / 'examples/predictive-feasibility/prepare.py')
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        contract = fixture.prepare(self.f.root)
        ProjectStore(self.f.root).initialize(contract)
        before = ProjectStore(self.f.root).snapshot()
        plan = self.f.output('project', 'plan', '--shadow')
        self.assertEqual(plan['summary']['declared_complete_plans'], 2)
        self.assertEqual({r['id'] for r in plan['strategic_alternatives']['items']}, {'fast', 'slow'})
        self.assertTrue(all(r['status'] == 'UNKNOWN' for r in plan['strategic_alternatives']['items']))
        self.assertEqual(ProjectStore(self.f.root).snapshot()['budget'], before['budget'])
        self.assertEqual(self.f.starts(), [])

    def test_concurrent_cut_is_unavailable_and_does_not_export_a_current_suggestion(self):
        self.f.initialize()
        store = ProjectStore(self.f.root)
        with patch('rds_owned_advisor._fingerprint', side_effect=['first-cut', 'new-cut']):
            plan = inspect_plan(store)
        self.assertEqual(plan['status'], 'UNAVAILABLE')
        self.assertIsNone(plan['next_small_check'])
        self.assertEqual(self.f.starts(), [])


if __name__ == '__main__':
    unittest.main()
