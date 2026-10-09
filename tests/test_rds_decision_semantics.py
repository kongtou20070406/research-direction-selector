"""Issue #254: a scoped acceptance gap does not establish a failed method.

All metrics and checkpoints are synthetic development evidence.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor_search import review_selection, search_directions
from rds_quick import brief
from test_rds_selection_review import fixture
import test_rds_triple_affirmative as affirmative_cases
from test_rds_triple_affirmative import context, predicate


class DecisionSemanticsTests(unittest.TestCase):
    def goal_case(self, value):
        graph, ctx = fixture()
        ctx['decision']['goal_conditions'] = [predicate('gain', 'gte', .05)]
        ctx['facts']['gain'] = {'value': value, 'source': 'synthetic-metrics.json'}
        graph['nodes'][0]['executable']['action'].update(target='gain', goal_contribution={
            'target': 'gain', 'path': ['gain'], 'source': 'synthetic-protocol.json'})
        return graph, ctx

    def test_real_cli_below_threshold_reports_gap_without_method_change(self):
        cli = affirmative_cases.TripleAffirmativeCLITests()
        for value in (.01, .03, .049):
            with self.subTest(value=value):
                review = cli.advise(context(predicate('gain', 'gte', .05)), 'gain', {'gain': value})
                self.assertEqual(review['goal']['status'], 'FALSE')
                self.assertEqual(review['ready_graph_directions'], 1)
                move = review['next_move']
                self.assertEqual(move['kind'], 'DIAGNOSE_GOAL_GAP')
                self.assertIn('smallest repair', move['prompt'])
                self.assertNotIn('candidate changing an assumption', move['prompt'])
                self.assertEqual(move['authorization'], 'UNCHANGED')
                self.assertEqual(review['goal']['conditions'][0]['actual'], value)
        for value in (.05, .051):
            with self.subTest(value=value):
                review = cli.advise(context(predicate('gain', 'gte', .05)), 'gain', {'gain': value})
                self.assertEqual(review['goal']['status'], 'TRUE')
                self.assertNotIn('next_move', review)

    def test_brief_preserves_reason_basis_and_source_for_actual_gap(self):
        cli = affirmative_cases.TripleAffirmativeCLITests()
        summary = cli.advise(context(predicate('gain', 'gte', .05)), 'gain', {'gain': .049}, None, '--brief')
        self.assertEqual(summary['next_move'], 'DIAGNOSE_GOAL_GAP')
        detail = summary['next_move_detail']
        self.assertIn('gap', detail['reason'])
        self.assertEqual(detail['basis'], 'INPUT_REVIEW_HEURISTIC_NOT_SCIENTIFIC_PROOF')
        self.assertEqual(detail['source'], 'selection_review.goal.conditions')

    def test_failed_affirmative_is_a_gap_without_a_method_diagnosis(self):
        cli = affirmative_cases.TripleAffirmativeCLITests()
        ctx = context(predicate('gain', 'gte', .05), {
            'portable': [predicate('replay_gain', 'gte', .05)],
            'applicable': [predicate('cohort_gain', 'gte', .05)]})
        values = {'gain': .05, 'replay_gain': .051, 'cohort_gain': .049}
        review = cli.advise(ctx, 'gain', values)
        self.assertEqual(review['goal']['status'], 'TRUE')
        self.assertEqual(review['goal']['triple_affirmative']['status'], 'FALSE')
        self.assertEqual(review['next_move']['kind'], 'DIAGNOSE_GOAL_GAP')
        self.assertEqual(review['next_move']['affirmatives'], ['APPLICABLE'])
        self.assertEqual(review['next_move']['source'], 'selection_review.goal.triple_affirmative')

    def loop_case(self, goal_value):
        from test_rds_advisor import LedgerLoopTests
        helper = LedgerLoopTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        ctx = deepcopy(helper.context)
        ctx['decision']['goal_conditions'] = [predicate('quality', 'eq', True)]
        ctx['facts']['quality'] = {'value': goal_value, 'source': 'synthetic-metrics.json'}
        helper.graph['nodes'][0]['executable']['action'].update(target='quality', goal_contribution={
            'target': 'quality', 'path': ['quality'], 'source': 'synthetic-protocol.json'})
        first = helper.search(context=ctx)['search']['candidates'][0]
        other = deepcopy(first)
        other['action']['intervention']['value'] = False
        helper.record('a-accepted', first, 'accepted', ctx)
        helper.record('b-accepted', other, 'accepted', ctx)
        helper.record('a-returned', first, 'deferred', ctx)
        return helper, ctx

    def test_current_true_goal_keeps_oscillation_warning_without_reformulation(self):
        helper, ctx = self.loop_case(True)
        before = helper.store.snapshot()
        search = helper.search(context=ctx)['search']
        self.assertIn('DECISION_OSCILLATION', [f['kind'] for f in search['loop_review']['flags']])
        self.assertEqual(search['selection_review']['goal']['status'], 'TRUE')
        self.assertNotIn('next_move', search['selection_review'])
        self.assertEqual(helper.store.snapshot(), before)

    def test_open_goal_oscillation_reviews_history_without_inventing_failed_method(self):
        helper, ctx = self.loop_case(False)
        search = helper.search(context=ctx)['search']
        self.assertEqual(search['selection_review']['next_move']['kind'], 'REVIEW_DECISION_HISTORY')
        self.assertIn('not a rejected route', search['selection_review']['next_move']['reason'])
        self.assertEqual(search['selection_review']['next_move']['authorization'], 'UNCHANGED')

    def test_true_goal_not_overturned_by_route_or_scope_warnings(self):
        graph, ctx = self.goal_case(.05)
        for kind in ('REPEAT_REJECTED_ROUTE', 'REPEAT_DECLARED_REJECTED_DOMAIN', 'DECISION_OSCILLATION'):
            with self.subTest(kind=kind):
                search = search_directions(graph, ctx)
                search['loop_review'] = {'flags': [{'kind': kind, 'candidate_id': 'route:probe',
                                                   'candidate_ids': ['route:probe']}]}
                search['truncation'] = {'truncated': True}
                review = review_selection(search, ctx)
                self.assertEqual(review['goal']['status'], 'TRUE')
                self.assertNotIn('next_move', review)
                self.assertIn('SEARCH_TRUNCATED', [f['kind'] for f in review['flags']])

    def test_unknown_and_integrity_checks_remain_earlier_than_gap(self):
        graph, ctx = self.goal_case(.049)
        ctx['decision']['goal_conditions'].append(predicate('other', 'eq', True))
        search = search_directions(graph, ctx)
        self.assertEqual(search['selection_review']['next_move']['kind'], 'RESOLVE_PREMISE')
        ctx['facts']['other'] = {'value': True, 'source': 'synthetic-metrics.json'}
        search = search_directions(graph, ctx)
        search['loop_review'] = {'flags': [{'kind': 'LOOP_HISTORY_REVIEW_ERROR'}]}
        review = review_selection(search, ctx)
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')
        self.assertIn('integrity', review['next_move']['reason'])

    def test_true_goal_with_history_warning_still_requires_open_affirmatives(self):
        graph, ctx = self.goal_case(.05)
        ctx['decision']['affirmations'] = {'portable': [predicate('replay', 'gte', .05)]}
        search = search_directions(graph, ctx)
        search['loop_review'] = {'flags': [{'kind': 'DECISION_OSCILLATION',
                                          'candidate_ids': ['route:probe']}]}
        review = review_selection(search, ctx)
        self.assertEqual(review['goal']['status'], 'TRUE')
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')
        self.assertIn('PORTABLE', review['next_move']['affirmatives'])
        self.assertEqual(review['flags'][0]['kind'], 'TRIPLE_AFFIRMATIVE_OPEN')

    def test_owned_brief_uses_same_detail_and_bounds_long_text(self):
        with tempfile.TemporaryDirectory() as raw:
            summary = brief(Path(raw), {'assurance': 'PROGRAM_OWNED_EVIDENCE_NOT_SCIENTIFIC_PROOF',
                                       'next_move': {'kind': 'DIAGNOSE_GOAL_GAP', 'reason': 'x' * 2000,
                                                     'basis': 'INPUT_REVIEW_HEURISTIC_NOT_SCIENTIFIC_PROOF',
                                                     'source': 'selection_review.goal.conditions'}}, 'test')
        self.assertEqual(summary['next_move'], 'DIAGNOSE_GOAL_GAP')
        self.assertEqual(len(summary['next_move_detail']['reason']), 512)
        self.assertTrue(summary['next_move_detail']['reason_truncated'])


if __name__ == '__main__':
    unittest.main()
