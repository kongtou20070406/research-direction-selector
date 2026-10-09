"""Neural preference behavior and real CLI admission; synthetic development only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_graph_ranker import FEATURES, rank, validate
from rds_project import ProjectStore
import test_rds_owned_advisor as fixture


def model(mode='tie_break'):
    return {'schema': 1, 'mode': mode, 'model': {
        'features': list(FEATURES), 'origin': 'hand-authored synthetic software fixture; not trained',
        'input_weights': [[0, 1, -1, 0, 0]], 'input_bias': [0],
        'layers': [{'self': [[1]], 'message': [[1]], 'bias': [0]} for _ in range(2)],
        'readout': [1], 'readout_bias': 0}}


class GraphRankerTests(unittest.TestCase):
    def graph(self):
        return {'nodes': [{'id': 'a', 'executable': {'action': {'id': 'a'}}},
                          {'id': 'b', 'executable': {'action': {'id': 'b'}}},
                          {'id': 'signal', 'executable': {'satisfied_when': [
                              {'fact': 'signal', 'value': True}]}}],
                'edges': [{'from': 'signal', 'to': 'b', 'relation': 'prerequisite_for'}]}

    def score(self, value=True, *, config=None, graph=None, precedence=None):
        facts = {} if value is None else {'signal': {'value': value, 'source': {'locator': 'synthetic'}}}
        return rank(config or model(), graph or self.graph(), facts,
                    [{'candidate': 'a'}, {'candidate': 'b'}], precedence=precedence)

    def test_topology_and_false_vs_unknown_change_neural_preference(self):
        ordered, report = self.score()
        self.assertEqual([r['candidate'] for r in ordered], ['b', 'a'])
        self.assertTrue(report['selection_applied'])
        negative, neg = self.score(False)
        missing, unknown = self.score(None)
        self.assertEqual([r['candidate'] for r in negative], ['a', 'b'])
        self.assertEqual([r['candidate'] for r in missing], ['a', 'b'])
        self.assertNotEqual(neg['scores'], unknown['scores'])
        no_edge = self.graph(); no_edge['edges'] = []
        self.assertNotEqual(self.score(graph=no_edge)[1]['input_sha256'], report['input_sha256'])
        self.assertEqual(report['training_status'], 'UNVERIFIED')
        self.assertFalse(report['research_policy_gain_measured'])

    def test_shadow_equal_and_precedence_preserve_order(self):
        for config, value, precedence in ((model('shadow'), True, None), (model(), None, None),
                                           (model(), True, 'ACTIVE_RESERVATION'),
                                           (model(), True, 'FEASIBILITY_PILOT_ORDER')):
            ordered, report = self.score(value, config=config, precedence=precedence)
            self.assertEqual([r['candidate'] for r in ordered], ['a', 'b'])
            self.assertFalse(report['selection_applied'])

    def test_invalid_dimensions_fields_and_values_reject(self):
        cases = []
        for value in (True, float('nan'), float('inf'), 10**400):
            config = model(); config['model']['readout_bias'] = value; cases.append(config)
        config = model(); config['model']['input_weights'] = [[1]]; cases.append(config)
        config = model(); config['model']['layers'] *= 2; cases.append(config)
        config = model(); config['model']['features'].reverse(); cases.append(config)
        config = model(); config['model']['code'] = 'print(1)'; cases.append(config)
        for config in cases:
            with self.subTest(config=str(config)[:120]), self.assertRaises((ValueError, OverflowError)):
                validate(config)

    def test_missing_endpoint_candidate_and_expansion_limit_abstain(self):
        graphs = []
        graph = self.graph(); graph['edges'][0]['from'] = 'missing'; graphs.append(graph)
        graph = self.graph(); graph['nodes'][0]['executable'] = {}; graphs.append(graph)
        graph = self.graph()
        graph['nodes'][2]['executable']['satisfied_when'] = [
            {'fact': 'f' + str(i), 'value': True} for i in range(513)]
        graphs.append(graph)
        for graph in graphs:
            ordered, report = self.score(graph=graph)
            self.assertEqual(report['status'], 'ABSTAINED')
            self.assertEqual([r['candidate'] for r in ordered], ['a', 'b'])
            self.assertFalse(report['selection_applied'])

    def test_trace_is_deterministic_deduplicated_and_input_is_unchanged(self):
        graph = self.graph(); before = deepcopy(graph)
        first = self.score(graph=graph)[1]
        self.assertEqual(before, graph)
        graph['nodes'].reverse(); graph['edges'] *= 2
        second = self.score(graph=graph)[1]
        self.assertEqual(first['input_sha256'], second['input_sha256'])
        self.assertEqual(first['scores'], second['scores'])
        self.assertEqual(before, self.graph())

    def test_trace_binds_large_values_without_copying_them(self):
        facts = {'signal': {'value': 'x' * 100000, 'source': {'locator': 'synthetic original'}}}
        _, report = rank(model(), self.graph(), facts, [{'candidate': 'a'}, {'candidate': 'b'}])
        self.assertEqual(report['status'], 'SCORED')
        predicate = next(n for n in report['trace']['nodes'] if n['kind'] == 'predicate')
        self.assertIn('actual_sha256', predicate['evidence'])
        self.assertNotIn('actual', predicate['evidence'])
        self.assertNotIn('expected', predicate['evidence'])
        self.assertLess(len(json.dumps(report['trace'])), 4000)


class GraphRankerCLITests(unittest.TestCase):
    # Reuse original public workload/CLI harness without inheriting its full test suite.
    setUp = fixture.OwnedAdvisorCLITests.setUp
    call = fixture.OwnedAdvisorCLITests.call
    output = fixture.OwnedAdvisorCLITests.output
    write_json = fixture.OwnedAdvisorCLITests.write_json
    starts = fixture.OwnedAdvisorCLITests.starts
    snapshot = fixture.OwnedAdvisorCLITests.snapshot
    create = fixture.OwnedAdvisorCLITests.create

    def initialize(self, config=None, *, blocked=False, budget=False, dominated=False, **kwargs):
        def mutate(policy):
            if config is not None:
                policy['graph_ranker'] = config
            policy['graph']['nodes'][1]['executable']['preconditions'] = (
                [{'fact': 'baseline.score', 'op': 'lt', 'value': 0}] if blocked else [])
            policy['graph']['nodes'].append({'id': 'signal', 'executable': {'preconditions': [], 'satisfied_when': [
                {'fact': 'run.baseline.completed', 'value': False}]}})
            policy['graph']['edges'].append({'from': 'signal', 'to': 'repair', 'relation': 'prerequisite_for'})
            if budget:
                policy['routes'][1]['manifest']['resource_estimates']['cpu_seconds'] = 11
            if dominated:
                policy['routes'][1]['manifest']['resource_estimates']['wall_seconds'] = 4
                for node in policy['graph']['nodes'][:2]:
                    node['executable']['action']['discrimination'] = {
                        'scope_id': 'same-synthetic-comparison', 'source': {'locator': 'synthetic predictions'},
                        'predictions': {'positive response': ['positive'], 'negative response': ['negative']}}
        return fixture.OwnedAdvisorCLITests.initialize(self, mutate_policy=mutate, **kwargs)

    def test_real_next_shadow_and_neural_advance_preserve_receipts(self):
        self.initialize(model())
        before = self.snapshot()
        report = self.output('project', 'next')
        self.assertEqual(report['graph_ranker']['status'], 'SCORED')
        self.assertEqual(report['selected_run'], 'repair', json.dumps(report['graph_ranker']))
        self.assertEqual(report['graph_ranker']['scope'], ['baseline', 'repair'])
        after = self.snapshot()
        self.assertEqual(before['budget'], after['budget'])
        self.assertEqual(after['runs'], [])
        self.assertEqual(self.starts(), [])
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['repair'])
        snap = self.snapshot()
        self.assertEqual(len(snap['runs']), 1)
        self.assertEqual(len(snap['receipts']), 1)
        self.assertEqual(self.output('project', 'next')['selected_run'], 'baseline')
        with ProjectStore(self.root)._db(True) as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM events WHERE json_extract(body,'$.kind')="
                                          "'OWNED_ADVISOR_REVIEW'").fetchone()[0], 0)

    def test_absent_and_shadow_keep_original_selection(self):
        # Separate roots keep contract/model immutability intact.
        self.initialize(model('shadow'))
        report = self.output('advise')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertFalse(report['graph_ranker']['selection_applied'])

    def test_absent_keeps_original_order(self):
        self.initialize()
        report = self.output('project', 'next')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertNotIn('graph_ranker', report)

    def test_high_scores_cannot_bypass_unknown_or_budget(self):
        self.initialize(model(), blocked=True)
        report = self.output('project', 'next')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertNotIn('repair', report['graph_ranker']['scope'])
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['baseline'])

    def test_cpu_budget_gate_excludes_high_score(self):
        self.initialize(model(), budget=True)
        report = self.output('project', 'next')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertEqual(report['graph_ranker']['scope'], ['baseline'])

    def test_dominated_route_cannot_enter_neural_scope(self):
        self.initialize(model(), dominated=True)
        report = self.output('project', 'next')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertEqual(report['graph_ranker']['scope'], ['baseline'])

    def test_preparation_file_edits_do_not_replace_frozen_weights(self):
        self.initialize(model())
        before = self.output('project', 'next')
        self.contract['advisor_policy']['graph_ranker']['model']['readout'] = [-1]
        self.write_json('contract.json', self.contract)
        after = self.output('project', 'next')
        self.assertEqual(before['graph_ranker']['model_sha256'], after['graph_ranker']['model_sha256'])
        self.assertEqual(before['selected_run'], after['selected_run'])

    def test_brief_exposes_preferences_and_full_report(self):
        self.initialize(model())
        brief = self.output('project', 'next', '--brief')
        self.assertEqual(brief['graph_ranker']['preferred'][0], 'repair')
        self.assertTrue(brief['graph_ranker']['selection_applied'])
        full = json.loads(Path(brief['record']).read_text(encoding='utf-8'))
        self.assertIn('trace', full['graph_ranker'])

    def test_active_reservation_takes_precedence(self):
        self.initialize(model('shadow'))
        self.create('baseline')
        report = self.output('project', 'next')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertEqual(report['graph_ranker']['precedence'], 'ACTIVE_RESERVATION')

    def test_invalid_model_rejected_without_init_writes(self):
        config = model(); config['model']['input_weights'] = [[1]]
        result = self.initialize(config, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / '.rds/project.sqlite3').exists())

    def test_runtime_scorer_abstention_retains_baseline(self):
        self.initialize(model())
        from rds_owned_advisor import review
        from rds_graph_ranker import rank as original_rank
        def broken(config, graph, facts, candidates, **kwargs):
            graph = deepcopy(graph); graph['edges'].append({'from': 'missing', 'to': 'baseline',
                                                           'relation': 'prerequisite_for'})
            return original_rank(config, graph, facts, candidates, **kwargs)
        with patch('rds_graph_ranker.rank', side_effect=broken):
            report = review(ProjectStore(self.root))
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertEqual(report['graph_ranker']['status'], 'ABSTAINED')

    def steer(self, kind, **values):
        snap = self.snapshot()
        request = {'id': 'instruction', 'kind': kind, 'message': 'Synthetic current user instruction',
                   'contract_sha256': snap['contract_sha256'],
                   'expected_revision': snap.get('steering', {}).get('revision'), **values}
        return self.output('project', 'steer', '--request', self.write_json('steering.json', request),
                           '--source', 'current-user-message:synthetic-graph-ranker-case', '--user-directed')

    def test_real_human_preference_has_priority_over_neural_order(self):
        self.initialize(model())
        self.assertEqual(self.output('project', 'next')['selected_run'], 'repair')
        self.steer('redirect', prefer=['baseline'])
        report = self.output('project', 'next')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertEqual(report['graph_ranker']['precedence'], 'HUMAN_PREFERENCE')
        self.assertFalse(report['graph_ranker']['selection_applied'])
        self.assertEqual(report['selection_basis'], 'CURRENT_USER_PRIORITY_WITHIN_ADMITTED_FROZEN_ROUTES')
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['baseline'])

    def test_real_withdrawal_excludes_high_score_and_zero_new_launch_for_it(self):
        self.initialize(model())
        self.assertEqual(self.output('project', 'next')['selected_run'], 'repair')
        self.steer('redirect', withdraw=['repair'])
        report = self.output('project', 'next')
        self.assertEqual(report['graph_ranker']['scope'], ['baseline'])
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['baseline'])
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['baseline'])

    def test_real_pause_preserves_budget_and_starts_no_neural_route(self):
        self.initialize(model())
        before = self.snapshot()['budget']
        self.steer('pause')
        report = self.output('project', 'advance')
        self.assertIsNone(report['selected_run'])
        self.assertEqual(report['graph_ranker']['precedence'], 'HUMAN_PAUSE')
        self.assertEqual(self.starts(), [])
        self.assertEqual(before, self.snapshot()['budget'])


if __name__ == '__main__':
    unittest.main()
