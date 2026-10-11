"""Synthetic PR303 triggers; declarations never supply scientific evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor_search import search_directions
from rds_advisor_workset import _compact, build, DETAIL_BYTES
from rds_artifacts import ArtifactFact
from rds_owned_advisor import review
from rds_project import ProjectStore, canonical
from rds_quick import brief, compact_brief_planning
from test_rds_selection_review import fixture
import test_rds_owned_advisor as owned_fixture


def observed(value, locator):
    return ArtifactFact({'kind': 'OBSERVED', 'value': value,
                         'source': {'path': 'metrics.json', 'sha256': 'a' * 64, 'locator': locator}},
                        reading_identity=('metrics.json', locator))


def affirmative_check_fixture(group, gap=None):
    graph, context = fixture()
    target = group + '_gap'
    context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
    context['decision']['affirmations'] = {
        name: [{'fact': name + '_ok', 'value': True}, {'fact': name + '_gap', 'value': True}]
        for name in ('portable', 'applicable')}
    context['facts'] = {name: observed(True, '/' + name) for name in
                        ('goal', 'portable_ok', 'portable_gap', 'applicable_ok', 'applicable_gap')}
    context['facts'][target] = observed(gap, '/' + target)
    action = graph['nodes'][0]['executable']['action']
    action.update(target=target, goal_contribution={
        'target': target, 'path': [target], 'source': 'declared-affirmative-plan.json'})
    return graph, context


class PlanningReviewTests(unittest.TestCase):
    def test_ready_declared_affirmative_checks_scope_open_facts_locally(self):
        for group in ('portable', 'applicable'):
            for gap in (False, None, 'reported_true'):
                with self.subTest(group=group, gap=gap):
                    graph, context = affirmative_check_fixture(group, gap)
                    target = group + '_gap'
                    if gap == 'reported_true':
                        context['facts'][target] = {'value': True, 'source': 'reported-only.json'}
                    before = deepcopy((graph, context))
                    search = search_directions(graph, context)
                    self.assertEqual(search['candidates'][0]['status'], 'READY')
                    review = search['selection_review']
                    self.assertEqual(review['candidates'][0]['goal_contribution']['status'], 'DECLARED_PATH')
                    self.assertEqual(review['planning']['scope'], 'LOCAL')
                    self.assertEqual(review['planning']['open_predicates'], [target])
                    self.assertEqual(review['planning']['local_checks'][0]['goal_target'], target)
                    self.assertEqual(review['goal']['status'], 'TRUE')
                    self.assertEqual(review['goal']['triple_affirmative'][group]['conditions'][1]['affirms'],
                                     'FALSE' if gap is False else 'UNKNOWN')
                    self.assertEqual(review['authorization'], 'UNCHANGED')
                    self.assertEqual((graph, context), before)

    def test_undeclared_and_closed_affirmative_targets_cannot_scope_an_open_gap(self):
        for group in ('portable', 'applicable'):
            for target in ('not_declared', group + '_ok', 'goal'):
                with self.subTest(group=group, target=target):
                    graph, context = affirmative_check_fixture(group)
                    action = graph['nodes'][0]['executable']['action']
                    action.update(target=target, goal_contribution={
                        'target': target, 'path': [target], 'source': 'declared-plan.json'})
                    before = deepcopy((graph, context))
                    search = search_directions(graph, context)
                    self.assertEqual(search['candidates'][0]['status'], 'READY')
                    review = search['selection_review']
                    self.assertEqual(review['candidates'][0]['goal_contribution']['status'],
                                     'UNKNOWN' if target == 'not_declared' else 'DECLARED_PATH')
                    self.assertEqual(review['planning']['scope'], 'GLOBAL')
                    self.assertEqual(review['planning']['open_predicates'], [group + '_gap'])
                    self.assertEqual((graph, context), before)

    def test_affirmative_targets_still_require_existing_path_and_readiness_gates(self):
        for group in ('portable', 'applicable'):
            for gate in ('valid_map', 'not_map_goal', 'wrong_start', 'unready'):
                with self.subTest(group=group, gate=gate):
                    graph, context = affirmative_check_fixture(group)
                    target = group + '_gap'
                    context['dependency_map'] = {'schema': 1, 'nodes': [
                        {'id': target, 'status': 'UNKNOWN', 'source': 'synthetic existing obligation'},
                        {'id': 'other', 'status': 'UNKNOWN', 'source': 'synthetic other obligation'}],
                        'hyperedges': [], 'goals': [target]}
                    if gate == 'not_map_goal':
                        context['dependency_map']['goals'] = ['other']
                    elif gate == 'wrong_start':
                        graph['nodes'][0]['executable']['action']['target'] = 'other'
                    elif gate == 'unready':
                        graph['nodes'][0]['executable']['preconditions'] = [
                            {'fact': 'missing_prerequisite', 'value': True}]
                    before = deepcopy((graph, context))
                    search = search_directions(graph, context)
                    review = search['selection_review']
                    if gate == 'unready':
                        self.assertFalse(any(c['action'].get('id') == 'probe' and c['status'] == 'READY'
                                             for c in search['candidates']))
                        self.assertEqual(review['planning']['local_checks'], [])
                    else:
                        self.assertEqual(review['candidates'][0]['goal_contribution']['status'],
                                         'DECLARED_PATH' if gate == 'valid_map' else 'UNKNOWN')
                    self.assertEqual(review['planning']['scope'], 'LOCAL' if gate == 'valid_map' else 'GLOBAL')
                    self.assertEqual(context['facts'][target]['value'], None)
                    self.assertEqual((graph, context), before)

    def test_compact_selection_retains_planning_and_exact_originals(self):
        value = {'status': 'REVIEWED', 'selected_run': None,
                 'next_move': {'kind': 'RESOLVE_PREMISE'}, 'selection_basis': None,
                 'planning': {'scope': 'GLOBAL', 'local_checks': [],
                              'open_predicates': ['p' * 300 + str(i) for i in range(8)],
                              'ready_obligations': [], 'omitted_ready_obligations': 0}}
        original = deepcopy(value)
        with tempfile.TemporaryDirectory() as raw:
            result = _compact(ProjectStore(raw), value)
            self.assertLessEqual(len(canonical(result).encode('utf-8')), DETAIL_BYTES)
            self.assertEqual(json.loads(Path(result['original']['path']).read_text(encoding='utf-8')), original)
            self.assertIn('planning', result)
            self.assertEqual(result['planning']['scope'], 'GLOBAL')
            self.assertTrue(result['planning']['details_omitted'])
            self.assertEqual(json.loads(Path(result['planning']['original']['path']).read_text(encoding='utf-8')),
                             original['planning'])
        self.assertEqual(value, original)

    def test_declared_affirmatives_report_only_nonaffirming_fact_identities(self):
        for gap in ('FALSE', 'UNKNOWN', 'REUSED', 'REPORTED'):
            with self.subTest(gap=gap):
                graph, context = fixture()
                context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
                context['decision']['affirmations'] = {
                    'portable': [{'fact': 'portable_ok', 'value': True}, {'fact': 'portable_gap', 'value': True}],
                    'applicable': [{'fact': 'applicable_ok', 'value': True}, {'fact': 'applicable_gap', 'value': True}]}
                context['facts'] = {name: observed(True, '/' + name) for name in
                                    ('goal', 'portable_ok', 'portable_gap', 'applicable_ok', 'applicable_gap')}
                for name in ('portable_gap', 'applicable_gap'):
                    context['facts'][name] = (observed(False, '/' + name) if gap == 'FALSE' else
                                             observed(None, '/' + name) if gap == 'UNKNOWN' else
                                             observed(True, '/goal') if gap == 'REUSED' else
                                             {'value': True, 'source': 'reported.json'})
                before = deepcopy((graph, context))
                selection = search_directions(graph, context)['selection_review']
                expected = ['portable_gap', 'applicable_gap']
                if gap == 'REUSED':
                    expected.insert(0, 'goal')  # FOUND also loses independent affirmation.
                self.assertEqual(selection['planning']['open_predicates'], expected)
                self.assertEqual(selection['goal']['status'], 'TRUE')
                self.assertEqual(selection['authorization'], 'UNCHANGED')
                self.assertEqual((graph, context), before)

    def test_closed_contribution_cannot_scope_an_unrelated_open_goal_as_local(self):
        for value in (False, None):
            graph, context = fixture()
            context['decision']['goal_conditions'] = [{'fact': name, 'value': True} for name in ('A', 'B')]
            context['facts'] = {'A': {'value': True, 'source': 'reported.json'},
                                'B': {'value': value, 'source': 'reported.json'}}
            action = graph['nodes'][0]['executable']['action']
            action.update(target='local_probe', goal_contribution={
                'target': 'A', 'path': ['local_probe'], 'source': 'declared-plan.json'})
            before = deepcopy((graph, context))
            selection = search_directions(graph, context)['selection_review']
            self.assertEqual(selection['planning']['open_predicates'], ['B'])
            self.assertEqual(selection['planning']['scope'], 'GLOBAL')
            self.assertEqual(selection['planning']['local_checks'][0]['goal_target'], 'A')
            self.assertEqual((graph, context), before)
            # The same declared check can scope an open A; it never makes A TRUE.
            context['facts']['A']['value'] = False
            selection = search_directions(graph, context)['selection_review']
            self.assertEqual(selection['planning']['scope'], 'LOCAL')
            self.assertEqual(selection['goal']['conditions'][0]['truth'], 'FALSE')

    def test_colliding_candidate_prefixes_are_marked_and_full_cas_is_unchanged(self):
        graph, context = fixture()
        context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
        action = graph['nodes'][0]['executable']['action']
        action.update(target='probe', goal_contribution={
            'target': 'goal', 'path': ['probe'], 'source': 'declared-plan.json'})
        graph['nodes'] = [deepcopy(graph['nodes'][0]) for _ in range(5)]
        for i, node in enumerate(graph['nodes']):
            node['id'] = 'p' * 70 + str(i)
            node['executable']['action']['id'] = 'probe' + str(i)
        advice = {'recommendations': [{'search': search_directions(graph, context)}]}
        original = deepcopy(advice)
        with tempfile.TemporaryDirectory() as raw:
            summary = brief(raw, advice, 'test')
            checks = summary['planning']['local_checks']
            self.assertEqual(len(checks), 5)
            self.assertTrue(all(row.get('candidate_truncated') is True for row in checks))
            self.assertEqual(len({row['candidate'] for row in checks}), 1)
            self.assertEqual(json.loads(Path(summary['record']).read_text(encoding='utf-8')), original)
            summary['workflow'] = {'padding': 'x' * 1024}
            compact_brief_planning(summary)
            self.assertEqual(json.loads(Path(summary['record']).read_text(encoding='utf-8')), original)
        self.assertEqual(advice, original)


class OwnedPlanningReviewTests(unittest.TestCase):
    def setUp(self):
        self.harness = owned_fixture.OwnedAdvisorCLITests(methodName='runTest')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def initialize(self, gate):
        def mutate(policy):
            baseline = policy['graph']['nodes'][0]['executable']['action']
            baseline['goal_contribution'] = {'target': 'baseline.score', 'path': ['baseline.score'],
                                             'source': 'frozen-fixture'}
            if gate == 'resource':
                policy['routes'][0]['manifest']['resource_estimates']['cpu_seconds'] = 11
            elif gate == 'control':
                policy['routes'][0]['manifest'].update(arm='treatment', control_id='repair')
            elif gate == 'feasibility':
                policy['feasibility'] = {'schema': 1, 'pilots': [], 'max_pilot_wall_seconds': 3,
                    'models': {}, 'plans': [{'id': 'complete', 'steps': ['baseline', 'repair'],
                        'goal_facts': ['baseline.score'], 'recovery_wall_seconds': 0,
                        'delivery_wall_seconds': 0}]}
        self.harness.initialize(mutate_policy=mutate)
        from rds_tms_store import current, save
        saved = current(self.harness.root)
        dependency = deepcopy(saved['dependency_map']) if saved else {
            'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}
        # A pre-existing goal obligation, explicitly UNKNOWN; the candidate adds no fact.
        dependency['nodes'].append({'id': 'baseline.score', 'status': 'UNKNOWN',
                                    'source': 'synthetic original goal declaration'})
        dependency['goals'].append('baseline.score')
        save(self.harness.root, dependency, expected=saved['sha256'] if saved else None)
        if gate == 'steering':
            snap = self.harness.snapshot()
            request = {'id': 'pause-fixture', 'kind': 'pause', 'message': 'Synthetic pause',
                       'contract_sha256': snap['contract_sha256'], 'expected_revision': None}
            self.harness.output('project', 'steer', '--request', self.harness.write_json('pause.json', request),
                                '--source', 'current-user-message:synthetic-case', '--user-directed')

    def assert_blocked_planning(self, report, before):
        self.assertIsNone(report['selected_run'])
        search = next(row['search'] for row in report['recommendations'] if 'search' in row)
        self.assertEqual(search['selection_review']['planning']['scope'], 'LOCAL')
        self.assertEqual(report['planning']['local_checks'], [])
        self.assertEqual(report['planning']['scope'], 'GLOBAL')
        view = build(ProjectStore(self.harness.root), report)
        self.assertEqual(view['status'], 'CURRENT')
        self.assertEqual(view['selection']['planning'], report['planning'])
        summary = brief(self.harness.root, report, 'test')
        self.assertEqual(summary['planning']['scope'], 'GLOBAL')
        self.assertNotIn('local_checks', summary['planning'])
        self.assertEqual(json.loads(Path(summary['record']).read_text(encoding='utf-8')), report)
        after = self.harness.snapshot()
        for key in ('budget', 'runs', 'receipts', 'contract'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(self.harness.starts(), [])
        self.assertEqual(report['scientific_support'], 'UNKNOWN')
        self.assertEqual(report['authorization'], 'UNCHANGED')

    def test_owned_resource_gate_filters_working_set_local_checks(self):
        self.initialize('resource')
        before = self.harness.snapshot()
        report = self.harness.output('advise')
        self.assertEqual(report['next_move']['kind'], 'RESOURCE_BLOCKED')
        self.assert_blocked_planning(report, before)

    def test_owned_control_gate_filters_working_set_local_checks(self):
        self.initialize('control')
        self.assert_blocked_planning(self.harness.output('advise'), self.harness.snapshot())

    def test_owned_steering_gate_filters_working_set_local_checks(self):
        self.initialize('steering')
        before = self.harness.snapshot()
        report = self.harness.output('advise')
        self.assertEqual(report['next_move']['kind'], 'HUMAN_STEERING')
        self.assert_blocked_planning(report, before)

    def test_real_owned_feasibility_unknown_forecast_filters_planning(self):
        self.initialize('feasibility')
        before = self.harness.snapshot()
        report = self.harness.output('advise')
        self.assertEqual(report['feasibility']['admitted_runs'], [])
        self.assertEqual(report['feasibility']['plans'][0]['status'], 'UNKNOWN')
        self.assert_blocked_planning(report, before)

    def test_owned_admitted_route_keeps_declared_check_without_creating_evidence(self):
        self.initialize('none')
        before = self.harness.snapshot()
        report = self.harness.output('advise')
        self.assertEqual(report['selected_run'], 'baseline')
        self.assertEqual(report['planning']['scope'], 'LOCAL')
        self.assertEqual(report['planning']['local_checks'], [
            {'candidate': 'baseline:baseline', 'target': 'baseline.score', 'goal_target': 'baseline.score'}])
        self.assertEqual(report['planning']['open_predicates'], ['baseline.score'])
        self.assertEqual(report['context']['facts']['baseline.score']['value'], None)
        after = self.harness.snapshot()
        for key in ('budget', 'runs', 'receipts', 'contract'):
            self.assertEqual(after[key], before[key])

    def test_real_working_set_cli_retains_partition_for_large_owned_goal_set(self):
        names = ['goal_' + 'p' * 90 + str(i) for i in range(32)]
        def mutate(policy):
            policy['context']['decision']['goal_conditions'] = [
                {'fact': name, 'op': 'gte', 'value': .5} for name in names]
            policy['observations'].extend({'fact': name, 'run_id': 'baseline', 'path': 'outputs/baseline.json',
                'selector': {'pointer': '/score'}, 'format': 'json'} for name in names)
        self.harness.initialize(mutate_policy=mutate)
        before = self.harness.snapshot()
        output = self.harness.output('advise', '--working-set', '--brief')
        view = output['working_set']
        self.assertEqual(view['status'], 'CURRENT')
        selection = view['selection']
        self.assertLessEqual(len(canonical(selection).encode('utf-8')), DETAIL_BYTES)
        self.assertEqual(selection['planning']['scope'], 'GLOBAL')
        exact = json.loads(Path(selection['planning']['original']['path']).read_text(encoding='utf-8'))
        self.assertEqual(exact['open_predicates'], names)
        full = json.loads(Path(output['record']).read_text(encoding='utf-8'))
        self.assertEqual(full['planning'], exact)
        self.assertEqual(full['context']['decision'], before['contract']['advisor_policy']['context']['decision'])
        after = self.harness.snapshot()
        for key in ('budget', 'runs', 'receipts', 'contract'):
            self.assertEqual(after[key], before[key])


if __name__ == '__main__':
    unittest.main()
