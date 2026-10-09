"""Actual AND/OR blocker consumption stays distinct from declared scientific truth."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor_search import search_directions
from rds_hypergraph import analyze_hypergraph
from rds_quick import choice, brief


def fixture():
    dependency = json.loads((ROOT / 'examples/goal-linked-hypergraph.json').read_text())
    action = {'id': 'lower-check', 'kind': 'OBLIGATION_CHECK', 'target': 'unrestricted_lower', 'description': 'Check the unrestricted lower bound',
              'claim': 'A bound on the complete original domain', 'required_observables': ['certificate'],
              'outcomes': [{'observation': label, 'next_decision': label}
                           for label in ('verified', 'counterexample', 'unresolved')],
              'goal_contribution': {'target': 'completion_standard',
                  'path': ['unrestricted_lower', 'completion_standard'], 'source': 'synthetic contract'}}
    graph = {'nodes': [{'id': 'lower-route', 'executable': {
        'decisions': ['next'], 'preconditions': [], 'action': action}}], 'edges': []}
    context = {'decision': {'id': 'next', 'goal_revision': '1', 'scope': {'domain': 'synthetic'}},
               'objective_binding': {'sha256': 'synthetic-unit-fixture-not-authorization'},
               'facts': {}, 'dependency_map': dependency}
    return graph, context


class GoalDependencyTests(unittest.TestCase):
    def test_and_premises_and_proposed_or_bridge_survive_a_connected_path(self):
        graph, context = fixture()
        before = deepcopy((graph, context))
        search = search_directions(graph, context)
        review = search['selection_review']
        mapped = review['candidates'][0]['goal_contribution']['graph_path']
        self.assertEqual(mapped['status'], 'DECLARED_CONNECTED_PATH')
        self.assertEqual(mapped['goal_review']['minimal_missing_evidence_sets'], [
            ['node:alternative_proof', 'rule:independent_route'],
            ['node:constructive_upper', 'node:matching_value', 'node:unrestricted_lower']])
        self.assertEqual(mapped['goal_review']['status'], 'UNKNOWN')
        self.assertEqual(search['candidates'][0]['status'], 'READY')
        self.assertEqual(review['authorization'], 'UNCHANGED')
        self.assertEqual((graph, context), before)

    def test_unrelated_supported_work_does_not_close_the_original_goal(self):
        graph, context = fixture()
        result = analyze_hypergraph(context['dependency_map'])
        self.assertEqual(result['declared_supported_closure'], ['checked_format', 'closed_small_case'])
        self.assertNotIn('completion_standard', result['declared_supported_closure'])
        graph['nodes'][0]['executable']['action']['target'] = 'closed_small_case'
        graph['nodes'][0]['executable']['action']['goal_contribution']['path'][0] = 'closed_small_case'
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['next_move']['kind'], 'REVIEW_GOAL_LINK')
        self.assertEqual(review['candidates'][0]['goal_contribution']['status'], 'UNKNOWN')

    def test_path_must_match_actual_proof_target_and_existing_direction(self):
        for path in (['constructive_upper', 'completion_standard'],
                     ['unrestricted_lower', 'constructive_upper', 'completion_standard'],
                     ['unrestricted_lower', 'missing', 'completion_standard'],
                     ['unrestricted_lower', 'unrestricted_lower', 'completion_standard'],
                     ['unrestricted_lower']):
            with self.subTest(path=path):
                graph, context = fixture()
                graph['nodes'][0]['executable']['action']['goal_contribution']['path'] = path
                contribution = search_directions(graph, context)['selection_review']['candidates'][0]['goal_contribution']
                self.assertEqual(contribution['status'], 'UNKNOWN')

    def test_nonproof_link_binds_actual_target_without_changing_ready(self):
        for mode in ('empirical', 'mixed'):
            for target in ('closed_small_case', None):
                with self.subTest(mode=mode, target=target):
                    graph, context = fixture()
                    context.update(require_goal_link=True, research_mode=mode)
                    action = graph['nodes'][0]['executable']['action']
                    action.update(kind='PAIRED_TEST', competing_explanations=['A', 'B'])
                    if target is None:
                        del action['target']
                    else:
                        action['target'] = target
                    before = deepcopy((graph, context))
                    search = search_directions(graph, context)
                    self.assertEqual(search['candidates'][0]['status'], 'READY')
                    contribution = search['selection_review']['candidates'][0]['goal_contribution']
                    self.assertEqual(contribution['status'], 'UNKNOWN')
                    self.assertIn('action.target', contribution['reason'])
                    advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
                    with self.assertRaisesRegex(ValueError, 'complete actual dependency map'):
                        choice(advice, context)
                    self.assertEqual((graph, context), before)
                    action['target'] = 'unrestricted_lower'
                    advice['recommendations'][0]['search'] = search_directions(graph, context)
                    self.assertEqual(choice(advice, context)['goal_guard']['ready_obligation'],
                                     'node:unrestricted_lower')

    def test_text_link_also_requires_its_actual_action_target(self):
        graph, context = fixture()
        del context['dependency_map']
        action = graph['nodes'][0]['executable']['action']
        action.update(kind='PAIRED_TEST', competing_explanations=['A', 'B'])
        del action['target']
        search = search_directions(graph, context)
        self.assertEqual(search['candidates'][0]['status'], 'READY')
        self.assertEqual(search['selection_review']['candidates'][0]['goal_contribution']['status'], 'UNKNOWN')
        action['target'] = 'unrestricted_lower'
        search = search_directions(graph, context)
        self.assertEqual(search['selection_review']['candidates'][0]['goal_contribution']['status'], 'DECLARED_PATH')

    def test_truncation_or_bad_map_is_unknown_without_claiming_no_blockers(self):
        for damage in ('truncated', 'unknown-premise', 'oversized'):
            with self.subTest(damage=damage):
                graph, context = fixture()
                if damage == 'truncated':
                    context['dependency_map']['limits'] = {'max_combinations': 1}
                elif damage == 'unknown-premise':
                    context['dependency_map']['hyperedges'][0]['premises'].append('missing')
                else:
                    context['dependency_map']['extra'] = 'x' * (128 * 1024)
                search = search_directions(graph, context)
                review = search['selection_review']
                self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')
                self.assertEqual(search['candidates'][0]['local_status'], 'READY')
                self.assertEqual(search['candidates'][0]['status'], 'NEEDS_COMPLETE_ANALYSIS')
                self.assertFalse(search['analysis_coverage']['full'])
                self.assertIn('DEPENDENCY_MAP_INCOMPLETE', [f['kind'] for f in review['flags']])
                advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
                with self.assertRaisesRegex(ValueError, 'Complete graph analysis required'):
                    choice(advice, context)

    def test_a_healthy_link_is_not_overridden_by_unrelated_invalid_candidate(self):
        graph, context = fixture()
        unrelated = deepcopy(graph['nodes'][0])
        unrelated['id'] = 'unrelated'
        unrelated['executable']['action']['target'] = 'closed_small_case'
        unrelated['executable']['action']['goal_contribution']['path'][0] = 'closed_small_case'
        graph['nodes'].append(unrelated)
        review = search_directions(graph, context)['selection_review']
        self.assertNotIn('next_move', review)

    def test_contradicted_and_premise_disables_only_that_bridge(self):
        graph, context = fixture()
        context['dependency_map']['nodes'][0]['status'] = 'CONTRADICTED'
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['candidates'][0]['goal_contribution']['status'], 'UNKNOWN')
        self.assertEqual(review['next_move']['kind'], 'REVIEW_GOAL_LINK')
        self.assertEqual(review['dependency_review']['goals']['completion_standard']['minimal_missing_evidence_sets'],
                         [['node:alternative_proof', 'rule:independent_route']])
        action = graph['nodes'][0]['executable']['action']
        action['target'] = 'alternative_proof'
        action['goal_contribution']['path'][0] = 'alternative_proof'
        contribution = search_directions(graph, context)['selection_review']['candidates'][0]['goal_contribution']
        self.assertEqual(contribution['graph_path']['status'], 'DECLARED_CONNECTED_PATH')

    def test_circular_goal_premise_is_not_a_grounded_route(self):
        graph, context = fixture()
        context['dependency_map']['hyperedges'] = [{'id': 'circular',
            'premises': ['unrestricted_lower', 'completion_standard'], 'conclusion': 'completion_standard',
            'status': 'SUPPORTED', 'source': 'synthetic circular assumption'}]
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['dependency_review']['goals']['completion_standard']['status'], 'UNRESOLVED')
        self.assertEqual(review['candidates'][0]['goal_contribution']['status'], 'UNKNOWN')
        self.assertEqual(review['next_move']['kind'], 'REVIEW_GOAL_LINK')

    def test_unlinked_candidate_cannot_hide_truncated_blocker_analysis(self):
        graph, context = fixture()
        context['dependency_map']['limits'] = {'max_combinations': 1}
        unrelated = deepcopy(graph['nodes'][0])
        unrelated['id'] = 'unlinked'
        unrelated['executable']['action'].pop('goal_contribution')
        graph['nodes'].append(unrelated)
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')

    def test_checking_a_proposed_bridge_keeps_its_rule_obligation(self):
        graph, context = fixture()
        action = graph['nodes'][0]['executable']['action']
        action['target'] = 'rule:independent_route'
        action['goal_contribution']['path'] = ['rule:independent_route', 'completion_standard']
        contribution = search_directions(graph, context)['selection_review']['candidates'][0]['goal_contribution']
        self.assertEqual(contribution['graph_path']['status'], 'DECLARED_CONNECTED_PATH')
        self.assertEqual(contribution['graph_path']['link_rule_alternatives'], [['independent_route']])
        self.assertIn(['node:alternative_proof', 'rule:independent_route'],
                      contribution['graph_path']['goal_review']['minimal_missing_evidence_sets'])

    def test_ready_proposed_reduction_can_run_without_promoting_its_claim(self):
        graph, context = fixture()
        context['require_goal_link'] = True
        next(n for n in context['dependency_map']['nodes'] if n['id'] == 'alternative_proof')['status'] = 'SUPPORTED'
        action = graph['nodes'][0]['executable']['action']
        action['target'] = 'rule:independent_route'
        action['goal_contribution']['path'] = ['rule:independent_route', 'completion_standard']
        before = deepcopy((graph, context))
        search = search_directions(graph, context)
        record = choice({'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}, context)
        self.assertEqual(record['goal_guard']['ready_obligation'], 'rule:independent_route')
        self.assertEqual(record['scientific_support'], 'UNKNOWN')
        self.assertNotEqual(record['goal_guard']['contribution']['graph_path']['goal_review']['status'], 'DECLARED_SUPPORTED')
        self.assertEqual((graph, context), before)

    def test_goal_annotation_does_not_reopen_an_unchanged_structured_route(self):
        from rds_advisor import _loop_route
        graph, _ = fixture()
        original = {'action': deepcopy(graph['nodes'][0]['executable']['action'])}
        annotated = deepcopy(original)
        annotated['action']['goal_contribution']['source'] = 'renamed-note.json'
        self.assertEqual(_loop_route(original), _loop_route(annotated))
        annotated['action']['target'] = 'different-obligation'
        self.assertNotEqual(_loop_route(original), _loop_route(annotated))

    def test_explicit_execution_guard_rechecks_structure_and_ready_obligation(self):
        graph, context = fixture()
        context['require_goal_link'] = True
        search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        record = choice(advice, context)
        self.assertTrue(record['require_goal_link'])
        # A stale/forged READY label cannot waive an actual input-map conflict.
        context['dependency_map']['nodes'][1]['status'] = 'SUPPORTED'
        with self.assertRaisesRegex(ValueError, 'Advisor context changed after complete graph analysis'):
            choice(advice, context)
        advice['recommendations'][0]['search'] = search_directions(graph, context)
        with self.assertRaisesRegex(ValueError, 'not a current ready obligation'):
            choice(advice, context)
        context['dependency_map']['nodes'][0]['status'] = 'CONTRADICTED'
        advice['recommendations'][0]['search'] = search_directions(graph, context)
        with self.assertRaisesRegex(ValueError, 'complete actual dependency map'):
            choice(advice, context)
        del context['dependency_map']
        advice['recommendations'][0]['search'] = search_directions(graph, context)
        with self.assertRaisesRegex(ValueError, 'complete actual dependency map'):
            choice(advice, context)

    def test_guard_remains_optional_and_preserves_old_choices(self):
        graph, context = fixture()
        del context['dependency_map']
        search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        self.assertEqual(choice(advice, context)['scientific_support'], 'UNKNOWN')
        context['require_goal_link'] = 'yes'
        advice['recommendations'][0]['search'] = search_directions(graph, context)
        with self.assertRaisesRegex(ValueError, 'must be boolean'):
            choice(advice, context)

    def test_a_grounded_or_alternative_does_not_hide_a_cyclic_selected_and_branch(self):
        graph, context = fixture()
        dependency = context['dependency_map']
        dependency['nodes'].extend({'id': ident, 'status': 'UNKNOWN', 'source': 'synthetic cycle'}
                                  for ident in ('cycle_a', 'cycle_b'))
        dependency['hyperedges'][0]['premises'] = ['unrestricted_lower', 'cycle_a']
        dependency['hyperedges'].extend([
            {'id': 'cycle_ab', 'premises': ['cycle_a'], 'conclusion': 'cycle_b',
             'status': 'SUPPORTED', 'source': 'synthetic cycle'},
            {'id': 'cycle_ba', 'premises': ['cycle_b'], 'conclusion': 'cycle_a',
             'status': 'SUPPORTED', 'source': 'synthetic cycle'}])
        context['require_goal_link'] = True
        search = search_directions(graph, context)
        self.assertEqual(search['selection_review']['dependency_review']['goals']['completion_standard']['status'], 'UNKNOWN')
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        with self.assertRaisesRegex(ValueError, 'complete actual dependency map'):
            choice(advice, context)
        action = graph['nodes'][0]['executable']['action']
        action['target'] = 'alternative_proof'
        action['goal_contribution']['path'][0] = 'alternative_proof'
        search = search_directions(graph, context)
        advice['recommendations'][0]['search'] = search
        self.assertEqual(choice(advice, context)['scientific_support'], 'UNKNOWN')

    def test_guard_preserves_actual_map_when_advice_is_from_an_earlier_input(self):
        graph, context = fixture()
        context['require_goal_link'] = True
        old_search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': old_search}]}
        context['dependency_map']['nodes'][0]['status'] = 'SUPPORTED'
        before = deepcopy((advice, context))
        with self.assertRaisesRegex(ValueError, 'Advisor context changed after complete graph analysis'):
            choice(advice, context)
        self.assertEqual((advice, context), before)
        current_search = search_directions(graph, context)
        current = current_search['selection_review']['dependency_review']
        advice['recommendations'][0]['search'] = current_search
        record = choice(advice, context)
        self.assertEqual(record['goal_guard']['input_sha256'], current['input_sha256'])
        self.assertEqual(record['goal_guard']['dependency_map'], context['dependency_map'])
        self.assertEqual(record['goal_guard']['ready_obligation'], 'node:unrestricted_lower')
        self.assertEqual(record['goal_guard']['contribution']['graph_path']['goal_review'], current['goals']['completion_standard'])
        self.assertEqual(record['selection_review'], current_search['selection_review'])

    def test_advice_cannot_override_or_supply_an_unchecked_current_binding(self):
        graph, context = fixture()
        context['require_goal_link'] = True
        advice = {'objective_binding': {'sha256': 'older-objective'},
                  'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
                                       'search': search_directions(graph, context)}]}
        for binding in ({'sha256': 'current-objective'}, None):
            with self.subTest(binding=binding):
                if binding is None:
                    context.pop('objective_binding', None)
                else:
                    context['objective_binding'] = binding
                advice['recommendations'][0]['search'] = search_directions(graph, context)
                before = deepcopy((advice, context))
                with self.assertRaisesRegex(ValueError, 'objective binding differs'):
                    choice(advice, context)
                self.assertEqual((advice, context), before)
        context['objective_binding'] = deepcopy(advice['objective_binding'])
        advice['recommendations'][0]['search'] = search_directions(graph, context)
        self.assertEqual(choice(advice, context)['goal_guard']['ready_obligation'], 'node:unrestricted_lower')

    def test_real_cli_rechecks_native_binding_before_guarding_raw_context(self):
        from test_rds_quick import QuickTests
        from test_rds_native_research import GOAL
        from rds_math import bind_objective, check_context
        helper = QuickTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        bind_objective(helper.root, json.dumps({**GOAL, 'scope': {'domain': 'synthetic'}}).encode('utf-8'))
        helper.script('print("native ledger")\n')
        helper.job('ledger', True, '--timeout', '30')
        ledger = helper.root / '.rds/exec/ledger'
        graph, context = fixture()
        del context['objective_binding']
        context['require_goal_link'] = True
        context_path, graph_path = helper.root / 'context.json', helper.root / 'graph.json'
        context_path.write_text(json.dumps(context), encoding='utf-8')
        graph_path.write_text(json.dumps(graph), encoding='utf-8')
        expected_binding = check_context(ledger, context)
        result = helper.job('native-goal', True, '--context', str(context_path),
                            '--graph', str(graph_path), '--ledger', str(ledger))
        summary = json.loads(result.stdout)
        self.assertEqual(summary['run_status'], 'SUCCEEDED')
        from rds_quick import latest_decision
        record, _ = latest_decision(ledger)
        self.assertEqual(record['selection_review']['objective_binding'], expected_binding)
        self.assertEqual(record['goal_guard']['ready_obligation'], 'node:unrestricted_lower')
        self.assertEqual(json.loads(context_path.read_text()), context)
        from rds_cli import parser
        from rds_project import ProjectStore
        from rds_quick import execute
        args = parser().parse_args(['--root', str(helper.root), 'exec', '--name', 'stale-native',
            '--timeout', '5', '--ledger', str(ledger), '--context', str(context_path),
            '--', sys.executable, '-B', 'probe.py'])
        stale_advice = {'objective_binding': {**expected_binding, 'sha256': '0' * 64},
                        'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
                                             'search': search_directions(graph, {**deepcopy(context),
                                                                                'objective_binding': expected_binding})}]}
        before = ProjectStore(ledger).snapshot()
        with self.assertRaisesRegex(ValueError, 'objective binding differs'):
            execute(args, (stale_advice, context))
        self.assertEqual(ProjectStore(ledger).snapshot(), before)
        self.assertFalse((helper.root / '.rds/exec/stale-native').exists())

    def test_an_or_route_cannot_supply_the_selected_paths_own_conclusion_as_a_premise(self):
        graph, context = fixture()
        dependency = context['dependency_map']
        dependency['nodes'].append({'id': 'needs_goal', 'status': 'UNKNOWN', 'source': 'synthetic feedback'})
        dependency['hyperedges'][0]['premises'] = ['unrestricted_lower', 'needs_goal']
        dependency['hyperedges'].append({'id': 'feedback', 'premises': ['completion_standard'],
            'conclusion': 'needs_goal', 'status': 'SUPPORTED', 'source': 'synthetic feedback'})
        context['require_goal_link'] = True
        search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        with self.assertRaisesRegex(ValueError, 'complete actual dependency map'):
            choice(advice, context)
        action = graph['nodes'][0]['executable']['action']
        action['target'] = 'alternative_proof'
        action['goal_contribution']['path'][0] = 'alternative_proof'
        advice['recommendations'][0]['search'] = search_directions(graph, context)
        self.assertEqual(choice(advice, context)['scientific_support'], 'UNKNOWN')

    def test_unknown_ancestors_do_not_reopen_a_declared_closed_goal(self):
        graph, context = fixture()
        context['require_goal_link'] = True
        next(n for n in context['dependency_map']['nodes'] if n['id'] == 'completion_standard')['status'] = 'SUPPORTED'
        search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        with self.assertRaisesRegex(ValueError, 'declared goal is already closed'):
            choice(advice, context)

    def test_rule_prefix_in_a_valid_node_id_does_not_make_it_a_rule(self):
        graph, context = fixture()
        context['require_goal_link'] = True
        context['dependency_map']['nodes'][1]['id'] = 'rule:lemma'
        context['dependency_map']['hyperedges'][0]['premises'][1] = 'rule:lemma'
        action = graph['nodes'][0]['executable']['action']
        action['target'] = 'rule:lemma'
        action['goal_contribution']['path'][0] = 'rule:lemma'
        search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        self.assertEqual(choice(advice, context)['goal_guard']['ready_obligation'], 'node:rule:lemma')

    def test_real_node_wins_a_name_collision_with_a_proposed_rule(self):
        graph, context = fixture()
        context['require_goal_link'] = True
        node_id = 'rule:independent_route'
        context['dependency_map']['nodes'].append({'id': node_id, 'status': 'UNKNOWN',
                                                   'source': 'synthetic valid leaf'})
        context['dependency_map']['hyperedges'].append({'id': 'node_route', 'premises': [node_id],
            'conclusion': 'completion_standard', 'status': 'SUPPORTED', 'source': 'synthetic node route'})
        action = graph['nodes'][0]['executable']['action']
        action['target'] = node_id
        action['goal_contribution']['path'] = [node_id, 'completion_standard']
        before = deepcopy((graph, context))
        search = search_directions(graph, context)
        contribution = search['selection_review']['candidates'][0]['goal_contribution']
        self.assertEqual(contribution['graph_path']['start_token'], 'node:' + node_id)
        self.assertEqual(contribution['graph_path']['link_rule_alternatives'], [['node_route']])
        record = choice({'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}, context)
        self.assertEqual(record['goal_guard']['ready_obligation'], 'node:' + node_id)
        self.assertEqual((graph, context), before)

    def test_closed_intermediate_does_not_admit_a_redundant_ancestor_check(self):
        graph, context = fixture()
        dependency = context['dependency_map']
        dependency['nodes'].append({'id': 'closed_bridge', 'status': 'SUPPORTED', 'source': 'synthetic checked bridge'})
        dependency['hyperedges'][0]['premises'][1] = 'closed_bridge'
        dependency['hyperedges'].append({'id': 'redundant', 'premises': ['unrestricted_lower'],
            'conclusion': 'closed_bridge', 'status': 'SUPPORTED', 'source': 'synthetic old bridge'})
        graph['nodes'][0]['executable']['action']['goal_contribution']['path'].insert(1, 'closed_bridge')
        context['require_goal_link'] = True
        search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        with self.assertRaisesRegex(ValueError, 'already closed intermediate'):
            choice(advice, context)
        dependency['nodes'][-1]['status'] = 'UNKNOWN'
        advice['recommendations'][0]['search'] = search_directions(graph, context)
        self.assertEqual(choice(advice, context)['goal_guard']['ready_obligation'], 'node:unrestricted_lower')

    def test_empirical_and_mixed_maps_do_not_promote_causal_or_task_evidence(self):
        for mode in ('empirical', 'mixed'):
            with self.subTest(mode=mode):
                graph, context = fixture()
                context['research_mode'] = mode
                action = graph['nodes'][0]['executable']['action']
                action.update(kind='PAIRED_TEST', competing_explanations=['mechanism-a', 'mechanism-b'])
                review = search_directions(graph, context)['selection_review']
                self.assertEqual(review['candidates'][0]['goal_contribution']['graph_path']['status'],
                                 'DECLARED_CONNECTED_PATH')
                self.assertEqual(review['assurance'], 'INPUT_REPORTED_NOT_SCIENTIFIC_VERIFICATION')

    def test_changed_premise_recomputes_blockers_and_preserves_choice_evidence(self):
        graph, context = fixture()
        before = search_directions(graph, context)['selection_review']['dependency_review']
        context['dependency_map']['nodes'][0]['status'] = 'SUPPORTED'
        search = search_directions(graph, context)
        after = search['selection_review']['dependency_review']
        self.assertNotEqual(after['input_sha256'], before['input_sha256'])
        self.assertIn(['node:matching_value', 'node:unrestricted_lower'],
                      after['goals']['completion_standard']['minimal_missing_evidence_sets'])
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        record = choice(advice, context)
        self.assertEqual(record['selection_review']['dependency_review'], after)
        with tempfile.TemporaryDirectory() as root:
            self.assertNotIn('dependency_review', json.dumps(brief(root, advice, 'test')))
        self.assertEqual(record['scientific_support'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
