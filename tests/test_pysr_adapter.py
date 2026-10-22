"""Tests for PySR expression adapter to RDS dialect.

Verifies conversion of SymPy / Python style expressions into the RDS dialect,
strict fail-closed rejection on unsupported constructs (no silent rewrites),
domain constraint extraction, and equivalence proposals.
Does not import or depend on the external pysr package.
"""
from fractions import Fraction
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'benchmark' / 'newtonbench'))

import rds_compass_model as model
from pysr_adapter import adapt_pysr_expression, convert_pysr_to_rds, adapt


class TestPySRAdapter(unittest.TestCase):
    def test_aliases_identical(self):
        self.assertIs(adapt, adapt_pysr_expression)
        self.assertIs(convert_pysr_to_rds, adapt_pysr_expression)

    def test_quadratic_polynomial(self):
        # 1. Multi-variable quadratic with transcendental cos: a*x**2 + b*cos(z) + c
        vars_all = ['a', 'b', 'c', 'x', 'z']
        results = adapt_pysr_expression('a*x**2 + b*cos(z) + c', variables=vars_all)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertTrue(item['supported'])
        self.assertIsNone(item['reason'])
        self.assertIsInstance(item['expression'], str)

        # Ensure the rendered RDS dialect can be parsed and compiled by rds_compass_model
        parsed_ast = model.parse(item['expression'], variables=vars_all)
        self.assertEqual(parsed_ast['op'], 'add')

        # Test evaluation on concrete numbers
        env = {'a': Fraction(2), 'b': Fraction(3), 'c': Fraction(1), 'x': Fraction(4), 'z': Fraction(0)}
        val = model.evaluate(parsed_ast, env)
        # 2 * 4**2 + 3 * cos(0) + 1 = 2*16 + 3*1 + 1 = 36
        self.assertEqual(val, Fraction(36))

        # 2. Standard single-variable quadratic: x**2 + 2*x + 1
        results2 = adapt_pysr_expression('x**2 + 2*x + 1', variables=['x'])
        self.assertEqual(len(results2), 1)
        self.assertTrue(results2[0]['supported'])
        self.assertIsNone(results2[0]['reason'])
        parsed2 = model.parse(results2[0]['expression'], variables=['x'])
        self.assertEqual(model.evaluate(parsed2, {'x': Fraction(3)}), Fraction(16))

    def test_equivalent_exp_log_representation(self):
        # exp(1.3*log(c)) is the valid RDS equivalent form for c**1.3 on c > 0
        results = adapt_pysr_expression('exp(1.3*log(c))', variables=['c'])
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertTrue(item['supported'])
        self.assertIsNone(item['reason'])
        # Domain note must reflect log requirement: c > 0
        self.assertIsNotNone(item['domain_notes'])
        self.assertIn('c > 0', item['domain_notes'])

        # Must parse and evaluate cleanly in rds_compass_model
        parsed = model.parse(item['expression'], variables=['c'])
        # For c=1, exp(1.3 * log(1)) = exp(0) = 1
        self.assertEqual(model.evaluate(parsed, {'c': Fraction(1)}), Fraction(1))

    def test_unsupported_fractional_power_must_not_silently_rewrite(self):
        # c**1.3 has exponent 13/10, whose denominator 10 exceeds MAX_ROOT (8).
        # Must fail-closed: supported=False, reason must mention exponent and denominator constraint.
        results = adapt_pysr_expression('c**1.3', variables=['c'])
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertFalse(item['supported'], "c**1.3 must not be silently accepted or rewritten")
        self.assertIsNotNone(item['reason'])
        reason_lower = item['reason'].lower()
        self.assertTrue('13/10' in item['reason'] or '1.3' in item['reason'])
        self.assertTrue('8' in item['reason'] or 'denominator' in reason_lower or '分母' in item['reason'])

        # When suggest_equivalent=True is requested, return original unsupported PLUS explicit equivalent proposal
        suggested = adapt_pysr_expression('c**1.3', variables=['c'], suggest_equivalent=True)
        self.assertEqual(len(suggested), 2)
        # First must remain the un-rewritten unsupported record
        self.assertFalse(suggested[0]['supported'])
        # Second is the explicit equivalent proposal
        self.assertTrue(suggested[1]['supported'])
        self.assertIn('exp', suggested[1]['expression'])
        self.assertIn('log', suggested[1]['expression'])
        self.assertIsNotNone(suggested[1]['domain_notes'])
        self.assertIn('c > 0', suggested[1]['domain_notes'])

    def test_undeclared_unknown_variables(self):
        # Variable 'y' not in declared set ['x']
        results = adapt_pysr_expression('x + y', variables=['x'])
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertFalse(item['supported'])
        self.assertIn('y', item['reason'])
        self.assertTrue('declared' in item['reason'].lower() or '声明' in item['reason'])

        # Missing multiple variables
        results_multi = adapt_pysr_expression('a*x**2 + b*cos(z) + c', variables=['x'])
        self.assertFalse(results_multi[0]['supported'])

    def test_empty_expression(self):
        for empty_input in ('', '   ', '\t\n', None):
            results = adapt_pysr_expression(empty_input, variables=['x'])
            self.assertEqual(len(results), 1)
            item = results[0]
            self.assertFalse(item['supported'])
            self.assertTrue('空' in item['reason'] or 'empty' in item['reason'].lower())

    def test_unsupported_functions(self):
        for func_expr in ('custom_op(x)', 'tan(x)', 'sinh(x)'):
            results = adapt_pysr_expression(func_expr, variables=['x'])
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0]['supported'])
            self.assertTrue('unsupported' in results[0]['reason'].lower() or '不支持' in results[0]['reason'])

    def test_variable_exponent_unsupported(self):
        # In RDS dialect, exponents must be constant
        results = adapt_pysr_expression('x**y', variables=['x', 'y'])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]['supported'])
        self.assertTrue('constant' in results[0]['reason'].lower() or '常量' in results[0]['reason'])

    def test_exponent_magnitude_unsupported(self):
        # Exponent 9 exceeds MAX_POWER (8)
        results = adapt_pysr_expression('x**9', variables=['x'])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]['supported'])
        self.assertIn('8', results[0]['reason'])

    def test_pi_constant_support(self):
        # pi is built-in when not declared as variable
        results = adapt_pysr_expression('sin(pi * x)', variables=['x'])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['supported'])
        parsed = model.parse(results[0]['expression'], variables=['x'])
        val = model.evaluate(parsed, {'x': Fraction(1, 2)})
        # sin(pi * 1/2) is evaluated rigorously as an irrational enclosure (Inexact) around 1
        self.assertIsInstance(val, model.Inexact)
        slack = Fraction(1, 10 ** 35)
        self.assertTrue(val - val.radius - slack <= 1 <= val + val.radius + slack)


if __name__ == '__main__':
    unittest.main()
