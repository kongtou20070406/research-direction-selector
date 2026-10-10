"""Research choice regressions from proxy success and a single supplied route."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor import RDSAdvisor, _json
from rds_advisor_search import review_selection, search_directions
from rds_quick import brief, choice


def fixture():
    action = {'id': 'probe', 'kind': 'PAIRED_TEST', 'description': 'Check a scoped training premise',
              'competing_explanations': ['bias', 'state-support'], 'required_observables': ['response'],
              'outcomes': [{'observation': 'positive', 'next_decision': 'review bounded successor'},
                           {'observation': 'negative', 'next_decision': 'revise premise'}]}
    graph = {'nodes': [{'id': 'route', 'executable': {'decisions': ['next'], 'preconditions': [], 'action': action}}], 'edges': []}
    context = {'decision': {'id': 'next', 'goal_revision': 'g1', 'scope': {'domain': 'synthetic'}}, 'facts': {}}
    return graph, context


class SelectionReviewTests(unittest.TestCase):
    def test_one_supplied_route_is_not_a_scientific_comparison(self):
        graph, context = fixture()
        original = deepcopy((graph, context))
        result = search_directions(graph, context)
        review = result['selection_review']
        self.assertEqual(result['candidates'][0]['status'], 'READY')
        self.assertEqual(review['basis'], 'REVIEW_ONLY')
        self.assertEqual(review['candidates'][0]['basis'], 'PROCEDURE_ONLY')
        self.assertEqual({f['kind'] for f in review['flags']}, {'SINGLE_CONFIGURED_DIRECTION', 'RIVAL_PREDICTIONS_MISSING'})
        self.assertEqual(review['next_move']['kind'], 'REVIEW_ALTERNATIVE')
        self.assertEqual(review['next_move']['authorization'], 'UNCHANGED')
        self.assertIn('Respect an explicitly chosen route', review['next_move']['prompt'])
        self.assertEqual(review['next_move']['preserve_refs'],
                         ['search.decision', 'context.budget', 'context.resources', 'context.method_constraints'])
        self.assertEqual((graph, context), original)

    def test_valid_proxy_does_not_close_a_failed_task_goal_or_change_permission(self):
        graph, context = fixture()
        context['decision']['goal_conditions'] = [{'fact': 'long_chain_gain', 'op': 'gte', 'value': .05}]
        graph['nodes'][0]['executable']['action']['goal_contribution'] = {
            'target': 'long_chain_gain', 'path': ['Measure the declared task gain'], 'source': 'task-protocol.json'}
        graph['nodes'][0]['executable']['action']['target'] = 'Measure the declared task gain'
        context['facts'] = {k: {'value': value, 'source': 'synthetic-terminal.json'} for k, value in (
            ('pipeline_valid', True), ('manipulation_passed', True), ('long_chain_gain', -.19))}
        with tempfile.TemporaryDirectory() as root:
            advisor = RDSAdvisor(root_dir=Path(root))
            advice = {'recommendations': advisor.recommend_next_directions({'advisor_context': context}, graph)}
            record = choice(advice, context)
        self.assertEqual(record['selection_review']['goal']['status'], 'FALSE')
        self.assertEqual(record['candidate']['status'], 'READY')
        self.assertEqual(record['scientific_support'], 'UNKNOWN')
        self.assertEqual(record['goal_conditions'], context['decision']['goal_conditions'])
        self.assertIn('GOAL_BRIDGE_OPEN', [f['kind'] for f in record['selection_review']['flags']])
        move = record['selection_review']['next_move']
        self.assertEqual(move['kind'], 'DIAGNOSE_GOAL_GAP')
        self.assertEqual(move['basis'], 'INPUT_REVIEW_HEURISTIC_NOT_SCIENTIFIC_PROOF')
        self.assertIn('capacity lower bound', move['reason'])
        self.assertIn('smallest repair', move['prompt'])
        self.assertEqual(record['scope'], context['decision']['scope'])
        self.assertEqual(record['goal_revision'], context['decision']['goal_revision'])

    def test_missing_goal_source_remains_unknown(self):
        graph, context = fixture()
        context['decision']['goal_conditions'] = [{'fact': 'quality', 'value': True}]
        context['facts']['quality'] = {'value': True}
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['goal']['status'], 'UNKNOWN')
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')

    def test_reported_goal_success_is_not_independent_evidence(self):
        graph, context = fixture()
        context['decision']['goal_conditions'] = [{'fact': 'quality', 'value': True}]
        context['facts']['quality'] = {'value': True, 'source': 'reported.json'}
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['goal']['status'], 'TRUE')
        self.assertEqual(review['goal']['assurance'], 'INPUT_REPORTED')
        self.assertNotIn('GOAL_BRIDGE_OPEN', [f['kind'] for f in review['flags']])
        self.assertEqual(review['basis'], 'REVIEW_ONLY')
        self.assertNotIn('next_move', review)

    def test_goal_contribution_is_a_declaration_across_modes_and_goal_types(self):
        for mode in ('theory', 'empirical', 'mixed'):
            for native in (False, True):
                with self.subTest(mode=mode, native=native):
                    graph, context = fixture()
                    context['research_mode'] = mode
                    action = graph['nodes'][0]['executable']['action']
                    if mode == 'theory':
                        action.update(kind='OBLIGATION_CHECK', target='local-lemma', claim='A local implication',
                            outcomes=[{'observation': label, 'next_decision': label}
                                      for label in ('verified', 'counterexample', 'unresolved')])
                        action.pop('competing_explanations')
                    target = 'completion_standard' if native else 'quality'
                    if native:
                        context['objective_binding'] = {'question_id': 'original', 'goal_revision': 'g1', 'sha256': 'a' * 64}
                    else:
                        context['decision']['goal_conditions'] = [{'fact': target, 'value': True}]
                        context['facts'][target] = {'value': False, 'source': 'task-result.json'}
                    before = deepcopy((graph, context))
                    search = search_directions(graph, context)
                    review = search['selection_review']
                    self.assertEqual(review['next_move']['kind'], 'REVIEW_GOAL_LINK')
                    self.assertEqual(review['candidates'][0]['goal_contribution']['status'], 'UNDECLARED')
                    self.assertEqual(search['candidates'][0]['status'], 'READY')
                    self.assertEqual((graph, context), before)

                    declaration = {'target': target, 'path': ['Produce a local bound', 'Use it in the original acceptance check'],
                                   'source': 'proposed-dependency.json'}
                    action['goal_contribution'] = declaration
                    action['target'] = 'Produce a local bound'
                    before = deepcopy((graph, context))
                    search = search_directions(graph, context)
                    report = search['selection_review']['candidates'][0]['goal_contribution']
                    self.assertEqual(report['status'], 'DECLARED_PATH')
                    self.assertEqual(report['assurance'], 'DECLARED_LINK_NOT_SCIENTIFIC_PROOF')
                    self.assertEqual(report['authorization'], 'UNCHANGED')
                    self.assertEqual({k: report[k] for k in declaration}, declaration)
                    self.assertEqual(search['candidates'][0]['status'], 'READY')
                    if not native:
                        self.assertEqual(search['selection_review']['goal']['status'], 'FALSE')
                    self.assertEqual((graph, context), before)

    def test_invalid_or_unbound_contribution_never_promotes_a_candidate(self):
        valid = {'target': 'completion_standard', 'path': ['Close the stated obligation'], 'source': 'proposal.json'}
        variants = [None, {}, {**valid, 'target': 'different-goal'}, {**valid, 'source': ''},
                    {**valid, 'source': {'path': 'proposal.json'}}, {**valid, 'path': []},
                    {**valid, 'path': [' ']}, {**valid, 'path': ['x'] * 9}, {**valid, 'path': ['x' * 513]},
                    {**valid, 'path': 'unsupported prose'}, {**valid, 'proved': True}]
        for declaration in variants:
            with self.subTest(declaration=declaration):
                graph, context = fixture()
                context['objective_binding'] = {'question_id': 'original', 'goal_revision': 'g1', 'sha256': 'a' * 64}
                graph['nodes'][0]['executable']['action']['goal_contribution'] = declaration
                before = deepcopy((graph, context))
                search = search_directions(graph, context)
                review = search['selection_review']
                self.assertEqual(review['candidates'][0]['goal_contribution']['status'], 'UNKNOWN')
                self.assertIn('GOAL_CONTRIBUTION_INVALID', [f['kind'] for f in review['flags']])
                self.assertEqual(review['next_move']['kind'], 'REVIEW_GOAL_LINK')
                self.assertEqual(search['candidates'][0]['status'], 'READY')
                self.assertEqual((graph, context), before)
        graph, context = fixture()
        graph['nodes'][0]['executable']['action']['goal_contribution'] = valid
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['candidates'][0]['goal_contribution']['status'], 'UNKNOWN')
        self.assertNotIn('objective_binding', review)

    def test_healthy_goal_link_keeps_unrelated_defects_out_of_compact_advice(self):
        for defect in (None, {'target': 'wrong', 'path': ['unused'], 'source': 'proposal.json'}):
            with self.subTest(defect=defect):
                graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
                context['objective_binding'] = {'question_id': 'original', 'goal_revision': 'g1', 'sha256': 'a' * 64}
                graph['nodes'][0]['executable']['action']['goal_contribution'] = {
                    'target': 'completion_standard', 'path': ['Decide the necessary applicability bridge'], 'source': 'proposal.json'}
                graph['nodes'][0]['executable']['action']['target'] = 'Decide the necessary applicability bridge'
                if defect is not None:
                    graph['nodes'][1]['executable']['action']['goal_contribution'] = defect
                search = search_directions(graph, context)
                self.assertNotIn('next_move', search['selection_review'])
                self.assertTrue(any(f['kind'].startswith('GOAL_CONTRIBUTION_') for f in search['selection_review']['flags']))
                with tempfile.TemporaryDirectory() as root:
                    summary = brief(root, {'recommendations': [{'search': search}]}, 'test')
                self.assertFalse(any(f.startswith('GOAL_CONTRIBUTION_') for f in summary['flags']))

    def test_goal_link_choice_and_brief_keep_full_declaration_without_authorizing(self):
        graph, context = fixture()
        context['objective_binding'] = {'question_id': 'original', 'goal_revision': 'g1', 'sha256': 'a' * 64}
        with tempfile.TemporaryDirectory() as root:
            search = search_directions(graph, context)
            advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
            record = choice(advice, context)
            self.assertEqual(record['selection_review']['objective_binding'], context['objective_binding'])
            self.assertEqual(record['candidate']['status'], 'READY')
            self.assertEqual(record['scientific_support'], 'UNKNOWN')
            summary = brief(root, advice, 'test')
            self.assertEqual(summary['next_move'], 'REVIEW_GOAL_LINK')
            self.assertEqual(summary['flags'][0], 'GOAL_CONTRIBUTION_UNDECLARED')
            self.assertLessEqual(len(summary['flags']), 3)
            self.assertNotIn('prompt', summary)
            self.assertEqual(json.loads(Path(summary['record']).read_text(encoding='utf-8')), advice)
            declaration = {'target': 'completion_standard', 'path': ['A useful auxiliary lemma', 'Original proof obligation'],
                           'source': 'proposal.json'}
            graph['nodes'][0]['executable']['action']['goal_contribution'] = declaration
            graph['nodes'][0]['executable']['action']['target'] = 'A useful auxiliary lemma'
            search = search_directions(graph, context)
            advice['recommendations'][0]['search'] = search
            record = choice(advice, context)
            self.assertEqual(record['candidate']['action']['goal_contribution'], declaration)
            self.assertEqual(record['selection_review']['candidates'][0]['goal_contribution']['path'], declaration['path'])
            self.assertEqual(record['scientific_support'], 'UNKNOWN')

    def paired(self, predictions):
        graph, context = fixture()
        action = graph['nodes'][0]['executable']['action']
        action['discrimination'] = {'scope_id': 'local-v1', 'source': 'declared-predictions.json', 'predictions': predictions}
        other = deepcopy(graph['nodes'][0]); other['id'] = 'alternative'; other['executable']['action']['id'] = 'alternative'
        graph['nodes'].append(other)
        context['costs'] = {k: {'value': v, 'resource': 'cpu', 'unit': 'seconds',
                                'comparison_group': 'same-attempt', 'source': 'declared-cost.json'} for k, v in (('probe', 1), ('alternative', 2))}
        return graph, context

    def test_supported_same_scope_predictions_and_costs_allow_conditional_comparison(self):
        graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
        result = search_directions(graph, context)
        self.assertEqual(result['selection_review']['basis'], 'CONDITIONAL_COMPARISON')
        self.assertEqual(result['ranking']['pareto_front'], ['route:probe'])
        self.assertEqual(result['selection_review']['assurance'], 'INPUT_REPORTED_NOT_SCIENTIFIC_VERIFICATION')
        self.assertNotIn('next_move', result['selection_review'])

    def test_unknown_prerequisite_ranking_does_not_compare_ready_directions(self):
        for include_ready in (False, True):
            with self.subTest(include_ready=include_ready):
                graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
                for node in graph['nodes']:
                    node['executable']['preconditions'] = [{'fact': 'gate', 'value': True}]
                    query_cost = deepcopy(context['costs']['probe'])
                    query_cost['value'] = 0
                    context['costs'][f"query:{node['id']}:gate"] = query_cost
                if include_ready:
                    independent = deepcopy(graph['nodes'][0])
                    independent['id'] = 'independent'
                    independent['executable']['preconditions'] = []
                    independent['executable']['action']['id'] = 'independent'
                    graph['nodes'].append(independent)
                    context['costs']['independent'] = deepcopy(context['costs']['probe'])
                result = search_directions(graph, context)
                self.assertEqual([c['status'] for c in result['candidates'][:2]], ['NEEDS_EVIDENCE'] * 2)
                self.assertEqual(result['ranking']['dominance'][0]['better'], 'route:probe')
                review = result['selection_review']
                self.assertEqual(review['ready_graph_directions'], int(include_ready))
                self.assertEqual(review['basis'], 'REVIEW_ONLY' if include_ready else 'NO_READY_DIRECTION')
                self.assertEqual(review['authorization'], 'UNCHANGED')
                if include_ready:
                    self.assertNotIn('next_move', review)
                else:
                    self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')

    def test_ready_comparison_does_not_wait_for_unrelated_pending_or_blocked_routes(self):
        graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
        for identity, fact in (('pending', 'missing'), ('blocked', 'unavailable')):
            extra = deepcopy(graph['nodes'][0])
            extra['id'] = identity
            extra['executable']['action']['id'] = identity
            extra['executable']['preconditions'] = [{'fact': fact, 'value': True}]
            graph['nodes'].append(extra)
        context['facts']['unavailable'] = {'value': False, 'source': 'unrelated-prerequisite.json'}
        search = search_directions(graph, context)
        self.assertEqual([c['status'] for c in search['candidates']], ['READY', 'READY', 'NEEDS_EVIDENCE'])
        self.assertEqual(search['blocked_candidates'][0]['status'], 'BLOCKED_PREREQUISITE')
        self.assertEqual(search['selection_review']['basis'], 'CONDITIONAL_COMPARISON')
        self.assertNotIn('next_move', search['selection_review'])

    def test_ready_comparison_keeps_unsupported_alternative_warning_without_steering(self):
        graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
        extra = deepcopy(graph['nodes'][0])
        extra['id'] = extra['executable']['action']['id'] = 'unsupported'
        extra['executable']['action']['discrimination'].pop('source')
        graph['nodes'].append(extra)
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['ready_graph_directions'], 3)
        self.assertEqual(review['basis'], 'CONDITIONAL_COMPARISON')
        self.assertIn(('PREDICTION_PREMISES_UNRESOLVED', 'unsupported:unsupported'),
                      [(flag['kind'], flag.get('candidate')) for flag in review['flags']])
        self.assertNotIn('next_move', review)

    def test_available_discriminator_does_not_request_another_for_ready_alternative(self):
        for defect, expected in (('overlap', 'RIVAL_PREDICTIONS_OVERLAP'),
                                 ('missing', 'RIVAL_PREDICTIONS_MISSING')):
            with self.subTest(defect=defect):
                graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
                alternative = graph['nodes'][1]['executable']['action']
                if defect == 'overlap':
                    alternative['discrimination']['predictions']['state-support'] = ['positive']
                else:
                    alternative.pop('discrimination')
                review = search_directions(graph, context)['selection_review']
                self.assertEqual(review['ready_graph_directions'], 2)
                self.assertEqual(review['basis'], 'REVIEW_ONLY')
                self.assertIn(expected, [flag['kind'] for flag in review['flags']])
                self.assertNotIn('next_move', review)

    def test_shared_pass_fail_predictions_do_not_distinguish_causes(self):
        graph, context = self.paired({'bias': ['positive', 'negative'], 'state-support': ['positive', 'negative']})
        result = search_directions(graph, context)
        self.assertEqual(result['ranking']['dominance'], [])
        self.assertEqual(result['selection_review']['basis'], 'REVIEW_ONLY')
        self.assertTrue(all(r['basis'] == 'NONDISCRIMINATING' for r in result['selection_review']['candidates']))
        self.assertEqual(result['selection_review']['next_move']['kind'], 'DESIGN_DISCRIMINATOR')
        self.assertIn('Retain the existing rival hypotheses', result['selection_review']['next_move']['prompt'])
        self.assertNotIn('changing an assumption', result['selection_review']['next_move']['prompt'])

    def test_partial_rival_overlap_keeps_existing_distinction_and_requests_missing_one(self):
        graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative'],
                                     'mixed': ['positive', 'negative']})
        for node in graph['nodes']:
            node['executable']['action']['competing_explanations'].append('mixed')
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['next_move']['kind'], 'DESIGN_DISCRIMINATOR')
        self.assertTrue(all(c['basis'] == 'CONDITIONAL_RIVAL_TEST' and c['distinguishing_pairs'] == 1
                            and c['unresolved_pairs'] == 2 for c in review['candidates']))

    def test_local_predictions_do_not_transfer_when_application_premise_fails(self):
        graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
        context['facts']['same_operator'] = {'value': False, 'source': 'actual-operator.json'}
        for node in graph['nodes']:
            node['executable']['action']['discrimination']['conditions'] = [{'fact': 'same_operator', 'value': True}]
        result = search_directions(graph, context)
        self.assertEqual(result['ranking']['dominance'], [])
        self.assertTrue(all(r['basis'] == 'PREDICTION_PREMISES_UNRESOLVED' for r in result['selection_review']['candidates']))
        self.assertEqual(result['selection_review']['next_move']['kind'], 'RESOLVE_PREMISE')

    def test_single_theory_obligation_needs_no_artificial_experiment(self):
        graph, context = fixture()
        action = graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', target='L1', claim='x*x >= 0 for rational x',
                      outcomes=[{'observation': label, 'next_decision': label} for label in ('verified', 'counterexample', 'unresolved')])
        action.pop('competing_explanations')
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['basis'], 'SCOPED_OBLIGATION')
        self.assertEqual(review['flags'], [])
        self.assertNotIn('next_move', review)

    def test_ready_theory_obligation_does_not_hide_a_sourced_failed_goal(self):
        graph, context = fixture()
        action = graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', target='L1', claim='x*x >= 0 for rational x',
                      outcomes=[{'observation': label, 'next_decision': label} for label in ('verified', 'counterexample', 'unresolved')])
        action.pop('competing_explanations')
        context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
        context['facts']['goal'] = {'value': False, 'source': 'reported-goal.json'}
        action['goal_contribution'] = {'target': 'goal', 'path': ['L1'], 'source': 'proof-plan.json'}
        original = deepcopy((graph, context))
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['basis'], 'SCOPED_OBLIGATION')
        self.assertEqual(review['goal']['status'], 'FALSE')
        self.assertEqual([flag['kind'] for flag in review['flags']], ['GOAL_BRIDGE_OPEN'])
        self.assertEqual(review['next_move']['kind'], 'DIAGNOSE_GOAL_GAP')
        self.assertEqual(review['next_move']['authorization'], 'UNCHANGED')
        self.assertEqual((graph, context), original)

    def test_planning_partition_scopes_local_check_against_open_global_predicate(self):
        graph, context = fixture()
        action = graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', target='L1', claim='x*x >= 0 for rational x',
                      outcomes=[{'observation': label, 'next_decision': label} for label in ('verified', 'counterexample', 'unresolved')])
        action.pop('competing_explanations')
        context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True},
                                                  {'fact': 'aux', 'value': True}]
        context['facts'] = {'goal': {'value': False, 'source': 'reported-goal.json'},
                            'aux': {'value': True, 'source': 'reported-aux.json'}}
        action['goal_contribution'] = {'target': 'goal', 'path': ['L1'], 'source': 'proof-plan.json'}
        original = deepcopy((graph, context))
        review = search_directions(graph, context)['selection_review']
        planning = review['planning']
        self.assertEqual(planning['scope'], 'LOCAL')
        self.assertEqual(planning['local_checks'], [{'candidate': 'route:probe', 'target': 'L1'}])
        self.assertEqual(planning['open_predicates'], ['goal'])
        self.assertEqual(planning['ready_obligations'], [])
        self.assertEqual(planning['omitted_ready_obligations'], 0)
        self.assertEqual(planning['authorization'], 'UNCHANGED')
        # A declared local path never closes the global AND goal.
        self.assertEqual(review['goal']['status'], 'FALSE')
        self.assertNotIn('planning', graph['nodes'][0]['executable']['action'])
        self.assertEqual((graph, context), original)
        # GLOBAL: open predicates without a declared contribution.
        action.pop('goal_contribution')
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['planning']['scope'], 'GLOBAL')
        self.assertEqual(review['planning']['local_checks'], [])
        self.assertEqual(review['planning']['open_predicates'], ['goal'])
        # UNSCOPED: no open predicates and no declared contribution.
        context['facts']['goal'] = {'value': True, 'source': 'reported-goal.json'}
        context['facts']['aux'] = {'value': True, 'source': 'reported-aux.json'}
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['planning']['scope'], 'UNSCOPED')
        self.assertEqual(review['planning']['local_checks'], [])
        self.assertEqual(review['planning']['open_predicates'], [])

    def test_planning_partition_reports_open_affirmative_obligations(self):
        def observed(value, locator):
            from rds_artifacts import ArtifactFact
            return ArtifactFact({'kind': 'OBSERVED', 'value': value,
                                 'source': {'path': 'metrics.json', 'sha256': 'a' * 64, 'locator': locator}},
                                reading_identity=('metrics.json', locator))

        graph, context = fixture()
        context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
        context['decision']['affirmations'] = {'portable': [{'fact': 'replay_error', 'op': 'lte', 'value': 0.1}]}
        context['facts'] = {'goal': observed(True, '/goal'), 'replay_error': observed(0.2, '/replay_error')}
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['goal']['status'], 'TRUE')
        # An undeclared affirmative is an open obligation, not vacuous.
        self.assertEqual(review['planning']['scope'], 'GLOBAL')
        self.assertEqual(review['planning']['open_predicates'],
                         ['affirmation:portable', 'affirmation:applicable'])
        context['facts']['replay_error'] = observed(0.05, '/replay_error')
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['planning']['open_predicates'], ['affirmation:applicable'])
        context['decision']['affirmations'] = {
            'portable': [{'fact': 'replay_error', 'op': 'lte', 'value': 0.1}],
            'applicable': [{'fact': 'cohort_error', 'op': 'lte', 'value': 0.1}]}
        context['facts']['cohort_error'] = observed(0.05, '/cohort_error')
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['planning']['open_predicates'], [])

    def test_brief_planning_projection_stays_bounded(self):
        graph, context = fixture()
        long_fact = 'long_fact_' * 50  # 500 characters: valid input, above the legacy 128 projection cap
        action = graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', target='unrestricted_lower', claim='a bound',
                      outcomes=[{'observation': label, 'next_decision': label} for label in ('verified', 'counterexample', 'unresolved')])
        action.pop('competing_explanations')
        context['decision']['goal_conditions'] = [{'fact': long_fact, 'value': True}]
        context['facts'] = {long_fact: {'value': False, 'source': 'reported-goal.json'}}
        action['goal_contribution'] = {'target': long_fact, 'path': ['unrestricted_lower'],
                                       'source': 'proof-plan.json'}
        search = search_directions(graph, context)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}
        with tempfile.TemporaryDirectory() as root:
            summary = brief(root, advice, 'test')
        projected = summary['planning']
        # The digest keeps a bounded projection; the CAS record keeps the full partition.
        self.assertEqual(projected['scope'], 'LOCAL')
        self.assertEqual(len(projected['open_predicates'][0]), 128)
        self.assertTrue(all(len(check['target']) <= 128 for check in projected['local_checks']))
        self.assertEqual(projected['open_predicates_omitted'], 0)

    def test_planning_partition_lists_capped_ready_obligation_tokens(self):
        graph, context = fixture()
        dependency = json.loads((ROOT / 'examples/goal-linked-hypergraph.json').read_text())
        context['dependency_map'] = dependency
        context['objective_binding'] = {'sha256': 'synthetic-unit-fixture-not-authorization'}
        action = graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', target='unrestricted_lower', claim='a bound',
                      outcomes=[{'observation': label, 'next_decision': label} for label in ('verified', 'counterexample', 'unresolved')])
        action.pop('competing_explanations')
        action['goal_contribution'] = {'target': 'completion_standard',
                                       'path': ['unrestricted_lower', 'completion_standard'],
                                       'source': 'synthetic contract'}
        context['decision']['goal_conditions'] = [{'fact': 'completion_standard', 'value': True}]
        original = deepcopy((graph, context))
        review = search_directions(graph, context)['selection_review']
        planning = review['planning']
        self.assertEqual(planning['scope'], 'LOCAL')
        self.assertEqual(planning['local_checks'], [{'candidate': 'route:probe', 'target': 'unrestricted_lower'}])
        self.assertEqual(planning['open_predicates'], ['completion_standard'])
        self.assertEqual(planning['ready_obligations'],
                         [row['token'] for row in review['dependency_review']['ready_obligations'][:8]])
        self.assertGreaterEqual(len(planning['ready_obligations']), 4)
        self.assertEqual(planning['omitted_ready_obligations'],
                         max(0, len(review['dependency_review']['ready_obligations']) - 8))
        # Valid IDs above the legacy 512/64 limits stay in the partition, never silently dropped.
        renamed = 'x' * 200
        dependency['nodes'][1]['id'] = renamed  # unrestricted_lower
        for edge in dependency['hyperedges']:
            edge['premises'] = [renamed if p == 'unrestricted_lower' else p for p in edge['premises']]
        action['target'] = renamed
        action['goal_contribution']['path'][0] = renamed
        review = search_directions(graph, context)['selection_review']
        self.assertEqual(review['planning']['local_checks'][0]['target'], renamed)
        self.assertIn('node:' + renamed, review['planning']['ready_obligations'])
        dependency['nodes'][1]['id'] = 'unrestricted_lower'
        for edge in dependency['hyperedges']:
            edge['premises'] = ['unrestricted_lower' if p == renamed else p for p in edge['premises']]
        action['target'] = 'unrestricted_lower'
        action['goal_contribution']['path'][0] = 'unrestricted_lower'
        self.assertEqual((graph, context), original)

    def test_truncated_search_cannot_claim_global_best(self):
        graph, context = self.paired({'bias': ['positive'], 'state-support': ['negative']})
        review = search_directions(graph, context, max_candidates=1)['selection_review']
        self.assertIn('SEARCH_TRUNCATED', [f['kind'] for f in review['flags']])
        self.assertNotEqual(review['basis'], 'CONDITIONAL_COMPARISON')
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')

    def test_actual_rejection_and_oscillation_have_distinct_scoped_reviews(self):
        from test_rds_advisor import LedgerLoopTests
        helper = LedgerLoopTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        candidate = helper.search()['search']['candidates'][0]
        saved = helper.record('rejected-once', candidate)
        renamed = deepcopy(helper.graph)
        renamed['nodes'][0]['executable']['action'].update(id='renamed', description='A supposedly new mechanism')
        before = helper.store.snapshot()
        repeated = helper.search(graph=renamed)['search']
        self.assertEqual(repeated['candidates'], [])
        self.assertEqual(repeated['blocked_candidates'][0]['loop_review']['checkpoint_sha256'], saved['sha256'])
        self.assertEqual(repeated['selection_review']['next_move']['kind'], 'REFORMULATE')
        self.assertIn('not semantic equivalence', repeated['selection_review']['next_move']['prompt'])
        self.assertEqual(repeated['selection_review']['next_move']['authorization'], 'UNCHANGED')
        self.assertEqual(helper.store.snapshot(), before)

        for value, expected in ((None, 'RESOLVE_PREMISE'), (True, None)):
            context = deepcopy(helper.context)
            context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
            context['facts']['goal'] = {'value': value, 'source': 'reported-goal.json'}
            result = helper.search(context=context)['search']
            self.assertEqual(result['loop_review']['flags'][0]['kind'], 'REPEAT_REJECTED_ROUTE')
            self.assertEqual(result['selection_review'].get('next_move', {}).get('kind'), expected)

        changed = deepcopy(helper.graph)
        changed['nodes'][0]['executable']['action']['intervention']['value'] = False
        self.assertEqual(len(helper.search(graph=changed)['search']['candidates']), 1)
        self.assertNotEqual(helper.search(graph=changed)['search']['selection_review']['next_move']['kind'], 'REFORMULATE')

        helper.record('accepted-later', candidate, 'accepted')
        other = deepcopy(candidate)
        other['action']['intervention']['value'] = False
        helper.record('other', other, 'accepted')
        helper.record('returned', candidate, 'deferred')
        result = helper.search()['search']
        self.assertEqual(result['loop_review']['flags'][0]['kind'], 'DECISION_OSCILLATION')
        self.assertEqual(result['loop_review']['flags'][0]['candidate_ids'], [candidate['id']])
        self.assertEqual(result['selection_review']['next_move']['kind'], 'REVIEW_DECISION_HISTORY')
        self.assertEqual(len(result['candidates']), 1)

    def test_filtered_rejected_route_does_not_reformulate_a_distinct_ready_route(self):
        from test_rds_advisor import LedgerLoopTests
        helper = LedgerLoopTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.record('rejected-once', helper.search()['search']['candidates'][0])
        graph = deepcopy(helper.graph)
        fresh = deepcopy(graph['nodes'][0])
        fresh['id'] = fresh['executable']['action']['id'] = 'new-route'
        fresh['executable']['action']['intervention']['value'] = False
        graph['nodes'].append(fresh)
        before = helper.store.snapshot()
        result = helper.search(graph=graph)['search']
        self.assertEqual([c['id'] for c in result['candidates']], ['new-route:new-route'])
        self.assertEqual(result['candidates'][0]['status'], 'READY')
        self.assertEqual(result['blocked_candidates'][0]['status'], 'BLOCKED_REJECTED_ROUTE')
        self.assertEqual(result['loop_review']['flags'][0]['kind'], 'REPEAT_REJECTED_ROUTE')
        self.assertEqual(result['selection_review']['next_move']['kind'], 'REVIEW_ALTERNATIVE')
        self.assertEqual(result['selection_review']['authorization'], 'UNCHANGED')
        self.assertEqual(helper.store.snapshot(), before)

        context = deepcopy(helper.context)
        context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
        context['facts']['goal'] = {'value': False, 'source': 'reported-goal.json'}
        graph['nodes'][1]['executable']['action']['goal_contribution'] = {
            'target': 'goal', 'path': ['Check the original claim'], 'source': 'proposal.json'}
        graph['nodes'][1]['executable']['action']['target'] = 'Check the original claim'
        review = helper.search(context=context, graph=graph)['search']['selection_review']
        self.assertEqual(review['next_move']['kind'], 'DIAGNOSE_GOAL_GAP')
        self.assertIn('goal predicate failed', review['next_move']['reason'])

    def test_recorded_oscillation_does_not_reformulate_a_distinct_ready_route(self):
        from test_rds_advisor import LedgerLoopTests
        from rds_advisor import _loop_route
        helper = LedgerLoopTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        first = helper.search()['search']['candidates'][0]
        other = deepcopy(first)
        other['action']['intervention']['value'] = False
        helper.record('first', first, 'accepted')
        helper.record('other', other, 'accepted')
        helper.record('returned', first, 'deferred')
        graph = deepcopy(helper.graph)
        graph['nodes'][0]['executable']['action']['operation'] = 'independent-new-check'
        before = helper.store.snapshot()
        result = helper.search(graph=graph)['search']
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['status'], 'READY')
        flag = result['loop_review']['flags'][0]
        self.assertEqual(flag['kind'], 'DECISION_OSCILLATION')
        self.assertEqual(flag['candidate_ids'], [])
        self.assertNotIn(_loop_route(result['candidates'][0]), flag['route_sha256'])
        self.assertEqual(result['selection_review']['next_move']['kind'], 'REVIEW_ALTERNATIVE')
        self.assertEqual(result['selection_review']['authorization'], 'UNCHANGED')
        self.assertEqual(helper.store.snapshot(), before)

    def test_declared_domain_signal_and_unknown_goal_remain_scoped_review(self):
        graph, context = fixture()
        result = search_directions(graph, context)
        result['loop_review'] = {'authorization': 'UNCHANGED', 'status': 'REVIEW_REQUIRED', 'flags': [
            {'kind': 'REPEAT_DECLARED_REJECTED_DOMAIN', 'candidate_id': 'route:probe', 'route_sha256': '1' * 64,
             'checkpoint_id': 'rejected-domain', 'checkpoint_sha256': '2' * 64, 'witness_sha256': '3' * 64}]}
        before = deepcopy(result)
        self.assertEqual(review_selection(result, context)['next_move']['kind'], 'REFORMULATE')
        self.assertEqual(result, before)
        context['decision']['goal_conditions'] = [{'fact': 'quality', 'value': True}, {'fact': 'scope', 'value': True}]
        context['facts']['quality'] = {'value': False, 'source': 'reported-terminal.json'}
        review = review_selection(result, context)
        self.assertEqual(review['goal']['status'], 'FALSE')
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')
        result['loop_review']['flags'] = [{'kind': 'LOOP_HISTORY_REVIEW_ERROR', 'reason': 'Checkpoint integrity failure'}]
        review = review_selection(result, context)
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')
        self.assertIn('integrity', review['next_move']['reason'])

    def test_goal_rejection_history_prompts_a_changed_premise_only_for_a_new_ready_variant(self):
        graph, context = fixture()
        context['decision']['goal_conditions'] = [{'fact': 'quality', 'value': True}, {'fact': 'scope', 'value': True}]
        context['facts']['quality'] = {'value': False, 'source': 'reported-terminal.json'}
        graph['nodes'][0]['executable']['action'].update(target='Measure quality', goal_contribution={
            'target': 'quality', 'path': ['Measure quality'], 'source': 'protocol.json'})
        result = search_directions(graph, context)
        history = {'kind': 'GOAL_ROUTES_REJECTED', 'goal_revision': 'g1', 'rejected_routes': 2, 'candidate_ids': ['route:probe'],
                   'question_ids': ['earlier', 'renamed'], 'checkpoint_ids': ['a', 'b']}
        result['loop_review'] = {'authorization': 'UNCHANGED', 'status': 'REVIEW_REQUIRED', 'flags': [history]}
        before = deepcopy(result)
        move = review_selection(result, context)['next_move']
        self.assertEqual(result, before)
        self.assertEqual(move['kind'], 'REFORMULATE')
        self.assertIn('changes only parameters of 2 route(s)', move['reason'])
        self.assertIn('not a capacity bound or a guilty premise', move['reason'])
        self.assertEqual(move['authorization'], 'UNCHANGED')

        # Without a matching ready variant the existing unresolved-goal review is unchanged.
        other = deepcopy(result)
        other['loop_review']['flags'][0]['candidate_ids'] = ['route:another']
        self.assertEqual(review_selection(other, context)['next_move']['kind'], 'RESOLVE_PREMISE')
        empty = deepcopy(result)
        empty['candidates'] = []
        self.assertEqual(review_selection(empty, context)['next_move']['kind'], 'RESOLVE_PREMISE')

        # Overlapping rival predictions still ask for a discriminator first.
        overlap = deepcopy(graph)
        overlap['nodes'][0]['executable']['action']['discrimination'] = {
            'scope_id': 'local-v1', 'source': 'declared-predictions.json',
            'predictions': {'bias': ['positive'], 'state-support': ['positive']}}
        overlap_result = search_directions(overlap, context)
        overlap_result['loop_review'] = deepcopy(result['loop_review'])
        overlap_review = review_selection(overlap_result, context)
        self.assertIn('RIVAL_PREDICTIONS_OVERLAP', {f['kind'] for f in overlap_review['flags']})
        self.assertEqual(overlap_review['next_move']['kind'], 'DESIGN_DISCRIMINATOR')

        # A reported closed goal needs no jump prompt.
        context['facts']['quality'] = {'value': True, 'source': 'reported-terminal.json'}
        context['facts']['scope'] = {'value': True, 'source': 'reported-terminal.json'}
        self.assertNotIn('next_move', review_selection(result, context))

    def test_route_family_ignores_only_parameters_of_structured_actions(self):
        from rds_advisor import _loop_family, _loop_route
        base = {'id': 'c', 'action': {'id': 'a', 'kind': 'PAIRED_TEST', 'description': 'Train', 'operation': 'train',
                                      'target': 'gain', 'parameters': {'gain': 16}}}
        variant = deepcopy(base)
        variant['action'].update(parameters={'gain': 16, 'tau': 0.5}, description='A renamed recipe')
        changed = deepcopy(base)
        changed['action']['operation'] = 'distill'
        self.assertNotEqual(_loop_route(base), _loop_route(variant))
        self.assertEqual(_loop_family(base), _loop_family(variant))
        self.assertNotEqual(_loop_family(base), _loop_family(changed))
        described = {'id': 'd', 'action': {'id': 'a', 'kind': 'PAIRED_TEST', 'description': 'Ablate attention heads'}}
        self.assertIsNone(_loop_family(described))
        self.assertIsNone(_loop_family({'id': 'e', 'action': {**base['action'], 'parameters': ['not', 'a', 'map']}}))

    def test_goal_predicates_are_nonempty_bounded_and_named(self):
        graph, context = fixture()
        for value in ([], [{}], [{'fact': ''}], [{'fact': 'x'}] * 33, 'free text'):
            with self.subTest(value=value):
                context['decision']['goal_conditions'] = value
                with self.assertRaisesRegex(ValueError, 'decision.goal_conditions'):
                    search_directions(graph, context)

    def test_digest_keeps_selection_warning_and_full_record(self):
        graph, context = fixture()
        search = search_directions(graph, context)
        advice = {'recommendations': [{'search': search}]}
        with tempfile.TemporaryDirectory() as root:
            summary = brief(root, advice, '5.8.0')
            full = json.loads(Path(summary['record']).read_text(encoding='utf-8'))
        self.assertEqual(summary['selection_basis'], 'REVIEW_ONLY')
        self.assertIn('RIVAL_PREDICTIONS_MISSING', summary['flags'])
        self.assertEqual(summary['next_move'], 'REVIEW_ALTERNATIVE')
        self.assertNotIn('prompt', summary)
        self.assertNotIn('reason', summary)
        self.assertLess(len(json.dumps(summary)), 1024)
        self.assertIn('smallest repair', full['recommendations'][0]['search']['selection_review']['next_move']['prompt'])
        self.assertEqual(full, advice)

    def test_cli_brief_exposes_only_move_kind_and_preserves_full_prompt_in_cas(self):
        graph, context = fixture()
        context['budget'] = {'value': 5, 'resource': 'cpu', 'unit': 'seconds',
                             'comparison_group': 'same-attempt', 'source': 'input-budget.json'}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, value in (('context.json', context), ('graph.json', graph)):
                (root / name).write_text(json.dumps(value), encoding='utf-8')
            completed = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root),
                'advise', '--context', str(root / 'context.json'), '--graph', str(root / 'graph.json'), '--brief'],
                capture_output=True, encoding='utf-8', timeout=15,
                env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            full = json.loads(Path(summary['record']).read_text(encoding='utf-8'))
            search = next(r['search'] for r in full['recommendations'] if r['type'] == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(summary['next_move'], 'REVIEW_ALTERNATIVE')
            self.assertLess(len(completed.stdout.encode('utf-8')), 1024)
            self.assertNotIn('counterfactual', completed.stdout)
            move = search['selection_review']['next_move']
            self.assertIn('counterfactual', move['prompt'])
            self.assertEqual(move['authorization'], 'UNCHANGED')
            self.assertEqual(search['decision'], context['decision'])
            self.assertEqual(json.loads((root / 'context.json').read_text()), context)
            self.assertFalse((root / '.rds/project.sqlite3').exists())

    def test_oversized_context_has_exact_size_and_actionable_hint_without_mutation(self):
        facts = {'observation': {'value': 1, 'code_sha256': {'source': 'a' * 300}}}
        original = deepcopy(facts)
        with self.assertRaisesRegex(ValueError, r'byte limit \(\d+ > 128\).*manifest digest/source locator'):
            _json(facts, 128)
        self.assertEqual(facts, original)


if __name__ == '__main__':
    unittest.main()
