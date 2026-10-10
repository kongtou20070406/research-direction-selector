"""Finite independent method oracles; no scientific uncertainty from correlated rows."""
from copy import deepcopy
from fractions import Fraction
from itertools import permutations
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_result_tools import compare_paired_metrics, check_residuals
from rds_tools import extract_function


def points(values, candidate=False):
    return {'values': values, 'sample_ids': ['s' + str(i) for i in range(len(values))],
            'identity': {'run_id': 'candidate' if candidate else 'reference',
                         'source_path': 'candidate.json' if candidate else 'reference.json',
                         'source_sha256': ('a' if candidate else 'b') * 64,
                         'data_sha256': 'c' * 64, 'data_split': 'heldout', 'evaluator_sha256': 'd' * 64,
                         'metric': 'loss', 'metric_definition': 'per-example loss', 'reduction': 'none',
                         'unit': 'dimensionless', 'direction': 'minimize'}}


SAMPLING = {'unit': 'example', 'independent': False, 'pairing': 'matched_sample_ids'}
DOMAIN = {'domain': 'four explicitly supplied points', 'precision': 'float64',
          'atol': 0.25, 'rtol': 0, 'independent_reference': True}


class PairedMethodTests(unittest.TestCase):
    def test_mixed_original_pairs_preserve_low_bits_before_subtraction(self):
        for candidate, baseline in (([10**16 + 1], [1e16]), ([1e16], [10**16 + 1]),
                                    ([10**16 + 1, 1e16], [1e16, 10**16 + 1])):
            expected = float(sum((Fraction(c) - Fraction(b) for c, b in zip(candidate, baseline)),
                                 Fraction()) / len(candidate))
            c, b = points(candidate, True), points(baseline)
            before = deepcopy([c, b])
            result = compare_paired_metrics(c, b, dict(SAMPLING, independent=True))
            self.assertEqual(result['status'], 'COMPARABLE')
            self.assertEqual(result['mean_delta'], expected)
            self.assertEqual(result['mean_improvement'], -expected)
            if len(candidate) == 2:
                self.assertEqual(result['standard_error'], 1)
            self.assertEqual([c, b], before)

    def test_integer_low_bits_preserve_centered_uncertainty(self):
        result = compare_paired_metrics(points([10**16 + 1, 10**16], True), points([0, 0]),
                                        dict(SAMPLING, independent=True))
        self.assertEqual(result['status'], 'COMPARABLE')
        self.assertEqual(result['standard_error'], .5)
        self.assertEqual(result['uncertainty_status'], 'ESTIMATED_UNDER_CALLER_IID_PREMISE')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_cancellation_permutations_keep_exact_mean_and_direction(self):
        values = [1e16, 1.0, -1e16]
        expected = float(sum(map(Fraction, values)) / len(values))
        for ordered in permutations(values):
            for direction in ('minimize', 'maximize'):
                c, b = points(list(ordered), True), points([0.0] * 3)
                c['identity']['direction'] = b['identity']['direction'] = direction
                original = deepcopy([c, b])
                result = compare_paired_metrics(c, b, dict(SAMPLING, independent=True))
                self.assertEqual(result['status'], 'COMPARABLE')
                self.assertEqual(result['mean_delta'], expected)
                self.assertEqual(result['mean_improvement'], -expected if direction == 'minimize' else expected)
                self.assertAlmostEqual(result['standard_error'] / 1e16, (1 / 3) ** 0.5)
                self.assertEqual(result['uncertainty_status'], 'ESTIMATED_UNDER_CALLER_IID_PREMISE')
                self.assertEqual(result['scientific_support'], 'UNKNOWN')
                self.assertEqual([c, b], original)

    def test_finite_values_with_unrepresentable_total_remain_unknown(self):
        result = compare_paired_metrics(points([1e308, 1e308], True), points([0.0, 0.0]), SAMPLING)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['reason'], 'UNREPRESENTABLE_DIFFERENCE')
        self.assertIsNone(result['mean_delta'])
        self.assertIsNone(result['standard_error'])

    def test_integer_low_bits_survive_exact_and_mixed_cancellation(self):
        for values in ([10 ** 16 + 1, -10 ** 16],
                       [10 ** 16 + 1, -10 ** 16, 0.0],
                       [10 ** 16 + 1, -1e16]):
            expected = float(sum(map(Fraction, values)) / len(values))
            for ordered in permutations(values):
                for direction in ('minimize', 'maximize'):
                    c, b = points(list(ordered), True), points([0] * len(values))
                    c['identity']['direction'] = b['identity']['direction'] = direction
                    result = compare_paired_metrics(c, b, SAMPLING)
                    self.assertEqual(result['status'], 'COMPARABLE')
                    self.assertEqual(result['mean_delta'], expected)
                    self.assertEqual(result['mean_improvement'], -expected if direction == 'minimize' else expected)
                    self.assertIsNone(result['standard_error'])
                    self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_descriptive_pairs_and_iid_standard_error_are_distinct(self):
        result = compare_paired_metrics(points([0, 2], True), points([1, 1]), SAMPLING)
        self.assertEqual(result['status'], 'COMPARABLE')
        self.assertEqual(result['mean_delta'], 0)
        self.assertEqual(result['mean_improvement'], 0)
        self.assertIsNone(result['standard_error'])
        self.assertEqual(result['uncertainty_status'], 'UNKNOWN')
        result = compare_paired_metrics(points([0, 2], True), points([1, 1]), dict(SAMPLING, independent=True))
        self.assertEqual(result['standard_error'], 1)
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_duplicate_misaligned_or_protocol_mismatched_pairs_stay_unknown(self):
        for field, value, reason in [('sample_ids', ['s0', 's0'], 'DUPLICATE_SAMPLE_IDS'),
                                     ('sample_ids', ['s1', 's0'], 'PAIRED_SAMPLE_ORDER_MISMATCH')]:
            candidate = points([0, 2], True)
            candidate[field] = value
            result = compare_paired_metrics(candidate, points([1, 1]), SAMPLING)
            self.assertEqual(result['reason'], reason)
            self.assertIsNone(result['mean_delta'])
        for field in ('data_split', 'evaluator_sha256', 'reduction', 'direction'):
            candidate = points([0, 2], True)
            candidate['identity'][field] = 'e' * 64 if field.endswith('sha256') else 'other'
            result = compare_paired_metrics(candidate, points([1, 1]), SAMPLING)
            self.assertEqual(result['status'], 'UNKNOWN')

    def test_no_invented_unit_independence_or_singleton_uncertainty(self):
        for sampling in ({}, dict(SAMPLING, independent='true'), dict(SAMPLING, unit='')):
            self.assertEqual(compare_paired_metrics(points([0], True), points([1]), sampling)['status'], 'UNKNOWN')
        result = compare_paired_metrics(points([0], True), points([1]), dict(SAMPLING, independent=True))
        self.assertEqual(result['uncertainty_reason'], 'INSUFFICIENT_INDEPENDENT_UNITS')
        self.assertIsNone(result['standard_error'])

    def test_representable_tiny_and_large_uncertainty_cannot_become_zero_or_overflow(self):
        for scale in (1e-200, 1e200):
            result = compare_paired_metrics(points([scale, -scale], True), points([0.0, 0.0]),
                                            dict(SAMPLING, independent=True))
            self.assertAlmostEqual(result['standard_error'] / scale, 1)
            self.assertEqual(result['uncertainty_status'], 'ESTIMATED_UNDER_CALLER_IID_PREMISE')
        result = compare_paired_metrics(points([5e-324, -5e-324], True), points([0.0, 0.0]),
                                        dict(SAMPLING, independent=True))
        self.assertGreater(result['standard_error'], 0)

    def test_malformed_oversized_nonfinite_and_overflow_inputs(self):
        for values in ([], [True], [float('nan')], [float('inf')], [0] * 2049):
            with self.assertRaises(ValueError):
                compare_paired_metrics(points(values, True), points(values), SAMPLING)
        result = compare_paired_metrics(points([1e308], True), points([-1e308]), SAMPLING)
        self.assertEqual(result['reason'], 'UNREPRESENTABLE_DIFFERENCE')
        self.assertIsNone(result['mean_delta'])

    def test_input_objects_are_not_mutated_and_direction_reverses(self):
        c, b = points([1, 3], True), points([3, 5])
        original = deepcopy([c, b, SAMPLING])
        self.assertEqual(compare_paired_metrics(c, b, SAMPLING)['mean_improvement'], 2)
        self.assertEqual([c, b, SAMPLING], original)
        c['identity']['direction'] = b['identity']['direction'] = 'maximize'
        self.assertEqual(compare_paired_metrics(c, b, SAMPLING)['mean_improvement'], -2)


