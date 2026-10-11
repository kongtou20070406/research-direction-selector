"""Invalid declared primary actions cannot hide behind another READY route."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_advisor_search import search_directions
from rds_quick import choice
from test_rds_advisor_search import node


def context():
    return {'decision': {'id': 'choose', 'goal_revision': 'primary-coverage-v1',
                         'scope': {'domain': 'synthetic-software-acceptance'}}, 'facts': {}}


def advice(search):
    return {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}


class PrimaryActionCoverageTests(unittest.TestCase):
    def assert_incomplete(self, graph, bad_ids):
        ctx = context()
        original = deepcopy((graph, ctx))
        result = search_directions(graph, ctx)
        coverage = result['graph_coverage']
        reports = {row['id']: row for row in coverage['analyzed_nodes']}
        self.assertEqual(coverage['node_count'], len(graph['nodes']))
        self.assertEqual(set(reports), {row['id'] for row in graph['nodes']})
        for rid in bad_ids:
            self.assertFalse(reports[rid]['action_validation']['valid'])
            self.assertTrue(any(rid in reason for reason in coverage['reasons']))
        self.assertFalse(coverage['full'])
        self.assertEqual(result['analysis_coverage']['status'], 'INCOMPLETE')
        healthy = next(candidate for candidate in result['candidates'] if candidate['rule_id'] == 'healthy')
        self.assertEqual(healthy['local_status'], 'READY')
        self.assertEqual(healthy['status'], 'NEEDS_COMPLETE_ANALYSIS')
        for selected in (None, healthy['id']):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, 'Complete graph analysis required'):
                choice(advice(result), ctx, selected)
        self.assertEqual((graph, ctx), original)

    def test_multiple_bad_primaries_block_healthy_automatic_and_explicit_choice(self):
        broken, null = node('broken'), node('null')
        broken['executable']['action']['outcomes'] = {}
        null['executable']['action'] = None
        self.assert_incomplete({'nodes': [broken, node('healthy'), null], 'edges': []}, ['broken', 'null'])

    def test_missing_primary_for_current_decision_blocks_healthy_choice(self):
        missing = node('missing')
        del missing['executable']['action']
        self.assert_incomplete({'nodes': [node('healthy'), missing], 'edges': []}, ['missing'])

    def test_declared_bad_action_for_other_decision_is_still_reported(self):
        other = node('other')
        other['executable']['decisions'] = ['other-decision']
        other['executable']['action']['outcomes'] = []
        self.assert_incomplete({'nodes': [node('healthy'), other], 'edges': []}, ['other'])

    def test_other_decision_interpretation_uses_structure_not_current_choice(self):
        ctx = context()
        ctx['decision']['current_choice'] = 'select A'
        other = node('other')
        other['executable']['decisions'] = ['other-decision']
        other['executable']['action'].update(kind='INTERPRETATION_UPDATE', outcomes=[
            {'observation': 'A', 'next_decision': 'select A'}])
        graph = {'nodes': [node('healthy'), other], 'edges': []}
        original = deepcopy((graph, ctx))
        result = search_directions(graph, ctx)
        reports = {row['id']: row for row in result['graph_coverage']['analyzed_nodes']}
        self.assertTrue(reports['other']['action_validation']['valid'])
        self.assertEqual(reports['other']['satisfaction'], 'UNKNOWN')
        self.assertTrue(result['analysis_coverage']['full'])
        self.assertEqual([row['rule_id'] for row in result['candidates']], ['healthy'])
        self.assertEqual(choice(advice(result), ctx)['candidate']['id'], 'healthy:healthy-test')
        self.assertEqual((graph, ctx), original)

    def test_current_interpretation_keeps_strict_choice_relation(self):
        changed = node('changed')
        changed['executable']['action'].update(kind='INTERPRETATION_UPDATE', outcomes=[
            {'observation': 'A', 'next_decision': 'select A'}])
        self.assert_incomplete({'nodes': [node('healthy'), changed], 'edges': []}, ['changed'])

    def test_unconfigured_and_actionless_prerequisite_keep_complete_unknown_analysis(self):
        prerequisite = node('prerequisite')
        prerequisite['executable']['decisions'] = []
        del prerequisite['executable']['action']
        graph = {'nodes': [node('healthy'), prerequisite, {'id': 'descriptive'}], 'edges': []}
        ctx = context()
        result = search_directions(graph, ctx)
        reports = {row['id']: row for row in result['graph_coverage']['analyzed_nodes']}
        self.assertTrue(result['analysis_coverage']['full'])
        self.assertEqual(reports['descriptive']['action_readiness'], 'UNKNOWN')
        self.assertEqual(reports['prerequisite']['satisfaction'], 'UNKNOWN')
        self.assertEqual(choice(advice(result), ctx)['candidate']['id'], 'healthy:healthy-test')


if __name__ == '__main__':
    unittest.main()
