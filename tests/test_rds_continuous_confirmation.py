"""Finite floating-point replay, strict evidence boundaries and original receipts."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_continuous_confirmation import (KIND, MAX_ROWS, SafeExpression, check_output,
                                         evaluate, metrics, validate_claim)
from rds_domain_confirmation import inspect_confirmation, validate_policy
from rds_project import ProjectStore, digest

CLAIM = {'schema': 1, 'kind': KIND, 'max_nrmse': .1, 'min_r2': .99}
INPUTS = {'schema': 1, 'variables': ['x0'], 'rows': [
    {'id': 'r' + str(i), 'values': [x]} for i, x in enumerate([-1., 0., 1., 2.])]}
LABELS = {'schema': 1, 'rows': [
    {'id': row['id'], 'target': 2 * row['values'][0] + 1} for row in INPUTS['rows']]}
SHA = 'a' * 64


def output(expression='2*x0+1'):
    return {'schema': 1, 'status': 'candidate', 'inputs_sha256': SHA,
            'row_ids': [row['id'] for row in INPUTS['rows']], 'expression': expression}


class ContinuousArithmeticTests(unittest.TestCase):
    def test_replays_predictions_and_recomputes_metrics(self):
        replay = evaluate('2*x0+1', INPUTS)
        self.assertEqual(replay['predictions'], [-1., 1., 3., 5.])
        result = check_output(CLAIM, INPUTS, LABELS, output(), SHA)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['metrics'], {'nrmse': 0., 'r2': 1., 'rmse': 0.})
        self.assertEqual(result['row_count'], 4)

    def test_wrong_finite_expression_is_fail(self):
        result = check_output(CLAIM, INPUTS, LABELS, output('2*x0'), SHA)
        self.assertEqual(result['status'], 'FAIL')
        self.assertAlmostEqual(result['metrics']['nrmse'], 1 / math.sqrt(5))
        self.assertAlmostEqual(result['metrics']['r2'], .8)
        self.assertAlmostEqual(result['metrics']['rmse'], 1)

    def test_both_declared_thresholds_are_enforced(self):
        candidate = output('2*x0')
        for claim in ({**CLAIM, 'max_nrmse': 1}, {**CLAIM, 'min_r2': 0}):
            self.assertEqual(check_output(claim, INPUTS, LABELS, candidate, SHA)['status'], 'FAIL')
        claim = {**CLAIM, 'max_nrmse': 1, 'min_r2': 0}
        self.assertEqual(check_output(claim, INPUTS, LABELS, candidate, SHA)['status'], 'PASS')

    def test_abstention_cannot_become_pass(self):
        candidate = output()
        candidate.pop('expression')
        candidate['status'] = 'abstain'
        result = check_output(CLAIM, INPUTS, LABELS, candidate, SHA)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIsNone(result['metrics'])
        candidate['expression'] = '2*x0+1'
        with self.assertRaises(ValueError):
            check_output(CLAIM, INPUTS, LABELS, candidate, SHA)

    def test_self_certified_metrics_and_predictions_are_not_evidence(self):
        for field, value in [('metrics', {'r2': 1, 'nrmse': 0}), ('verdict', 'PASS'),
                             ('predictions', [-1, 1, 3, 5]), ('scientific_support', 'PROVEN')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_output(CLAIM, INPUTS, LABELS, {**output(), field: value}, SHA)
        with self.assertRaises(ValueError):
            check_output(CLAIM, INPUTS, LABELS, {'status': 'PASS'}, SHA)

    def test_candidate_identity_and_full_ordered_coverage(self):
        for key, value in [('inputs_sha256', 'b' * 64), ('schema', True), ('status', 'unknown'),
                           ('status', []), ('row_ids', ['r0', 'r1', 'r2']),
                           ('row_ids', ['r0', 'r1', 'r2', 'r2']),
                           ('row_ids', ['r3', 'r2', 'r1', 'r0'])]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                check_output(CLAIM, INPUTS, LABELS, {**output(), key: value}, SHA)

    def test_frozen_feature_and_label_ids_cannot_duplicate_or_reorder(self):
        for which in ('features', 'labels'):
            inputs, labels = deepcopy(INPUTS), deepcopy(LABELS)
            data = inputs if which == 'features' else labels
            data['rows'][1]['id'] = data['rows'][0]['id']
            with self.subTest(which=which), self.assertRaisesRegex(ValueError, 'Duplicate'):
                check_output(CLAIM, inputs, labels, output(), SHA)
        labels = deepcopy(LABELS)
        labels['rows'].reverse()
        with self.assertRaisesRegex(ValueError, 'identity or ordering'):
            check_output(CLAIM, INPUTS, labels, output(), SHA)

    def test_labels_are_not_expression_variables_or_feature_fields(self):
        for expression in ('target', 'y', 'labels[0]', 'row.id', 'x1'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                check_output(CLAIM, INPUTS, LABELS, output(expression), SHA)
        inputs = deepcopy(INPUTS)
        inputs['rows'][0]['target'] = LABELS['rows'][0]['target']
        with self.assertRaisesRegex(ValueError, 'row schema'):
            check_output(CLAIM, inputs, LABELS, output(), SHA)
        inputs = deepcopy(INPUTS)
        inputs['variables'] = ['target']
        with self.assertRaises(ValueError):
            check_output(CLAIM, inputs, LABELS, output(), SHA)

    def test_disallowed_syntax_is_never_executed(self):
        for expression in ("__import__('os').system('echo bad')", 'x0.__class__', '[x0][0]',
                           '(lambda: 1)()', 'sum([x0])', 'x0 if x0 else 1', 'x0 < 1',
                           'True', '1j', 'x0//2', 'x0%2', 'sin(x0, 1)', 'sin(x=x0)',
                           'x0 ** x0', 'x0**13', 'x0**-13', 'nan', 'inf', '1e309'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                SafeExpression(expression, 1)

    def test_supported_functions_constants_and_literal_powers(self):
        expression = 'sin(pi/2)+cos(0)+sqrt(abs(x0))+log(e)+exp(0)+x0**-2'
        self.assertAlmostEqual(SafeExpression(expression, 1).at([4.]), 6.0625)
        self.assertEqual(SafeExpression('-x0**2 + +x0', 1).at([2.]), -2.)

    def test_undefined_and_overflow_fail_closed(self):
        for expression in ('1/x0', 'log(x0)', 'sqrt(x0)', '(-1)**0.5', 'exp(301)',
                           '1e150*1e150', '1e100**12', 'x0**-1'):
            with self.subTest(expression=expression), self.assertRaisesRegex(ValueError, 'Undefined'):
                check_output(CLAIM, INPUTS, LABELS, output(expression), SHA)

    def test_shape_numeric_and_threshold_types(self):
        for bad_value in (True, '1', None, float('nan'), float('inf'), -float('inf'), 10 ** 500):
            inputs, labels = deepcopy(INPUTS), deepcopy(LABELS)
            inputs['rows'][0]['values'][0] = bad_value
            labels['rows'][0]['target'] = bad_value
            with self.subTest(value=repr(bad_value)[:40]):
                with self.assertRaises(ValueError):
                    check_output(CLAIM, inputs, LABELS, output(), SHA)
                with self.assertRaises(ValueError):
                    check_output(CLAIM, INPUTS, labels, output(), SHA)
                with self.assertRaises(ValueError):
                    validate_claim({**CLAIM, 'max_nrmse': bad_value})
        for key, value in [('schema', True), ('kind', 'symbolic_truth'), ('max_nrmse', -1), ('min_r2', 2)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_claim({**CLAIM, key: value})
        inputs = deepcopy(INPUTS)
        inputs['rows'][0]['values'].append(0)
        with self.assertRaises(ValueError):
            check_output(CLAIM, inputs, LABELS, output(), SHA)

    def test_constant_targets_and_metric_overflow_are_unknown(self):
        for target in (0., 3.):
            labels = deepcopy(LABELS)
            for row in labels['rows']:
                row['target'] = target
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Constant'):
                check_output(CLAIM, INPUTS, labels, output(), SHA)
        with self.assertRaises(ValueError):
            metrics([-1e-300, 1e-300], [1e150, 1e150])
        self.assertEqual(metrics([-1e149, 1e149], [0., 0.])['nrmse'], 1)

    def test_tiny_residual_cannot_underflow_into_perfect_fit(self):
        measured = metrics([-1., 1e-200], [-1., 0.])
        self.assertGreater(measured['nrmse'], 0.)
        self.assertGreater(measured['rmse'], 0.)
        inputs = {'schema': 1, 'variables': ['x0'], 'rows': [
            {'id': 'r0', 'values': [-1.]}, {'id': 'r1', 'values': [0.]}]}
        labels = {'schema': 1, 'rows': [
            {'id': 'r0', 'target': -1.}, {'id': 'r1', 'target': 1e-200}]}
        candidate = {**output('x0'), 'row_ids': ['r0', 'r1']}
        exact_gate = {**CLAIM, 'max_nrmse': 0., 'min_r2': 1.}
        self.assertEqual(check_output(exact_gate, inputs, labels, candidate, SHA)['status'], 'FAIL')
        with self.assertRaisesRegex(ValueError, 'underflow'):
            metrics([-1e150, 1e-200], [-1e150, 0.])

    def test_strict_r2_one_refuses_rounded_perfect_fit_with_nonzero_residual(self):
        inputs = {'schema': 1, 'variables': ['x0'], 'rows': [
            {'id': 'r0', 'values': [-1.]}, {'id': 'r1', 'values': [0.]}]}
        candidate = {**output('x0'), 'row_ids': ['r0', 'r1']}
        claim = {**CLAIM, 'max_nrmse': 1e-7, 'min_r2': 1.}
        for residual in (1e-9, 1e-200):
            labels = {'schema': 1, 'rows': [
                {'id': 'r0', 'target': -1.}, {'id': 'r1', 'target': residual}]}
            result = check_output(claim, inputs, labels, candidate, SHA)
            self.assertEqual(result['metrics']['r2'], 1.)  # Display can round; admission cannot.
            self.assertGreater(result['metrics']['nrmse'], 0.)
            self.assertEqual(result['status'], 'FAIL')
            relaxed = {**claim, 'min_r2': .9999999999999999}
            self.assertEqual(check_output(relaxed, inputs, labels, candidate, SHA)['status'], 'PASS')
        exact_labels = {'schema': 1, 'rows': [
            {'id': row['id'], 'target': row['values'][0]} for row in inputs['rows']]}
        self.assertEqual(check_output(claim, inputs, exact_labels, candidate, SHA)['status'], 'PASS')

    def test_r2_gate_preserves_zero_and_negative_threshold_boundaries(self):
        labels = {'schema': 1, 'rows': [
            {'id': row['id'], 'target': row['values'][0]} for row in INPUTS['rows']]}
        candidate = output('0')
        # This target has a nonzero mean, so predicting zero yields negative R2.
        measured = check_output({**CLAIM, 'max_nrmse': 2, 'min_r2': -1}, INPUTS, labels, candidate, SHA)
        self.assertEqual(measured['status'], 'PASS')
        self.assertLess(measured['metrics']['r2'], 0)
        self.assertEqual(check_output({**CLAIM, 'max_nrmse': 2, 'min_r2': 0},
                                     INPUTS, labels, candidate, SHA)['status'], 'FAIL')

    def test_size_and_ast_complexity_limits(self):
        for expression in ('1' * 4097, '-(' * 20 + '1' + ')' * 20,
                           '+'.join('x0' for _ in range(80))):
            with self.subTest(expression=expression[:30]), self.assertRaises(ValueError):
                SafeExpression(expression, 1)
        payload = {**output(), 'padding': 'x' * (1024 * 1024)}
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            check_output(CLAIM, INPUTS, LABELS, payload, SHA)
        inputs = deepcopy(INPUTS)
        inputs['rows'] = inputs['rows'][:1] * (MAX_ROWS + 1)
        with self.assertRaisesRegex(ValueError, 'row count'):
            evaluate('x0', inputs)


EVALUATOR = '''import hashlib,json,pathlib,sqlite3
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt=json.loads(db.execute("SELECT body FROM receipts WHERE run_id='candidate'").fetchone()[0])
sha=lambda p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
answer={'candidate_receipt_sha256':receipt['sha256'],'claim_sha256':sha('claim.json'),
        'evaluator_sha256':sha('evaluator.py'),'inputs_sha256':sha('data.json'),
        'labels_sha256':LABEL_SHA,'verdict':VERDICT}
EXTRA
pathlib.Path('outputs/confirmation.json').write_text(json.dumps(answer),encoding='utf-8')
'''


class ContinuousOwnedReceiptTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('_continuous_domain_harness', ROOT / 'tests/test_rds_domain_confirmation.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.harness = module.DomainConfirmationTests('test_pending_has_no_promoted_verdict')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def build(self, *, expression='2*x0+1', verdict='PASS', abstain=False, malformed=None,
              wrong_label_hash=False, evaluator_metrics=False, evaluator_replay=False, constant=False, nested_labels=False):
        h = self.harness
        writer = h.write
        labels = deepcopy(LABELS)
        if constant:
            for row in labels['rows']:
                row['target'] = 0
        writer('labels.json', '[' * 10000 + '0' + ']' * 10000 if nested_labels else labels)
        def write(path, value):
            if path == 'claim.json':
                value = CLAIM
            elif path == 'data.json':
                value = INPUTS
            elif path == 'candidate-input.json':
                value = output(expression)
                value['inputs_sha256'] = hashlib.sha256((h.root / 'data.json').read_bytes()).hexdigest()
                if abstain:
                    value.pop('expression')
                    value['status'] = 'abstain'
                if malformed == 'duplicate':
                    value = json.dumps(value).replace('"schema": 1', '"schema": 1, "schema": 1')
                elif malformed == 'nan':
                    value = json.dumps(value).replace(json.dumps(expression), 'NaN')
                elif malformed == 'metrics':
                    value['metrics'] = {'r2': 1, 'nrmse': 0}
                elif malformed == 'nested':
                    value = '[' * 10000 + '0' + ']' * 10000
                elif malformed == 'status_list':
                    value['status'] = []
                elif malformed == 'expression_object':
                    value['expression'] = {'code': '2*x0+1'}
            elif path == 'evaluator.py':
                label_hash = repr('b' * 64) if wrong_label_hash else "sha('labels.json')"
                extra = "answer['metrics']={'r2':1,'nrmse':0}" if evaluator_metrics else ''
                if evaluator_replay:
                    extra = "answer['replay']={'status':'PASS'}"
                value = EVALUATOR.replace('LABEL_SHA', label_hash).replace('VERDICT', repr(verdict)).replace('EXTRA', extra)
            writer(path, value)
        h.write = write
        original = ProjectStore.initialize
        def initialize(store, contract, **kwargs):
            contract = deepcopy(contract)
            sha = lambda p: hashlib.sha256((h.root / p).read_bytes()).hexdigest()
            contract['bindings'].append({'path': 'labels.json', 'role': 'data', 'sha256': sha('labels.json')})
            policy = contract['advisor_policy']
            policy['confirmation'].update(domain='continuous', scope='Frozen finite numeric fit only; no held-out or symbolic claim')
            policy['confirmation']['rules']['kind'] = KIND
            policy['confirmation']['data'].append({'path': 'labels.json', 'sha256': sha('labels.json')})
            protocol = json.loads((h.root / 'protocol.json').read_text())
            protocol['data_sha256'] = ProjectStore._role_sha(contract, 'data')
            writer('protocol.json', protocol)
            for binding in contract['bindings']:
                if binding['role'] == 'protocol':
                    binding['sha256'] = sha('protocol.json')
            for route in policy['routes']:
                manifest = route['manifest']
                manifest['protocol']['sha256'] = sha('protocol.json')
                h.manifests[manifest['id']] = manifest
            return original(store, contract, **kwargs)
        with patch.object(ProjectStore, 'initialize', new=initialize):
            h.build()
        return h

    def test_original_receipts_confirm_finite_fit_and_preserve_limits(self):
        h = self.build()
        result = h.run_all()
        self.assertEqual(result['task_confirmation'], 'PASS', result)
        self.assertEqual(result['assurance'], 'RECOMPUTED_FINITE_FLOAT_EXPRESSION')
        self.assertEqual(result['finite_evaluation']['metrics']['r2'], 1)
        self.assertEqual(result['confirmation_independence'], 'DECLARED_EXPOSED')
        for field in ('scientific_support', 'symbolic_identity', 'causal_support', 'generalization'):
            self.assertEqual(result[field], 'UNKNOWN')
        before = h.store.snapshot()
        h.store.recover('confirmation')
        self.assertEqual(h.store.snapshot()['budget'], before['budget'])
        self.assertEqual(len(h.store.snapshot()['receipts']), 2)

    def test_wrong_formula_fails_despite_successful_processes(self):
        result = self.build(expression='2*x0', verdict='FAIL').run_all()
        self.assertEqual(result['task_confirmation'], 'FAIL', result)
        self.assertEqual(result['execution'], {'candidate': 'SUCCEEDED', 'confirmation': 'SUCCEEDED'})

    def test_dishonest_frozen_evaluator_cannot_override_replay(self):
        result = self.build(expression='2*x0', verdict='PASS').run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('contradicts replay', ' '.join(result['reasons']))
        self.assertEqual(result['finite_evaluation']['status'], 'FAIL')

    def test_abstention_is_unknown(self):
        result = self.build(abstain=True, verdict='UNKNOWN').run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('Candidate abstained', result['reasons'])

    def test_malformed_originals_fail_closed_after_real_execution(self):
        for malformed in ('duplicate', 'nan', 'metrics'):
            with self.subTest(malformed=malformed):
                # Each real execution uses a fresh retained ledger.
                case = ContinuousOwnedReceiptTests('test_abstention_is_unknown')
                case.setUp()
                try:
                    result = case.build(malformed=malformed).run_all()
                    self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
                    self.assertEqual(result['execution']['candidate'], 'SUCCEEDED')
                finally:
                    case.doCleanups()

    def test_deep_original_candidate_json_is_unknown(self):
        result = self.build(malformed='nested').run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertEqual(result['execution']['candidate'], 'SUCCEEDED')
        self.assertTrue(result['reasons'])

    def test_deep_frozen_label_json_is_unknown(self):
        result = self.build(nested_labels=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertEqual(result['execution']['confirmation'], 'SUCCEEDED')
        self.assertTrue(result['reasons'])

    def test_unhashable_original_status_is_unknown(self):
        result = self.build(malformed='status_list').run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('candidate status', ' '.join(result['reasons']))

    def test_non_string_original_expression_is_unknown(self):
        result = self.build(malformed='expression_object').run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('expression', ' '.join(result['reasons']))

    def test_undefined_expression_is_unknown(self):
        result = self.build(expression='1/x0').run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('Undefined', ' '.join(result['reasons']))

    def test_overflow_expression_is_unknown(self):
        result = self.build(expression='1e150*1e150').run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)

    def test_constant_targets_are_unknown(self):
        result = self.build(constant=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('Constant', ' '.join(result['reasons']))

    def test_wrong_label_identity_is_unknown(self):
        result = self.build(wrong_label_hash=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('input identity', ' '.join(result['reasons']))

    def test_evaluator_metric_claim_is_not_accepted(self):
        result = self.build(evaluator_metrics=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)

    def test_evaluator_replay_field_is_outside_the_exact_envelope(self):
        result = self.build(evaluator_replay=True).run_all()
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('schema differs', ' '.join(result['reasons']))

    def test_tampered_original_candidate_cannot_be_replaced(self):
        h = self.build()
        h.run_all()
        h.write('outputs/candidate.json', output('2*x0'))
        result = inspect_confirmation(h.store, h.contract)
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)

    def test_tampered_labels_cannot_be_replaced(self):
        h = self.build()
        h.run_all()
        h.write('labels.json', {'schema': 1, 'rows': []})
        result = inspect_confirmation(h.store, h.contract)
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('input changed', ' '.join(result['reasons']))

    def test_forged_receipt_is_not_retained_original(self):
        h = self.build()
        h.run_all()
        state = h.store.snapshot()
        receipt = state['receipts'][0]
        receipt['worker_pid'] += 1
        receipt['sha256'] = digest({key: value for key, value in receipt.items() if key != 'sha256'})
        result = inspect_confirmation(h.store, h.contract, state)
        self.assertEqual(result['task_confirmation'], 'UNKNOWN', result)
        self.assertIn('retained original', ' '.join(result['reasons']))

    def test_features_and_labels_require_distinct_frozen_refs(self):
        h = self.build()
        contract = deepcopy(h.contract)
        refs = contract['advisor_policy']['confirmation']['data']
        refs[1] = deepcopy(refs[0])
        with self.assertRaises(ValueError):
            validate_policy(h.store, contract, contract['advisor_policy'])


if __name__ == '__main__':
    unittest.main()
