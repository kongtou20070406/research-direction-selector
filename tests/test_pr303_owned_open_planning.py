"""Owned repartition regressions; synthetic receipts are not scientific proof."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor import RDSAdvisor
from rds_owned_advisor import review
from rds_project import ProjectStore
from rds_tms_store import current, save
import test_rds_owned_advisor as owned_fixture


class OwnedOpenPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = owned_fixture.OwnedAdvisorCLITests(methodName='runTest')
        cls.harness.setUp()
        cls.addClassCleanup(cls.harness.doCleanups)

        def mutate(policy):
            repair = policy['graph']['nodes'][1]['executable']
            repair['preconditions'] = []
            repair['action'].update(target='repair.score', goal_contribution={
                'target': 'repair.score', 'path': ['repair.score'], 'source': 'synthetic frozen plan'})
            policy['context']['decision']['affirmations'] = {
                'portable': [{'fact': 'repair.score', 'op': 'gte', 'value': .5}]}

        cls.harness.initialize('positive', mutate_policy=mutate)
        cls.harness.create()
        cls.harness.execute()
        saved = current(cls.harness.root)
        dependency = deepcopy(saved['dependency_map'])
        dependency['nodes'].append({'id': 'repair.score', 'status': 'UNKNOWN',
                                    'source': 'synthetic open obligation'})
        dependency['goals'].append('repair.score')
        save(cls.harness.root, dependency, expected=saved['sha256'])

    def report(self, *, completion=False, closed=False, rejected=False):
        original = RDSAdvisor._recommend_next_directions

        def scoped_context(advisor, state, graph, **kwargs):
            state = deepcopy(state)
            graph = deepcopy(graph)
            context = state['advisor_context']
            if completion or closed:
                del context['decision']['affirmations']
            if completion:
                # Supply the bound-completion API seam; the real search and
                # Owned admission gates still compute every candidate report.
                context['objective_binding'] = {'sha256': 'synthetic binding only'}
                dependency = context['dependency_map']
                dependency['nodes'].append({'id': 'completion_standard', 'status': 'UNKNOWN',
                                            'source': 'synthetic bound completion'})
                dependency['goals'].append('completion_standard')
                dependency['hyperedges'].append({'id': 'completion-link', 'premises': ['repair.score'],
                    'conclusion': 'completion_standard', 'status': 'SUPPORTED',
                    'source': 'synthetic declared bridge'})
                graph['nodes'][1]['executable']['action']['goal_contribution'].update(
                    target='completion_standard', path=['repair.score', 'completion_standard'])
            if rejected:
                graph['nodes'][1]['executable']['preconditions'] = [
                    {'fact': 'missing_prerequisite', 'value': True}]
            return original(advisor, state, graph, **kwargs)

        before = self.harness.snapshot()
        with patch.object(RDSAdvisor, '_recommend_next_directions', scoped_context):
            result = review(ProjectStore(self.harness.root), persist=False)
        after = self.harness.snapshot()
        self.assertEqual(after, before)
        self.assertEqual(self.harness.starts(), ['baseline'])
        self.assertEqual(result['status'], 'REVIEWED')
        self.assertEqual(result['authorization'], 'UNCHANGED')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertIsNone(result['selected_run'])
        self.assertIsNone(result['selected_manifest'])
        search = next(row['search'] for row in result['recommendations'] if 'search' in row)
        self.assertEqual(search['selection_review']['goal']['status'], 'TRUE')
        return result, search

    def assert_open_local(self, completion=False):
        result, search = self.report(completion=completion)
        target = 'completion_standard' if completion else 'repair.score'
        candidate = next(c for c in search['candidates'] if c['action'].get('id') == 'repair')
        self.assertEqual(candidate['status'], 'READY')
        selection = search['selection_review']
        self.assertEqual(selection['planning']['scope'], 'LOCAL')
        self.assertEqual(next(c for c in selection['candidates'] if c['id'] == candidate['id'])[
            'goal_contribution']['status'], 'DECLARED_PATH')
        if completion:
            self.assertEqual(selection['dependency_review']['goals'][target]['status'], 'UNKNOWN')
        else:
            self.assertEqual(selection['goal']['triple_affirmative']['portable']['status'], 'UNKNOWN')
        self.assertIn(target, result['planning']['open_predicates'])
        self.assertEqual(result['context']['facts']['repair.score']['value'], None)
        self.assertEqual(result['planning']['scope'], 'LOCAL')
        self.assertEqual(result['planning']['local_checks'], [
            {'candidate': candidate['id'], 'target': 'repair.score', 'goal_target': target}])

    def test_true_ordinary_goal_keeps_open_affirmative_check(self):
        self.assert_open_local()

    def test_true_ordinary_goal_keeps_bound_completion_check(self):
        self.assert_open_local(completion=True)

    def test_closed_goal_clears_local_checks(self):
        result, _ = self.report(closed=True)
        self.assertEqual(result['planning']['scope'], 'UNSCOPED')
        self.assertEqual(result['planning']['open_predicates'], [])
        self.assertEqual(result['planning']['local_checks'], [])

    def test_rejected_affirmative_candidate_cannot_become_local(self):
        result, search = self.report(rejected=True)
        self.assertFalse(any(c['action'].get('id') == 'repair' and c['status'] == 'READY'
                             for c in search['candidates']))
        self.assertEqual(result['planning']['scope'], 'GLOBAL')
        self.assertIn('repair.score', result['planning']['open_predicates'])
        self.assertEqual(result['planning']['local_checks'], [])

    def test_rejected_completion_candidate_cannot_become_local(self):
        result, _ = self.report(completion=True, rejected=True)
        self.assertEqual(result['planning']['scope'], 'GLOBAL')
        self.assertIn('completion_standard', result['planning']['open_predicates'])
        self.assertEqual(result['planning']['local_checks'], [])


if __name__ == '__main__':
    unittest.main()