class ResidualMethodTests(unittest.TestCase):
    def test_large_all_integer_tolerances_keep_exact_thresholds_and_extraction(self):
        code, _ = extract_function((ROOT / 'scripts/rds_result_tools.py').read_bytes(), 'check_residuals')
        namespace = {}
        exec(compile(code, '<extracted-integer-tolerance>', 'exec'), namespace)
        for atol, rtol, reference in ((10**400, 0, [0]), (0, 10**400, [1])):
            candidate, baseline = points([2], True), points(reference)
            domain = dict(DOMAIN, atol=atol, rtol=rtol)
            before = deepcopy([candidate, baseline, domain])
            residual = abs(Fraction(2) - Fraction(reference[0]))
            threshold = Fraction(atol) + Fraction(rtol) * abs(Fraction(reference[0]))
            for operation in (check_residuals, namespace['check_residuals']):
                result = operation(candidate, baseline, domain)
                self.assertEqual(result['status'], 'OBSERVED')
                self.assertEqual(result['within_tolerance'], residual <= threshold)
                self.assertEqual(result['max_abs_residual'], residual)
                self.assertEqual(result['scientific_support'], 'UNKNOWN')
            self.assertEqual([candidate, baseline, domain], before)
        for domain in (dict(DOMAIN, atol=1e308, rtol=1e308),
                       dict(DOMAIN, atol=10**400, rtol=0)):
            for operation in (check_residuals, namespace['check_residuals']):
                result = operation(points([1.], True), points([1.]), domain)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertEqual(result['reason'], 'UNREPRESENTABLE_RESIDUAL')

    def test_mixed_original_residual_and_tolerance_boundary_are_exact(self):
        c, r = points([10**16 + 1], True), points([1e16])
        before = deepcopy([c, r])
        for atol, expected in ((0, False), (.5, False), (1, True)):
            result = check_residuals(c, r, dict(DOMAIN, atol=atol))
            self.assertEqual(result['status'], 'OBSERVED')
            self.assertEqual(result['max_abs_residual'], 1)
            self.assertEqual(result['within_tolerance'], expected)
        # 1 + (2**53+1) is exactly 2**53+2, not rounded 2**53.
        result = check_residuals(points([2**53 + 2], True), points([2**53 + 1]),
                                 dict(DOMAIN, atol=0., rtol=1 / (2**53 + 1)))
        exact_tolerance = Fraction(1 / (2**53 + 1)) * (2**53 + 1)
        self.assertEqual(result['within_tolerance'], Fraction(1) <= exact_tolerance)
        self.assertEqual([c, r], before)

    def test_mixed_perfect_representable_tiny_and_unrepresentable_residuals(self):
        for values, reference in (([2**54, 0], [float(2**54), 0.]), ([0., 2], [0, 2.])):
            result = check_residuals(points(values, True), points(reference), dict(DOMAIN, atol=0))
            self.assertTrue(result['within_tolerance'])
            self.assertEqual(result['max_abs_residual'], 0)
        result = check_residuals(points([5e-324], True), points([0]), dict(DOMAIN, atol=0))
        self.assertEqual(result['max_abs_residual'], 5e-324)
        self.assertFalse(result['within_tolerance'])
        result = check_residuals(points([1e308], True), points([-1e308]), DOMAIN)
        self.assertEqual(result['reason'], 'UNREPRESENTABLE_RESIDUAL')

    def test_exact_boundary_and_original_worst_pointer(self):
        result = check_residuals(points([0, 1.25, 2.5], True), points([0, 1, 2]), DOMAIN)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['max_abs_residual'], 0.5)
        self.assertEqual(result['violations'], 1)
        self.assertFalse(result['within_tolerance'])
        self.assertEqual(result['worst_pointer'], '/values/2')
        result = check_residuals(points([0, 1.25], True), points([0, 1]), DOMAIN)
        self.assertTrue(result['within_tolerance'])
        self.assertEqual(result['assurance'], 'FINITE_NUMERICAL_OBSERVATION')

    def test_missing_reference_precision_and_bad_tolerance_stay_unknown(self):
        for domain in ({}, dict(DOMAIN, independent_reference=False), dict(DOMAIN, precision=''),
                       dict(DOMAIN, atol=-1), dict(DOMAIN, rtol=True)):
            result = check_residuals(points([1], True), points([1]), domain)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertIsNone(result['within_tolerance'])
        result = check_residuals(points([1e308], True), points([-1e308]), DOMAIN)
        self.assertEqual(result['reason'], 'UNREPRESENTABLE_RESIDUAL')

    def test_extracted_methods_reach_independent_oracles(self):
        source = (ROOT / 'scripts/rds_result_tools.py').read_bytes()
        for entry, args, key, expected in [
            ('compare_paired_metrics', [points([1, 3], True), points([3, 5]), SAMPLING], 'mean_improvement', 2),
            ('check_residuals', [points([0, 1.25], True), points([0, 1]), DOMAIN], 'within_tolerance', True)]:
            code, included = extract_function(source, entry)
            namespace = {}
            exec(compile(code, '<extracted-method>', 'exec'), namespace)
            self.assertEqual(namespace[entry](*args)[key], expected)
            self.assertIn('_paired_points', included)
        code, _ = extract_function(source, 'compare_paired_metrics')
        namespace = {}
        exec(compile(code, '<extracted-cancellation>', 'exec'), namespace)
        result = namespace['compare_paired_metrics'](points([1e16, 1.0, -1e16], True),
                                                    points([0.0] * 3), SAMPLING)
        self.assertEqual(result['mean_delta'], 1 / 3)


if __name__ == '__main__':
    unittest.main()
