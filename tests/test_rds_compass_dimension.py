"""Local dimensional legality: illegal forms are refused, unknown forms fail closed."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_compass_dimension import ZERO, dimension_of

MASS = (1, 0, 0, 0, 0, 0, 0)
LENGTH = (0, 1, 0, 0, 0, 0, 0)
TIME = (0, 0, 1, 0, 0, 0, 0)
DIMENSIONS = {'m': MASS, 'r': LENGTH, 't': TIME, 'k': ZERO}


def var(name):
    return {'var': name}


def const(value):
    return {'const': value}


def op(name, *args):
    return {'op': name, 'args': list(args)}


class DimensionTests(unittest.TestCase):
    def dim(self, node):
        return dimension_of(node, DIMENSIONS)

    def test_products_and_quotients_combine_exponents(self):
        self.assertEqual(self.dim(op('div', op('mul', var('m'), var('r')), op('pow', var('t'), const('2')))),
                         (1, 1, -2, 0, 0, 0, 0))

    def test_adding_a_mass_to_a_length_is_illegal_in_any_context(self):
        for name in ('add', 'sub', 'min', 'max', 'mod'):
            with self.subTest(op=name):
                self.assertIsNone(self.dim(op(name, var('m'), var('r'))))
                self.assertEqual(self.dim(op(name, var('m'), var('m'))), MASS)

    def test_transcendental_functions_need_a_dimensionless_argument(self):
        for name in ('exp', 'log', 'sin', 'cos'):
            with self.subTest(op=name):
                self.assertIsNone(self.dim(op(name, var('r'))))
                self.assertEqual(self.dim(op(name, op('div', var('r'), var('r')))), ZERO)

    def test_square_root_needs_even_exponents(self):
        self.assertEqual(self.dim(op('sqrt', op('mul', var('r'), var('r')))), LENGTH)
        self.assertIsNone(self.dim(op('sqrt', var('r'))))

    def test_a_power_needs_a_dimensionless_integer_exponent_on_a_dimensioned_base(self):
        self.assertEqual(self.dim(op('pow', var('r'), const('-3'))), (0, -3, 0, 0, 0, 0, 0))
        self.assertIsNone(self.dim(op('pow', var('r'), const('1/2'))))
        self.assertIsNone(self.dim(op('pow', var('r'), var('k'))))
        self.assertIsNone(self.dim(op('pow', var('k'), var('r'))))
        # A dimensionless base may take any dimensionless exponent.
        self.assertEqual(self.dim(op('pow', var('k'), const('1/2'))), ZERO)

    def test_a_conditional_needs_a_dimensionless_test_and_matching_branches(self):
        self.assertEqual(self.dim(op('ite', var('k'), var('r'), var('r'))), LENGTH)
        self.assertIsNone(self.dim(op('ite', var('r'), var('r'), var('r'))))
        self.assertIsNone(self.dim(op('ite', var('k'), var('r'), var('m'))))

    def test_unbalanced_equations_are_not_this_modules_question(self):
        # A de-dimensionalised law (G absorbed) is still locally legal on each side.
        self.assertEqual(self.dim(op('div', op('mul', var('m'), var('m')), op('pow', var('r'), const('2')))),
                         (2, -2, 0, 0, 0, 0, 0))

    def test_unknown_or_malformed_forms_fail_closed(self):
        cases = {
            'unknown op': op('tanh', var('k')),
            'wrong arity': op('mul', var('m')),
            'extra key': {'op': 'neg', 'args': [var('m')], 'note': 'x'},
            'args not a list': {'op': 'neg', 'args': (var('m'),)},
            'undeclared variable': var('q'),
            'bad constant': const('not a number'),
            'zero denominator': const('1/0'),
            'not a node': 'm',
        }
        for label, node in cases.items():
            with self.subTest(label):
                self.assertIsNone(self.dim(node))
        self.assertIsNone(dimension_of(var('m'), None))

    def test_declared_dimensions_must_be_integer_seven_tuples(self):
        for bad in ((1, 0, 0), [1, 0, 0, 0, 0, 0, 0], (True, 0, 0, 0, 0, 0, 0), (1.0, 0, 0, 0, 0, 0, 0)):
            with self.subTest(bad=bad):
                self.assertIsNone(dimension_of(var('x'), {'x': bad}))

    def test_constants_and_pi_are_dimensionless(self):
        self.assertEqual(self.dim(const('3/2')), ZERO)
        self.assertEqual(self.dim(op('pi')), ZERO)
        self.assertEqual(self.dim(op('neg', op('abs', var('m')))), MASS)


if __name__ == '__main__':
    unittest.main()
