"""Focused tests for scripts/rds_compass_antiu.py (plan L1-a)."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rds_compass_antiu as antiu  # noqa: E402

X, Y = {'var': 'x'}, {'var': 'y'}
A = {'op': 'add', 'args': [{'op': 'mul', 'args': [X, {'const': '2'}]}, {'const': '1'}]}   # 2x + 1
B = {'op': 'add', 'args': [{'op': 'mul', 'args': [Y, {'const': '3'}]}, {'const': '4'}]}   # 3y + 4
C = {'op': 'add', 'args': [{'op': 'mul', 'args': [X, {'const': '3'}]}, {'const': '4'}]}   # 3x + 4
A_VALUES = {'1': '3', '2': '5', '3': '7'}
C_VALUES = {'1': '7', '2': '10', '3': '13'}


def spine(depth, tail):
    """An add spine of ``2 * depth + 1`` nodes; depth 7 is exactly MAX_NODES = 15."""
    node = X
    for index in range(depth - 1):
        node = {'op': 'add', 'args': [node, {'const': str(index + 1)}]}
    return {'op': 'add', 'args': [node, {'const': str(tail)}]}


SPINE, SPINE_B = spine(7, 7), spine(7, 8)  # 15 nodes, one constant leaf apart


def case(law, values, origin='tool:t'):
    return {'law_ast': law, 'law': '', 'origin': origin, 'record_sha256': '', 'variable': 'x',
            'domain': {'min': 1, 'max': 3},
            'witness': [{'input': int(point), 'value': value} for point, value in values.items()]}


class LggTest(unittest.TestCase):
    def test_matching_operator_shares_the_node_and_keeps_constants(self):
        operator = antiu.generalize([A, B])
        self.assertEqual(antiu.canonical(operator['template_ast']),
                         '{"args":[{"args":[{"const":"$C1"},{"var":"$V2"}],"op":"mul"},'
                         '{"const":"$C3"}],"op":"add"}')
        self.assertEqual([hole['kind'] for hole in operator['holes']], ['CONST_INT', 'VAR', 'CONST_INT'])
        self.assertEqual(operator['holes'][1]['members'], ['x', 'y'])

    def test_arity_mismatch_becomes_an_expression_hole(self):
        operator = antiu.generalize([X, A])
        self.assertEqual(antiu.canonical(operator['template_ast']), '"$E1"')
        self.assertEqual(operator['holes'], [{'name': '$E1', 'kind': 'EXPR', 'max_nodes': 5}])

    def test_hole_members_are_snapshotted_not_aliased(self):
        third = {'op': 'add', 'args': [{'op': 'mul', 'args': [X, {'const': '5'}]}, {'const': '4'}]}
        operator = antiu.generalize([A, C, third])
        self.assertEqual(operator['holes'][0]['members'], ['2', '3', '5'])
        self.assertIsNot(operator['laws'][0], A)

    def test_refill_reproduces_every_case_exactly(self):
        operator = antiu.generalize([A, C])
        self.assertEqual([antiu.instantiate(operator['template_ast'], sigma) for sigma in operator['sigmas']],
                         operator['laws'])


class MdlTest(unittest.TestCase):
    def test_leaf_bits_follow_the_2_2_encoding(self):
        self.assertEqual(antiu.mdl_bits(X), 8)                 # 1 opcode, one name -> 0 name bits
        self.assertEqual(antiu.mdl_bits({'const': '3'}), 13)    # 8 + gamma(zigzag 3 = 7) = 8 + 5
        self.assertEqual(antiu.mdl_bits({'const': '1/2'}), 16)  # 8 + gamma(3) + gamma(5)

    def test_gamma_length_is_the_2_2_bit_count(self):
        self.assertEqual([antiu._gamma_bits(1), antiu._gamma_bits(2), antiu._gamma_bits(7)], [1, 3, 5])
        with self.assertRaises(ValueError):
            antiu._gamma_bits(0)


class AdmitTest(unittest.TestCase):
    def test_small_shared_templates_miss_the_ratio_gate(self):
        """Defect: a typical 5-node devset law pair scores R = 0.862, far below 1.5."""
        operator = antiu.generalize([A, C])
        self.assertEqual(antiu.model.size(A), 5)
        self.assertAlmostEqual(antiu._mdl(operator)['ratio'], 0.861789, places=6)
        self.assertFalse(antiu.admit(operator, [case(A, A_VALUES), case(C, C_VALUES)]))

    def test_ratio_gate_only_opens_at_max_nodes(self):
        """The gate opens only near MAX_NODES=15 and only when the two laws differ in a single leaf."""
        operator = antiu.generalize([SPINE, SPINE_B])
        self.assertEqual(antiu.model.size(SPINE), 15)
        self.assertGreater(antiu._mdl(operator)['ratio'], antiu.MIN_RATIO)
        self.assertTrue(antiu.admit(operator, [case(SPINE, A_VALUES), case(SPINE_B, A_VALUES)]))

    def test_one_support_case_is_never_admitted(self):
        self.assertFalse(antiu.admit(antiu.generalize([A]), [case(A, A_VALUES)]))

    def test_identical_cases_are_rejected_by_the_non_trivial_gate(self):
        operator = antiu.generalize([A, A])
        self.assertFalse(antiu._non_trivial(operator))
        self.assertFalse(antiu.admit(operator, [case(A, A_VALUES), case(A, A_VALUES)]))


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.operator = antiu.generalize([A, C])
        self.cases = [case(A, A_VALUES), case(C, C_VALUES)]

    @staticmethod
    def runner(table):
        """Canned values keyed by canonical AST: the contract must catch a bad replay without evaluating."""
        return lambda nodes, points, name='x': [table.get(antiu.canonical(node), [None] * len(points))
                                                for node in nodes]

    def test_witness_replay_boundary_and_mutants_all_pass(self):
        outcome = antiu.contract(self.operator, self.cases)
        self.assertTrue(outcome['ok'], outcome['failures'])
        self.assertEqual(outcome['checks']['witness'], [True, True])
        self.assertTrue(outcome['checks']['boundary'])
        self.assertTrue(outcome['checks']['discriminable'])

    def test_a_wrong_witness_value_fails_closed(self):
        outcome = antiu.contract(self.operator, [case(A, A_VALUES), case(C, {'1': '9', '2': '9', '3': '9'})])
        self.assertFalse(outcome['ok'])
        self.assertIn('WITNESS_REPLAY', [failure['check'] for failure in outcome['failures']])

    def test_a_constant_runner_fails_the_discriminability_gate(self):
        flat = [['0', '0', '0']]
        table = {antiu.canonical(node): flat for node in [A, C]}
        table.update({antiu.canonical(mutant): flat for node in [A, C] for mutant in antiu._mutants(node)})
        outcome = antiu.contract(self.operator, self.cases, self.runner(table))
        self.assertFalse(outcome['checks']['discriminable'])
        self.assertIn('DISCRIMINABILITY', [failure['check'] for failure in outcome['failures']])


class SignatureTest(unittest.TestCase):
    @staticmethod
    def plan(low, high):
        return {'inputs': {'x': {'min': str(low), 'max': str(high), 'integer': True}},
                'goal': {'observable': 'f'}, 'observables': ['f'],
                'tolerances': {'f': {'abs': '0', 'rel': '0'}},
                'candidates': [{'id': 'x1', 'inputs': {'x': '1'}}],
                'grammar': {'operators': ['add', 'mul']}}

    def test_law_content_does_not_change_the_bucket(self):
        self.assertEqual(antiu.domain_signature(self.plan(1, 3)), antiu.domain_signature(self.plan(1, 3)))

    def test_input_span_moves_the_bucket(self):
        self.assertNotEqual(antiu.domain_signature(self.plan(1, 3)), antiu.domain_signature(self.plan(1, 30)))


if __name__ == '__main__':
    unittest.main()
