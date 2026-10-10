"""Independent finite requirement oracles; thresholds do not prove scientific safety."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_result_tools import evaluate_decision_requirements
from rds_tools import extract_function
from test_rds_result_diagnostics import diagnostic_fixture


REQUIREMENTS = {'head': 'gate', 'min_benefit_support': 4, 'min_harm_support': 4,
                'min_benefit_acceptance': .25, 'max_harm_acceptance': 0}
CHECKS = {'min_benefit_support', 'min_harm_support',
          'min_benefit_acceptance', 'max_harm_acceptance'}


class DecisionRequirementTests(unittest.TestCase):
    def evaluate(self, requirements=None, document=None, identity=None, semantics=None):
        original, original_identity, original_semantics = diagnostic_fixture()
        return evaluate_decision_requirements(
            json.dumps(original if document is None else document),
            original_identity if identity is None else identity,
            original_semantics if semantics is None else semantics,
            deepcopy(REQUIREMENTS) if requirements is None else requirements)

    def assert_outcome(self, result, status, reason=None):
        self.assertEqual(result['operation'], 'evaluate_decision_requirements')
        self.assertEqual(result['status'], status)
        self.assertIs(result['met'], {'PASS': True, 'FAIL': False, 'UNKNOWN': None}[status])
        self.assertEqual(result['reason'], reason)
        self.assertEqual(result['identity_assurance'], 'CALLER_METADATA')
        self.assertEqual(result['assurance'], 'FINITE_SAMPLE_REQUIREMENTS')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_gate_exact_oracle_pass_has_four_explicit_checks(self):
        result = self.evaluate()
        self.assert_outcome(result, 'PASS')
        self.assertEqual(result['diagnostic_status'], 'OBSERVED')
        self.assertEqual(result['diagnostic_reasons'], [])
        self.assertEqual(set(result['checks']), CHECKS)
        for field, observed in [('min_benefit_support', 4), ('min_harm_support', 4),
                                ('min_benefit_acceptance', .25), ('max_harm_acceptance', 0)]:
            self.assertEqual(result['checks'][field],
                             {'status': 'PASS', 'observed': observed, 'required': REQUIREMENTS[field]})

    def test_benefit_failure_and_other_head_harm_are_actual_failures(self):
        result = self.evaluate(dict(REQUIREMENTS, min_benefit_acceptance=.5))
        self.assert_outcome(result, 'FAIL', 'REQUIREMENTS_NOT_MET')
        self.assertEqual(result['checks']['min_benefit_acceptance']['status'], 'FAIL')
        self.assertEqual(result['checks']['max_harm_acceptance']['status'], 'PASS')
        for head, expected_benefit in [('regression', .75), ('classifier', .25)]:
            with self.subTest(head=head):
                result = self.evaluate(dict(REQUIREMENTS, head=head))
                self.assert_outcome(result, 'FAIL', 'REQUIREMENTS_NOT_MET')
                self.assertEqual(result['checks']['min_benefit_acceptance']['observed'], expected_benefit)
                self.assertEqual(result['checks']['max_harm_acceptance']['observed'], .25)
                self.assertEqual(result['checks']['max_harm_acceptance']['status'], 'FAIL')

    def test_positive_but_insufficient_support_blocks_rate_judgment(self):
        for field in ('min_benefit_support', 'min_harm_support'):
            with self.subTest(field=field):
                result = self.evaluate(dict(REQUIREMENTS, **{field: 5}))
                self.assert_outcome(result, 'UNKNOWN', 'INSUFFICIENT_CLASS_SUPPORT')
                self.assertEqual(result['diagnostic_status'], 'OBSERVED')
                self.assertEqual(result['checks'][field]['status'], 'FAIL')
                self.assertEqual(result['checks'][field]['observed'], 4)
                for rate in ('min_benefit_acceptance', 'max_harm_acceptance'):
                    self.assertEqual(result['checks'][rate]['status'], 'UNKNOWN')

    def test_missing_class_is_unknown_even_with_forged_safe_zero(self):
        for sign in (-1, 1):
            document, _, _ = diagnostic_fixture()
            document['rows'] = [r for r in document['rows'] if r['gain'] == sign]
            document.update(status='PASS', met=True, scientific_support='PASS',
                            heads={'gate': {'acceptance': {'harm': {'rate': 0, 'support': 999}}}})
            result = self.evaluate(document=document)
            self.assert_outcome(result, 'UNKNOWN', 'UNRESOLVED_DIAGNOSTIC')
            self.assertEqual(result['diagnostic_status'], 'UNKNOWN')
            self.assertTrue(result['diagnostic_reasons'])

    def test_original_rows_override_self_signed_document_and_row_status(self):
        document, _, _ = diagnostic_fixture()
        document.update(status='PASS', met=True, requirement_status='PASS',
                        scientific_support='PASS', checks={key: {'status': 'PASS'} for key in CHECKS})
        for row in document['rows']:
            row.update(status='PASS', met=True, scientific_support='PASS')
        result = self.evaluate(dict(REQUIREMENTS, head='regression'), document=document)
        self.assert_outcome(result, 'FAIL', 'REQUIREMENTS_NOT_MET')
        self.assertEqual(result['checks']['max_harm_acceptance']['observed'], .25)

    def test_unknown_lineage_identity_stage_and_mixed_origin_remain_unknown(self):
        for defect in ('source', 'parent', 'stage', 'identity', 'mixed_origin'):
            document, identity, semantics = diagnostic_fixture()
            if defect == 'source':
                del document['rows'][0]['source_sha256']
            elif defect == 'parent':
                document['rows'][0].update(depth=1, parent_id='absent', parent_sha256='d' * 64,
                                           parent_origin='natural')
            elif defect == 'stage':
                document['rows'][0]['stage'] = 'UNKNOWN'
            elif defect == 'identity':
                identity['source_sha256'] = 'UNKNOWN'
            else:
                document['parents'] = [{'parent_id': 'P', 'source_sha256': 'd' * 64,
                                        'origin': 'natural', 'depth': 0, 'stage': 'evaluation'}]
                document['rows'][0].update(origin='artificial', depth=1, parent_id='P',
                                           parent_sha256='d' * 64, parent_origin='natural')
            with self.subTest(defect=defect):
                result = self.evaluate(document=document, identity=identity, semantics=semantics)
                self.assert_outcome(result, 'UNKNOWN', 'UNRESOLVED_DIAGNOSTIC')

    def test_declared_artificial_origin_has_only_finite_sample_assurance(self):
        document, _, _ = diagnostic_fixture()
        document['parents'] = [{'parent_id': 'P', 'source_sha256': 'd' * 64,
                                'origin': 'natural', 'depth': 0, 'stage': 'evaluation'}]
        for row in document['rows']:
            row.update(origin='artificial', depth=1, parent_id='P',
                       parent_sha256='d' * 64, parent_origin='natural')
        self.assert_outcome(self.evaluate(document=document), 'PASS')

    def test_neutral_rows_do_not_supply_beneficial_or_harmful_support(self):
        document, _, _ = diagnostic_fixture()
        neutral = deepcopy(document['rows'][0])
        neutral.update(pair_id='neutral', gain=0, predicted_gain=-1, p_better=.9)
        document['rows'].append(neutral)
        result = self.evaluate(document=document)
        self.assert_outcome(result, 'PASS')
        self.assertEqual(result['checks']['min_benefit_support']['observed'], 4)
        self.assertEqual(result['checks']['min_harm_support']['observed'], 4)
        self.assert_outcome(self.evaluate(dict(REQUIREMENTS, min_harm_support=5), document=document),
                            'UNKNOWN', 'INSUFFICIENT_CLASS_SUPPORT')

    def test_positive_benefit_sign_is_used_for_both_truth_and_regression(self):
        document, _, semantics = diagnostic_fixture()
        semantics['benefit_sign'] = 'positive'
        semantics['class_order'] = ['harm', 'neutral', 'benefit']
        self.assert_outcome(self.evaluate(document=document, semantics=semantics), 'PASS')
        result = self.evaluate(dict(REQUIREMENTS, head='regression', min_benefit_acceptance=.75,
                                    max_harm_acceptance=.25), document=document, semantics=semantics)
        self.assert_outcome(result, 'PASS')

    def decimal_document(self, harmful_accepted=False):
        document, _, _ = diagnostic_fixture()
        template = document['rows'][0]
        document['rows'] = []
        for kind, gain in [('benefit', -1), ('harm', 1)]:
            for i in range(10):
                row = deepcopy(template)
                accepts = i == 0 and (kind == 'benefit' or harmful_accepted)
                row.update(pair_id=kind + str(i), gain=gain, predicted_gain=-1 if accepts else 1,
                           p_better=.9)
                document['rows'].append(row)
        return document

    def test_decimal_tenth_boundary_uses_count_ratio_not_binary_rounding(self):
        requirements = dict(REQUIREMENTS, min_benefit_support=10, min_harm_support=10,
                            min_benefit_acceptance=.1)
        document = self.decimal_document()
        self.assert_outcome(self.evaluate(requirements, document=document), 'PASS')
        self.assert_outcome(self.evaluate(dict(requirements, min_benefit_acceptance=.10000000000000002),
                                          document=document), 'FAIL', 'REQUIREMENTS_NOT_MET')
        document = self.decimal_document(harmful_accepted=True)
        self.assert_outcome(self.evaluate(dict(requirements, max_harm_acceptance=.1), document=document), 'PASS')
        self.assert_outcome(self.evaluate(dict(requirements, max_harm_acceptance=.09999999999999999),
                                          document=document), 'FAIL', 'REQUIREMENTS_NOT_MET')

    def test_missing_unsupported_and_wrong_type_requirements_are_unknown(self):
        cases = [{}, dict(REQUIREMENTS, unexpected='PASS')]
        cases.extend({key: value for key, value in REQUIREMENTS.items() if key != missing}
                     for missing in REQUIREMENTS)
        for field in ('min_benefit_support', 'min_harm_support'):
            cases.extend(dict(REQUIREMENTS, **{field: value}) for value in (True, 0, -1, 1.5, 20001, '4'))
        for field in ('min_benefit_acceptance', 'max_harm_acceptance'):
            cases.extend(dict(REQUIREMENTS, **{field: value})
                         for value in (True, -.1, 1.1, '0', None))
        cases.extend(dict(REQUIREMENTS, head=value) for value in ('unknown', None, True, [], {}))
        raw, identity, semantics = diagnostic_fixture()
        for requirements in cases:
            with self.subTest(requirements=repr(requirements)):
                result = evaluate_decision_requirements(json.dumps(raw), identity, semantics, requirements)
                self.assert_outcome(result, 'UNKNOWN', 'DECLARE_DECISION_REQUIREMENTS')

    def test_non_json_and_oversized_requirements_are_strict_input_errors(self):
        document, identity, semantics = diagnostic_fixture()
        requirements = [None, [], 'PASS']
        requirements.extend(dict(REQUIREMENTS, min_benefit_acceptance=value)
                            for value in (float('nan'), float('inf')))
        requirements.append(dict(REQUIREMENTS, unexpected=[0] * 20000))
        requirements.append(dict(REQUIREMENTS, unexpected=object()))
        for requirement in requirements:
            with self.subTest(requirement=type(requirement).__name__), self.assertRaises(ValueError):
                evaluate_decision_requirements(json.dumps(document), identity, semantics, requirement)

    def test_original_strict_json_errors_are_not_replaced_by_forged_pass(self):
        _, identity, semantics = diagnostic_fixture()
        for raw in ('{"rows":[],"rows":[]}', '{"rows":NaN}', '{"rows":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                evaluate_decision_requirements(raw, identity, semantics, REQUIREMENTS)

    def test_inputs_and_returned_requirements_are_independent(self):
        document, identity, semantics = diagnostic_fixture()
        requirements = deepcopy(REQUIREMENTS)
        original = deepcopy((document, identity, semantics, requirements))
        result = self.evaluate(requirements, document, identity, semantics)
        self.assertEqual((document, identity, semantics, requirements), original)
        result['requirements']['head'] = 'classifier'
        self.assertEqual(requirements, REQUIREMENTS)

    def test_extracted_dependency_closure_retains_independent_oracles(self):
        code, included = extract_function((ROOT / 'scripts/rds_result_tools.py').read_bytes(),
                                          'evaluate_decision_requirements')
        self.assertIn('diagnose_decisions', included)
        self.assertIn('Fraction', included)
        namespace = {}
        exec(compile(code, '<extracted-requirements>', 'exec'), namespace)
        document, identity, semantics = diagnostic_fixture()
        for requirements, status in [(REQUIREMENTS, 'PASS'),
                                     (dict(REQUIREMENTS, min_benefit_acceptance=.5), 'FAIL'),
                                     (dict(REQUIREMENTS, min_harm_support=5), 'UNKNOWN')]:
            result = namespace['evaluate_decision_requirements'](json.dumps(document), identity, semantics, requirements)
            self.assertEqual(result, evaluate_decision_requirements(json.dumps(document), identity, semantics, requirements))
            self.assertEqual(result['status'], status)


if __name__ == '__main__':
    unittest.main()
