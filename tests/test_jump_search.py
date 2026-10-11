import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import rds_jump_search as search


def var(name):
    return {'var': name}


def const(value):
    return {'const': value}


def op(name, left, right):
    return {'op': name, 'args': [left, right]}


class JumpSearchTests(unittest.TestCase):
    def test_exact_domain_and_modulo(self):
        self.assertEqual(search.evaluate(const(4), {}), 4)
        self.assertEqual(search.evaluate(op('mod', const(-7), const(3)), {'x': 0}), 2)
        for ast, env in [(var('missing'), {'x': 1}), (const(True), {'x': 1}),
                         (var('x'), {'x': True}), (op('mod', var('x'), const(0)), {'x': 2}),
                         (op('mul', var('x'), var('x')), {'x': search.INTEGER_BOUND})]:
            with self.subTest(ast=ast, env=env), self.assertRaises(ValueError):
                search.evaluate(ast, env)

    def test_strict_ast_cannot_erase_domain_errors(self):
        undefined = op('mod', var('x'), const(0))
        with self.assertRaises(search.DomainError):
            search.evaluate(op('mul', undefined, const(0)), {'x': 2})
        with self.assertRaises(ValueError):
            search.evaluate({'const': 2, 'source': 'not syntax'}, {'x': 1})
        with self.assertRaises(ValueError):
            search.evaluate({'op': ['add'], 'args': [var('x'), const(1)]}, {'x': 1})
        cyclic = {'op': 'add', 'args': [const(1), None]}
        cyclic['args'][1] = cyclic
        with self.assertRaises(ValueError):
            search.evaluate(cyclic, {'x': 1})

    def test_diagnostics_keep_counterexamples(self):
        rows = [{'inputs': {'x': 2, 'y': 3}, 'value': 6}, {'inputs': {'x': 1, 'y': 1}, 'value': 1}]
        report = search.diagnose(rows, op('add', var('x'), var('y')))
        self.assertEqual([r['residual'] for r in report['residuals']], [1, -1])
        self.assertEqual(len(report['counterexamples']), 2)
        self.assertEqual(report['max_abs_residual'], 1)

    def test_observations_change_generated_solution(self):
        envs = [{'x': 2, 'y': 3}, {'x': 4, 'y': 5}, {'x': -2, 'y': 4}]
        for target, expected in [(lambda x, y: x + y, 'add'), (lambda x, y: x * y, 'mul')]:
            rows = [{'inputs': env, 'value': target(**env)} for env in envs]
            result = search.synthesize(rows, ['x', 'y'], [], ['add', 'sub', 'mul'], max_nodes=3)
            self.assertEqual(result['status'], 'FIT_FOUND')
            self.assertEqual(result['candidate']['op'], expected)
            for env in envs:
                self.assertEqual(search.evaluate(result['candidate'], env), target(**env))

    def test_training_coincidence_keeps_probe_distinct_expression(self):
        # x and y have identical training values. Deduplicating value vectors
        # would discard y and make the separating candidate unreachable.
        result = search.synthesize([{'inputs': {'x': 1, 'y': 1}, 'value': 1}],
                                   ['x', 'y'], [], [], max_nodes=1,
                                   probes=[{'x': 2, 'y': 3}], baseline=var('x'))
        self.assertEqual(result['candidate'], var('y'))
        self.assertEqual(result['probe'], {'x': 2, 'y': 3})
        self.assertEqual(result['probe_predictions'], {'candidate': 3, 'rival': 2})
        self.assertEqual(result['equivalence'], 'SEPARATED_ON_DECLARED_PROBE')

    def test_five_node_search_generalizes_to_unused_declared_input(self):
        envs = [{'x': 2, 'y': 3}, {'x': 3, 'y': 4}, {'x': -2, 'y': 5}]
        rows = [{'inputs': env, 'value': env['x'] * env['y'] + env['x']} for env in envs]
        result = search.synthesize(rows, ['x', 'y'], [], ['add', 'mul'], max_nodes=5,
                                   probes=[{'x': 5, 'y': 2}], baseline=op('add', var('x'), var('y')))
        self.assertEqual(result['status'], 'FIT_FOUND')
        self.assertEqual(search.evaluate(result['candidate'], {'x': 5, 'y': 2}), 15)
        self.assertEqual(result['probe_predictions'], {'candidate': 15, 'rival': 7})
        self.assertGreater(result['generated'], 2)

    def test_conflicting_measurements_cannot_be_hidden(self):
        rows = [{'inputs': {'x': 2}, 'value': 2}, {'inputs': {'x': 2}, 'value': 3}]
        result = search.synthesize(rows, ['x'], [], ['add'], max_nodes=3)
        self.assertEqual(result['status'], 'NO_FIT')

    def test_canonical_equivalence_is_not_general_semantic_equivalence(self):
        baseline = op('add', const(0), var('x'))
        result = search.synthesize([{'inputs': {'x': 2}, 'value': 2}], ['x'], [0], ['add'],
                                   max_nodes=3, baseline=baseline)
        self.assertEqual(result['equivalence'], 'EXACT_CANONICAL_EQUIVALENCE')
        coincident = search.synthesize([{'inputs': {'x': 2, 'y': 2}, 'value': 2}],
                                       ['x', 'y'], [], [], seeds=[var('y')],
                                       max_nodes=1, baseline=var('x'))
        self.assertEqual(coincident['equivalence'], 'OBSERVATIONAL_COINCIDENCE_ONLY')

    def test_retained_seed_checked_and_counterexample_filters(self):
        seed = op('mul', var('x'), var('y'))
        rows = [{'inputs': {'x': 2, 'y': 3}, 'value': 6}]
        result = search.synthesize(rows, ['x', 'y'], [], ['mul'], seeds=[seed], max_nodes=3, max_candidates=1)
        self.assertEqual(result['candidate'], seed)
        self.assertEqual(result['candidate_origin'], 'retained_seed')
        self.assertEqual(result['seed_index'], 0)
        rows.append({'inputs': {'x': 3, 'y': 4}, 'value': 9})
        rejected = search.synthesize(rows, ['x', 'y'], [], ['mul'], seeds=[seed], max_nodes=3)
        self.assertEqual(rejected['status'], 'NO_FIT')
        self.assertGreater(rejected['filtered'], 0)
        with self.assertRaises(ValueError):
            search.synthesize(rows, ['x', 'y'], [], ['add'], seeds=[seed])

    def test_search_budget_bounds_raw_duplicates(self):
        rows = [{'inputs': {'x': 2}, 'value': 999}]
        result = search.synthesize(rows, ['x'], [0, 1], ['add', 'mul'], max_nodes=9, max_candidates=7)
        self.assertEqual(result['attempted'], 7)
        self.assertLessEqual(result['generated'], 7)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['exhaustion'], 'CANDIDATE_LIMIT')
        self.assertEqual(result['status'], 'SEARCH_TRUNCATED')

    def test_exhausted_no_candidate_is_scoped(self):
        result = search.synthesize([{'inputs': {'x': 2}, 'value': 99}], ['x'], [], [], max_nodes=1)
        self.assertEqual(result['status'], 'NO_FIT')
        self.assertFalse(result['truncated'])
        self.assertEqual(result['exhaustion'], 'FINITE_GRAMMAR_EXHAUSTED')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_conservative_inputs_caps_and_undefined_probe(self):
        row = [{'inputs': {'x': 1}, 'value': 1}]
        for kwargs in [{'max_candidates': 10001}, {'max_nodes': 10}, {'max_nodes': True},
                       {'probes': [{'x': 1, 'z': 2}]}, {'seeds': [const(5)]}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                search.synthesize(row, ['x'], [], ['add'], **kwargs)
        result = search.synthesize(row, ['x'], [0], [], max_nodes=1,
                                   probes=[{'x': 0}], baseline=op('mod', var('x'), const(0)))
        self.assertEqual(result['status'], 'FIT_FOUND')
        self.assertIsNone(result['probe'])
        self.assertEqual(result['diagnostics']['counterexamples'][0]['status'], 'DOMAIN_ERROR')


if __name__ == '__main__':
    unittest.main()
