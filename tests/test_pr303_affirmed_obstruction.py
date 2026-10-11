"""PR303 affirmative obstructions use reviewed evidence, never declarations alone."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor import RDSAdvisor
from rds_advisor_search import _obstruction_review, review_obstructions, review_selection, search_directions
from rds_owned_advisor import review as owned_review
from rds_project import ProjectStore
from test_pr303_planning_review import affirmative_check_fixture, observed
from test_rds_capability_requirement import obstruction, REQUIREMENT
import test_rds_owned_advisor as owned_fixture


def reviewed(graph, context):
    search = search_directions(graph, context)
    search['selection_review'] = review_selection(search, context)
    return search


class AffirmedObstructionTests(unittest.TestCase):
    def test_collision_never_overwrites_an_open_goal_or_affirmative_state(self):
        for group in ('portable', 'applicable'):
            for truth, affirms in (('UNKNOWN', 'TRUE'), ('TRUE', 'UNKNOWN'),
                                   ('FALSE', 'TRUE'), ('TRUE', 'FALSE'), ('TRUE', 'TRUE')):
                with self.subTest(group=group, truth=truth, affirms=affirms):
                    _, context = affirmative_check_fixture(group, True)
                    target = group + '_gap'
                    context['decision']['goal_conditions'] = [{'fact': target, 'value': True}]
                    # Exercise the consumer contract directly with colliding review
                    # states; neither state may overwrite the other's open predicate.
                    goal = {'conditions': [{'fact': target, 'truth': truth}],
                            'triple_affirmative': {group: {'conditions': [
                                {'fact': target, 'affirms': affirms}]}}}
                    original = deepcopy((context, goal))
                    entries = _obstruction_review([obstruction(target, 'MISSING_INPUT')], context, goal)
                    self.assertEqual(entries[0]['status'], 'NOT_APPLICABLE'
                                     if truth == affirms == 'TRUE' else 'APPLICABLE')
                    self.assertEqual((context, goal), original)

    def test_closed_affirmative_does_not_steer_an_unrelated_open_goal(self):
        for group in ('portable', 'applicable'):
            for cause in ('MISSING_INPUT', 'UNSUPPORTED_OPERATION'):
                with self.subTest(group=group, cause=cause):
                    graph, context = affirmative_check_fixture(group, True)
                    context['facts']['goal'] = observed(False, '/goal')
                    search = reviewed(graph, context)
                    before = deepcopy(search['selection_review'])
                    context['obstructions'] = [obstruction(group + '_gap', cause, requirement=REQUIREMENT)]
                    original = deepcopy((graph, context))
                    review_obstructions(search, context)
                    selection = search['selection_review']
                    self.assertEqual(selection['goal']['triple_affirmative'][group]['status'], 'TRUE')
                    self.assertEqual(selection['obstruction_review'][0]['status'], 'NOT_APPLICABLE')
                    self.assertEqual(selection['next_move'], before['next_move'])
                    self.assertEqual(selection['planning'], before['planning'])
                    self.assertEqual(selection['candidates'], before['candidates'])
                    self.assertEqual(selection['goal']['status'], 'FALSE')
                    self.assertEqual(selection['authorization'], 'UNCHANGED')
                    self.assertEqual((graph, context), original)

    def test_unknown_failed_reported_and_reused_affirmatives_remain_applicable_and_local(self):
        for group in ('portable', 'applicable'):
            for state in ('missing', 'failed', 'reported', 'reused'):
                with self.subTest(group=group, state=state):
                    graph, context = affirmative_check_fixture(group, False if state == 'failed' else None)
                    target = group + '_gap'
                    if state == 'reported':
                        context['facts'][target] = {'value': True, 'source': 'reported-only.json'}
                    elif state == 'reused':
                        context['facts'][target] = deepcopy(context['facts']['goal'])
                    search = reviewed(graph, context)
                    before = deepcopy(search['selection_review'])
                    context['obstructions'] = [obstruction(target, 'MISSING_INPUT', requirement=REQUIREMENT)]
                    review_obstructions(search, context)
                    selection = search['selection_review']
                    row = selection['goal']['triple_affirmative'][group]['conditions'][1]
                    self.assertEqual(row['affirms'], 'FALSE' if state == 'failed' else 'UNKNOWN')
                    self.assertEqual(selection['obstruction_review'][0]['status'], 'APPLICABLE')
                    self.assertEqual(selection['next_move']['kind'], before['next_move']['kind'])
                    self.assertNotIn('supersedes', selection['next_move'])
                    self.assertEqual(selection['next_move']['obstructions'][0]['response'], 'EVIDENCE_REPAIR')
                    self.assertEqual(selection['candidates'][0]['goal_contribution']['status'], 'DECLARED_PATH')
                    self.assertEqual(selection['planning']['scope'], 'LOCAL')
                    self.assertIn(target, selection['planning']['open_predicates'])
                    self.assertEqual(selection['planning'], before['planning'])
                    self.assertEqual(selection['authorization'], 'UNCHANGED')

    def test_all_predicates_for_a_fact_must_affirm_before_closing_obstruction(self):
        for group in ('portable', 'applicable'):
            for placement in ('same_group', 'other_group', 'goal'):
                with self.subTest(group=group, placement=placement):
                    graph, context = affirmative_check_fixture(group, True)
                    target = group + '_gap'
                    failed = {'fact': target, 'value': False}
                    if placement == 'goal':
                        context['decision']['goal_conditions'].append(failed)
                    else:
                        owner = group if placement == 'same_group' else (
                            'applicable' if group == 'portable' else 'portable')
                        context['decision']['affirmations'][owner].append(failed)
                    search = reviewed(graph, context)
                    context['obstructions'] = [obstruction(target, 'MISSING_INPUT')]
                    review_obstructions(search, context)
                    self.assertEqual(search['selection_review']['obstruction_review'][0]['status'], 'APPLICABLE')

    def test_found_reported_true_remains_open_for_obstruction(self):
        graph, context = affirmative_check_fixture('portable', True)
        context['facts']['goal'] = {'value': True, 'source': 'reported-only.json'}
        search = reviewed(graph, context)
        context['obstructions'] = [obstruction('goal', 'MISSING_INPUT')]
        review_obstructions(search, context)
        selection = search['selection_review']
        self.assertEqual(selection['goal']['conditions'][0]['truth'], 'TRUE')
        self.assertEqual(selection['goal']['triple_affirmative']['found']['conditions'][0]['affirms'], 'UNKNOWN')
        self.assertEqual(selection['obstruction_review'][0]['status'], 'APPLICABLE')
        self.assertEqual(selection['next_move']['kind'], 'RESOLVE_PREMISE')


class OwnedAffirmedObstructionTests(unittest.TestCase):
    def test_imported_affirmative_does_not_steer_owned_open_goal(self):
        for group in ('portable', 'applicable'):
            with self.subTest(group=group):
                harness = owned_fixture.OwnedAdvisorCLITests(methodName='runTest')
                harness.setUp()
                self.addCleanup(harness.doCleanups)

                def mutate(policy):
                    policy['context']['decision']['goal_conditions'] = [
                        {'fact': 'repair.score', 'op': 'gte', 'value': .5}]
                    policy['context']['decision']['affirmations'] = {
                        group: [{'fact': 'baseline.score', 'op': 'eq', 'value': -1}]}

                harness.initialize(mutate_policy=mutate)
                harness.create()
                harness.execute()
                store = ProjectStore(harness.root)
                before = harness.snapshot()
                baseline = owned_review(store, persist=False)
                original_recommend = RDSAdvisor._recommend_next_directions

                def with_obstruction(advisor, snapshot, graph, **kwargs):
                    # Owned policy excludes obstructions; supply this declaration at
                    # the real Advisor boundary, retaining the same context identity
                    # for Owned Advisor's downstream admission checks.
                    snapshot['advisor_context']['obstructions'] = [
                        obstruction('baseline.score', 'MISSING_INPUT', requirement=REQUIREMENT)]
                    return original_recommend(advisor, snapshot, graph, **kwargs)

                with mock.patch.object(RDSAdvisor, '_recommend_next_directions', with_obstruction):
                    result = owned_review(store, persist=False)
                search = next(row['search'] for row in result['recommendations'] if 'search' in row)
                selection = search['selection_review']
                row = selection['goal']['triple_affirmative'][group]['conditions'][0]
                self.assertEqual((row['evidence_status'], row['affirms']), ('ARTIFACT_OBSERVED', 'TRUE'))
                self.assertEqual(selection['goal']['status'], 'UNKNOWN')
                self.assertEqual(selection['obstruction_review'][0]['status'], 'NOT_APPLICABLE')
                self.assertEqual(result['next_move'], baseline['next_move'])
                self.assertEqual(result['selected_run'], baseline['selected_run'])
                self.assertEqual(result['planning'], baseline['planning'])
                self.assertEqual(result['scientific_support'], 'UNKNOWN')
                self.assertEqual(result['authorization'], 'UNCHANGED')
                self.assertEqual(harness.snapshot(), before)
                self.assertEqual(harness.starts(), ['baseline'])


if __name__ == '__main__':
    unittest.main()
