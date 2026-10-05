"""Exact arithmetic boundaries and real owned candidate/evaluator receipts."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_domain_confirmation import inspect_confirmation
from rds_project import ProjectStore
from rds_polynomial_confirmation import KIND, check_output, evaluate

CLAIM = {'schema': 1, 'kind': KIND, 'quantities': ['values', 'sparse_jacobian']}
DATA = {'schema': 1, 'variables': ['x', 'y'], 'polynomials': [
    {'id': 'F0', 'terms': [{'coefficient': '3/2', 'powers': [[0, 2]]},
                          {'coefficient': '-1', 'powers': [[0, 1], [1, 1]]},
                          {'coefficient': '2', 'powers': []}]},
    {'id': 'F1', 'terms': [{'coefficient': '1', 'powers': [[1, 2]]}]}],
    'points': [{'id': 'zero', 'coordinates': ['0', '2']},
               {'id': 'rational', 'coordinates': ['1/2', '-1/3']}]}
EXPECTED = {'point_ids': ['zero', 'rational'], 'polynomial_ids': ['F0', 'F1'],
            'values': [['2', '4'], ['61/24', '1/9']],
            'jacobian': [[[0, 0, '-2'], [0, 1, '0'], [1, 1, '4']],
                         [[0, 0, '11/6'], [0, 1, '-1/2'], [1, 1, '-2/3']]]}
SHA = 'a' * 64


def output():
    return {'schema': 1, 'inputs_sha256': SHA, **deepcopy(EXPECTED)}


class PolynomialArithmeticTests(unittest.TestCase):
    def test_exact_values_and_all_structural_derivatives(self):
        self.assertEqual(evaluate(CLAIM, DATA), EXPECTED)
        self.assertEqual(check_output(CLAIM, DATA, output(), SHA)['status'], 'PASS')

    def test_values_only_profile(self):
        claim = {**CLAIM, 'quantities': ['values']}
        wanted = {k: v for k, v in EXPECTED.items() if k != 'jacobian'}
        self.assertEqual(evaluate(claim, DATA), wanted)
        self.assertEqual(check_output(claim, DATA, {'schema': 1, 'inputs_sha256': SHA, **wanted}, SHA)['status'], 'PASS')

    def test_wrong_exact_value_has_located_counterexample(self):
        candidate = output()
        candidate['values'][1][0] = '5/2'
        result = check_output(CLAIM, DATA, candidate, SHA)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['counterexample'], {'quantity': 'values', 'point_id': 'rational',
                          'polynomial_id': 'F0', 'expected': '61/24', 'actual': '5/2'})

    def test_wrong_derivative_is_fail(self):
        candidate = output()
        candidate['jacobian'][0][0][2] = '0'
        result = check_output(CLAIM, DATA, candidate, SHA)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['counterexample']['variable'], 'x')

    def test_structural_derivative_cannot_be_omitted_at_zero(self):
        candidate = output()
        candidate['jacobian'][0].pop(1)
        with self.assertRaisesRegex(ValueError, 'structural coverage'):
            check_output(CLAIM, DATA, candidate, SHA)

    def test_jacobian_coordinates_are_ordered_and_strictly_typed(self):
        for replacement in ([True, 0, '-2'], [0.0, 0, '-2'], [0, 1, '-2']):
            with self.subTest(replacement=replacement):
                candidate = output()
                candidate['jacobian'][0][0] = replacement
                with self.assertRaisesRegex(ValueError, 'coordinates or ordering'):
                    check_output(CLAIM, DATA, candidate, SHA)

    def test_rational_format_and_self_success_are_not_evidence(self):
        for wrong in ('0/1', '2/4', '1.0', '1e3', '-0', '__import__("os")', 2, True):
            with self.subTest(wrong=wrong):
                candidate = output()
                candidate['values'][0][0] = wrong
                with self.assertRaises(ValueError):
                    check_output(CLAIM, DATA, candidate, SHA)
        with self.assertRaises(ValueError):
            check_output(CLAIM, DATA, {'status': 'PASS'}, SHA)

    def test_identity_and_complete_equation_coverage(self):
        for key, value in [('inputs_sha256', 'b' * 64), ('point_ids', ['rational', 'zero']),
                           ('polynomial_ids', ['F1', 'F0']), ('schema', True)]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    check_output(CLAIM, DATA, {**output(), key: value}, SHA)
        candidate = output()
        candidate['values'][0].pop()
        with self.assertRaisesRegex(ValueError, 'equation coverage'):
            check_output(CLAIM, DATA, candidate, SHA)

    def test_data_duplicate_degree_and_type_limits(self):
        variants = []
        for powers in ([[True, 1]], [[0, 1.0]], [[0, 3]], [[0, 1], [0, 1]], [[1, 1], [0, 1]]):
            bad = deepcopy(DATA)
            bad['polynomials'][0]['terms'][0]['powers'] = powers
            variants.append(bad)
        bad = deepcopy(DATA)
        bad['polynomials'][0]['terms'].append(deepcopy(bad['polynomials'][0]['terms'][0]))
        variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evaluate(CLAIM, bad)

    def test_ninety_digit_rational_certificate_coordinates(self):
        data = deepcopy(DATA)
        x = Fraction(10 ** 90 + 7, 10 ** 90 + 9)
        data['points'] = [{'id': 'Qx', 'coordinates': [str(x), '0']}]
        expected = evaluate(CLAIM, data)
        self.assertEqual(expected['values'][0][0], str(Fraction(3, 2) * x * x + 2))
        self.assertEqual(expected['jacobian'][0][0][2], str(3 * x))

    def test_119_quadratics_variables_and_sparse_jacobian(self):
        n = 119
        data = {'schema': 1, 'variables': ['x' + str(i) for i in range(n)],
                'polynomials': [{'id': 'F' + str(i), 'terms': [
                    {'coefficient': '1', 'powers': [[i, 2]]},
                    {'coefficient': '3', 'powers': [[(i + 1) % n, 1]]},
                    {'coefficient': '-1/7', 'powers': []}]} for i in range(n)],
                'points': [{'id': 'integer', 'coordinates': [str(i) for i in range(n)]}]}
        result = evaluate(CLAIM, data)
        self.assertEqual(result['values'][0], [str(Fraction(i * i + 3 * ((i + 1) % n)) - Fraction(1, 7)) for i in range(n)])
        self.assertEqual(len(result['jacobian'][0]), 238)
        self.assertIn([0, 0, '0'], result['jacobian'][0])

    def test_input_and_intermediate_bit_bounds(self):
        data = deepcopy(DATA)
        data['points'][0]['coordinates'][0] = str(2 ** 1024)
        with self.assertRaisesRegex(ValueError, 'bit bound'):
            evaluate(CLAIM, data)
        data = {'schema': 1, 'variables': ['x' + str(i) for i in range(9)],
                'polynomials': [{'id': 'sum', 'terms': [{'coefficient': '1', 'powers': [[i, 1]]} for i in range(9)]}],
                'points': [{'id': 'large', 'coordinates': ['1/' + str(2 ** 1023 - (2 * i + 1)) for i in range(9)]}]}
        with self.assertRaisesRegex(ValueError, 'bit bound'):
            evaluate(CLAIM, data)

    def test_specialized_file_and_shape_bounds(self):
        candidate = output()
        candidate['large'] = 'x' * (1024 * 1024)
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            check_output(CLAIM, DATA, candidate, SHA)
        data = deepcopy(DATA)
        data['points'] *= 5
        with self.assertRaisesRegex(ValueError, 'count bound'):
            evaluate(CLAIM, data)


EVALUATOR = '''import hashlib,json,pathlib,sqlite3
from fractions import Fraction as Q
d=json.loads(pathlib.Path('data.json').read_text())
c=json.loads(pathlib.Path('outputs/candidate.json').read_text())
expected=[]; jac=[]
for point in d['points']:
    x=[Q(v) for v in point['coordinates']]; row=[]; jrow=[]
    for pi,p in enumerate(d['polynomials']):
        total=Q(0)
        for term in p['terms']:
            v=Q(term['coefficient'])
            for index,power in term['powers']: v*=x[index]**power
            total+=v
        row.append(str(total))
        for vi in sorted({i for t in p['terms'] for i,e in t['powers']}):
            total=Q(0)
            for term in p['terms']:
                exponent=next((e for i,e in term['powers'] if i==vi),0)
                if not exponent: continue
                v=Q(term['coefficient'])*exponent
                for i,e in term['powers']: v*=x[i]**(e-(i==vi))
                total+=v
            jrow.append([pi,vi,str(total)])
    expected.append(row); jac.append(jrow)
verdict='PASS' if c.get('values')==expected and c.get('jacobian')==jac else 'FAIL'
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt=json.loads(db.execute("SELECT body FROM receipts WHERE run_id='candidate'").fetchone()[0])
sha=lambda p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
answer={'candidate_receipt_sha256':receipt['sha256'],'claim_sha256':sha('claim.json'),
        'evaluator_sha256':sha('evaluator.py'),'inputs_sha256':INPUT_SHA,'verdict':VERDICT}
pathlib.Path('outputs/confirmation.json').write_text(json.dumps(answer),encoding='utf-8')
'''


class PolynomialOwnedReceiptTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('_polynomial_domain_harness', ROOT / 'tests/test_rds_domain_confirmation.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.harness = module.DomainConfirmationTests('test_pending_has_no_promoted_verdict')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def build(self, *, wrong=False, missing=False, dishonest=False, wrong_identity=False, oversized=False):
        h = self.harness
        writer = h.write
        def write(path, value):
            if path == 'claim.json': value = CLAIM
            elif path == 'data.json': value = DATA
            elif path == 'candidate-input.json':
                value = output()
                value['inputs_sha256'] = hashlib.sha256((h.root / 'data.json').read_bytes()).hexdigest()
                if wrong: value['values'][0][0] = '3'
                if missing: value['jacobian'][0].pop(1)
                if oversized: value['large'] = 'x' * (1024 * 1024)
            elif path == 'evaluator.py':
                input_sha = repr('b' * 64) if wrong_identity else "sha('data.json')"
                verdict = repr('PASS') if dishonest else 'verdict'
                value = EVALUATOR.replace('INPUT_SHA', input_sha).replace('VERDICT', verdict)
            writer(path, value)
        h.write = write
        original = ProjectStore.initialize
        def initialize(store, contract, **kwargs):
            contract = deepcopy(contract)
            contract['advisor_policy']['confirmation']['rules']['kind'] = KIND
            return original(store, contract, **kwargs)
        with patch.object(ProjectStore, 'initialize', new=initialize):
            h.build()
        return h

    def test_real_frozen_evaluator_and_host_confirm_exact_results(self):
        h = self.build()
        result = h.run_all()
        self.assertEqual(result['task_confirmation'], 'PASS', result)
        self.assertEqual(result['assurance'], 'FINITE_EXACT_QQ_POLYNOMIAL_EVALUATION')
        for key in ('scientific_support', 'minimal_polynomial', 'global_optimality', 'disk_covering', 'root_uniqueness'):
            self.assertEqual(result[key], 'UNKNOWN')
        before = h.store.snapshot()
        recovered = h.store.recover('confirmation')
        self.assertEqual(recovered, next(r for r in before['receipts'] if r['run_id'] == 'confirmation'))
        self.assertEqual(h.store.snapshot()['budget'], before['budget'])
        self.assertEqual(len(h.store.snapshot()['runs']), 2)

    def test_wrong_exact_value_is_fail_after_successful_check_process(self):
        result = self.build(wrong=True).run_all()
        self.assertEqual(result['task_confirmation'], 'FAIL', result)
        self.assertEqual(result['execution']['confirmation'], 'SUCCEEDED')
        self.assertEqual(result['finite_arithmetic']['counterexample']['actual'], '3')

    def test_frozen_evaluator_self_pass_cannot_override_host(self):
        result = self.build(wrong=True, dishonest=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('contradicts replay', ' '.join(result['reasons']))

    def test_omitted_zero_derivative_and_wrong_input_identity_are_unknown(self):
        result = self.build(missing=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('structural coverage', ' '.join(result['reasons']))

    def test_evaluator_wrong_input_identity_is_unknown(self):
        result = self.build(wrong_identity=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('input identity', ' '.join(result['reasons']))

    def test_one_mib_original_limit_preserves_successful_receipts(self):
        h = self.build(oversized=True)
        result = h.run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('bounded JSON', ' '.join(result['reasons']))
        self.assertEqual(len(h.store.snapshot()['receipts']), 2)


if __name__ == '__main__':
    unittest.main()
