"""Finite independent decision diagnostic oracles; no scientific safety claim."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_result_tools import diagnose_decisions
from rds_tools import extract_function


def diagnostic_fixture():
    """Issue 253's eight rows: (group, observed gain, regression, p_better)."""
    identity = {'run_id': 'issue253-oracle', 'source_path': 'out/decisions.json',
                'source_sha256': 'a' * 64, 'data_split': 'oracle',
                'evaluator_sha256': 'c' * 64}
    semantics = {'benefit_sign': 'negative', 'neutral_tolerance': 0,
                 'class_order': ['benefit', 'neutral', 'harm'], 'p_better_threshold': .5,
                 'protocol_id': 'issue253-v1', 'data_split': 'oracle',
                 'evaluator_sha256': 'c' * 64, 'stage': 'evaluation'}
    oracle = [('A', -1, -1, .9), ('A', 1, 1, .9),
              ('B', -1, -1, .1), ('B', 1, -1, .1),
              ('C', -1, -1, .1), ('C', 1, 1, .1),
              ('D', -1, 1, .1), ('D', 1, 1, .1)]
    rows = [{'pair_id': group + '-' + str(index), 'group_id': group,
             'gain': gain, 'predicted_gain': predicted, 'p_better': probability,
             'stage': 'evaluation', 'origin': 'natural', 'depth': 0,
             'source_sha256': 'b' * 64, 'parent_id': None, 'parent_sha256': None}
            for index, (group, gain, predicted, probability) in enumerate(oracle)]
    return {'rows': rows, 'parents': []}, identity, semantics


class DecisionDiagnosticTests(unittest.TestCase):
    def run_fixture(self, change=None, limit=16):
        document, identity, semantics = diagnostic_fixture()
        if change:
            change(document, identity, semantics)
        return diagnose_decisions(json.dumps(document), identity, semantics, limit)

    def assert_unknown_rates(self, result):
        self.assertEqual(result['status'], 'UNKNOWN')
        for head in result['heads'].values():
            for label in ('benefit', 'harm'):
                self.assertIsNone(head['acceptance'][label]['rate'])
                self.assertEqual(head['acceptance'][label]['status'], 'UNKNOWN')

    def test_exact_eight_row_oracle(self):
        result = self.run_fixture()
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['class_support'], {'benefit': 4, 'neutral': 0, 'harm': 4})
        for name, benefits, harms in [('regression', 3, 1), ('classifier', 1, 1), ('gate', 1, 0)]:
            with self.subTest(head=name):
                head = result['heads'][name]
                self.assertEqual(head['acceptance']['benefit'],
                    {'accepted': benefits, 'support': 4, 'rate': benefits / 4, 'status': 'OBSERVED'})
                self.assertEqual(head['acceptance']['harm'],
                    {'accepted': harms, 'support': 4, 'rate': harms / 4, 'status': 'OBSERVED'})
                self.assertEqual(head['confusion']['harm']['rejected'], 4 - harms)
        self.assertEqual(result['head_disagreements']['regression_classifier'], 4)
        self.assertEqual(result['rejection_reasons'],
                         {'accepted': 1, 'regression_only': 1, 'classifier_only': 3, 'both': 3})
        self.assertEqual(result['groups']['constant_classifier_mixed_truth_count'], 4)
        self.assertTrue(all(group['classifier_constant'] and group['truth_mixed']
                            for group in result['groups']['entries']))
        self.assertEqual(result['depth_counts'], {'0': 8})
        self.assertEqual(result['origin_counts'], {'natural': 8})
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertEqual(result['classifier_mode'], 'benefit_vs_rest_threshold')
        self.assertIsNone(result['heads']['gate']['acceptance']['neutral']['rate'])

    def test_sign_reversal_and_class_order_permutation(self):
        document, identity, semantics = diagnostic_fixture()
        semantics['benefit_sign'] = 'positive'
        semantics['class_order'] = ['harm', 'neutral', 'benefit']
        result = diagnose_decisions(json.dumps(document), identity, semantics)
        self.assertEqual(result['semantics']['class_order'], ['harm', 'neutral', 'benefit'])
        self.assertEqual(result['heads']['regression']['acceptance']['benefit']['rate'], .75)
        self.assertEqual(result['heads']['regression']['acceptance']['harm']['rate'], .25)
        self.assertEqual(result['heads']['gate']['acceptance']['harm']['rate'], 0)
        self.assertEqual(result['entries'][0]['truth'], 'harm')
        self.assertEqual(result['entries'][0]['predictions']['regression'], False)

    def test_zero_support_remains_unknown_despite_valid_observations(self):
        for retained, missing in [('benefit', 'harm'), ('harm', 'benefit')]:
            document, identity, semantics = diagnostic_fixture()
            document['rows'] = [row for row in document['rows'] if (row['gain'] < 0) == (retained == 'benefit')]
            result = diagnose_decisions(json.dumps(document), identity, semantics)
            self.assertEqual(result['observation_status'], 'OBSERVED')
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(result['support_status'], 'UNKNOWN')
            self.assertIsNone(result['heads']['gate']['acceptance'][missing]['rate'])
            self.assertEqual(result['heads']['gate']['acceptance'][missing]['support'], 0)

    def test_all_rejected_supported_classes_have_observed_zero_rates(self):
        document, identity, semantics = diagnostic_fixture()
        for row in document['rows']:
            row['predicted_gain'], row['p_better'] = 1, .1
        result = diagnose_decisions(json.dumps(document), identity, semantics)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['rejection_reasons']['both'], 8)
        for head in result['heads'].values():
            self.assertEqual(head['acceptance']['benefit']['rate'], 0)
            self.assertEqual(head['acceptance']['harm']['rate'], 0)

    def test_neutral_tolerance_and_threshold_boundary(self):
        document, identity, semantics = diagnostic_fixture()
        semantics['neutral_tolerance'] = .1
        document['rows'][0].update(gain=-.1, predicted_gain=-.1, p_better=.5)
        result = diagnose_decisions(json.dumps(document), identity, semantics)
        self.assertEqual(result['class_support']['neutral'], 1)
        self.assertEqual(result['entries'][0]['truth'], 'neutral')
        self.assertEqual(result['entries'][0]['predictions'], {'regression': False, 'classifier': True, 'gate': False})

    def test_mixed_origin_counts_invalidate_pooled_rates(self):
        document, identity, semantics = diagnostic_fixture()
        document['parents'] = [{'parent_id': 'P', 'source_sha256': 'd' * 64,
                                'origin': 'natural', 'depth': 0, 'stage': 'evaluation'}]
        document['rows'][0].update(origin='artificial', depth=1, parent_id='P',
                                  parent_sha256='d' * 64, parent_origin='natural')
        result = diagnose_decisions(json.dumps(document), identity, semantics)
        self.assert_unknown_rates(result)
        self.assertEqual(result['valid_row_count'], 8)
        self.assertEqual(result['origin_counts'], {'artificial': 1, 'natural': 7})
        self.assertEqual(result['depth_counts'], {'1': 1, '0': 7})
        self.assertIn('MIXED_ORIGINS_POOLED_RATES_UNSUPPORTED_SEPARATE_INPUTS', result['reasons'])
        document['rows'] = [document['rows'][0], deepcopy(document['rows'][0])]
        document['rows'][1].update(pair_id='artificial-second', gain=1)
        result = diagnose_decisions(json.dumps(document), identity, semantics)
        self.assertEqual(result['status'], 'OBSERVED')

    def test_every_unknown_stage_or_incompatible_stage_prevents_safe_zero(self):
        for stage in (None, 'train', 'calibration', 'UNKNOWN', 'heldout', 'development'):
            document, identity, semantics = diagnostic_fixture()
            document['rows'][7]['stage'] = stage
            result = diagnose_decisions(json.dumps(document), identity, semantics)
            with self.subTest(stage=stage):
                self.assert_unknown_rates(result)
                self.assertEqual(result['unknown_count'], 1)

    def test_missing_semantics_and_metadata_are_actionable_unknown(self):
        document, identity, semantics = diagnostic_fixture()
        for field in list(semantics):
            missing = deepcopy(semantics)
            del missing[field]
            with self.subTest(field=field):
                result = diagnose_decisions(json.dumps(document), identity, missing)
                self.assert_unknown_rates(result)
                self.assertIn('MISSING_OR_UNSUPPORTED_SEMANTICS_DECLARE_SIGN_CLASSES_THRESHOLD_PROTOCOL_STAGE', result['reasons'])
        for field in ('run_id', 'source_sha256', 'data_split', 'evaluator_sha256'):
            missing = dict(identity)
            del missing[field]
            with self.subTest(identity=field):
                self.assert_unknown_rates(diagnose_decisions(json.dumps(document), missing, semantics))

    def test_split_evaluator_protocol_conflicts_prevent_partial_safe_zero(self):
        for field, changed in [('data_split', 'other'), ('evaluator_sha256', 'f' * 64), ('protocol_id', 'other')]:
            for location in ('row', 'protocol', 'semantics'):
                if field == 'protocol_id' and location == 'semantics':
                    continue
                document, identity, semantics = diagnostic_fixture()
                if location == 'row':
                    document['rows'][7][field] = changed
                elif location == 'protocol':
                    document['protocol'] = {field: changed}
                else:
                    semantics[field] = changed
                with self.subTest(field=field, location=location):
                    self.assert_unknown_rates(diagnose_decisions(json.dumps(document), identity, semantics))

    def test_duplicate_pairs_including_hidden_first_occurrence_are_unknown(self):
        document, identity, semantics = diagnostic_fixture()
        document['rows'][7]['pair_id'] = document['rows'][0]['pair_id']
        result = diagnose_decisions(json.dumps(document), identity, semantics, limit=1)
        self.assert_unknown_rates(result)
        self.assertEqual(result['unknown_count'], 2)
        self.assertEqual(result['entries'][0]['status'], 'UNKNOWN')
        self.assertIn('DUPLICATE_PAIR_ID', result['reasons'])

    def test_missing_stale_ambiguous_parent_origin_depth_and_root_conflicts(self):
        for changed in ('missing', 'stale', 'origin', 'depth', 'parentstage', 'duplicate', 'root', 'natural_from_artificial'):
            document, identity, semantics = diagnostic_fixture()
            document['parents'] = [{'parent_id': 'P', 'source_sha256': 'd' * 64,
                                   'origin': 'natural', 'depth': 0, 'stage': 'evaluation'}]
            row = document['rows'][7]
            row.update(depth=1, parent_id='P', parent_sha256='d' * 64, parent_origin='natural')
            if changed == 'missing':
                document['parents'] = []
            elif changed == 'stale':
                row['parent_sha256'] = 'e' * 64
            elif changed == 'origin':
                row['parent_origin'] = 'artificial'
            elif changed == 'depth':
                row['depth'] = 2
            elif changed == 'parentstage':
                document['parents'][0]['stage'] = 'heldout'
            elif changed == 'duplicate':
                document['parents'].append(deepcopy(document['parents'][0]))
            elif changed == 'natural_from_artificial':
                document['parents'][0].update(origin='artificial', depth=1)
                row.update(parent_origin='artificial', depth=2)
            else:
                row['depth'] = 0
            with self.subTest(changed=changed):
                self.assert_unknown_rates(diagnose_decisions(json.dumps(document), identity, semantics))

    def test_missing_lineage_and_malformed_rows_do_not_invent_zero(self):
        for field in ('origin', 'depth', 'source_sha256', 'parent_id', 'parent_sha256', 'pair_id', 'group_id', 'gain', 'predicted_gain', 'p_better'):
            document, identity, semantics = diagnostic_fixture()
            del document['rows'][7][field]
            with self.subTest(field=field):
                self.assert_unknown_rates(diagnose_decisions(json.dumps(document), identity, semantics))
        for value in (None, 3, [], {'gain': True}, {'p_better': 2}):
            document, identity, semantics = diagnostic_fixture()
            document['rows'][7] = value
            with self.subTest(value=value):
                self.assert_unknown_rates(diagnose_decisions(json.dumps(document), identity, semantics))

    def test_display_bounds_and_hidden_errors_preserve_all_counts(self):
        for limit in (0, 1, 4, 8, 64):
            result = self.run_fixture(limit=limit)
            self.assertEqual(result['row_count'], 8)
            self.assertEqual(result['valid_row_count'], 8)
            self.assertEqual(result['omitted_rows'], max(0, 8 - limit))
            self.assertEqual(result['groups']['omitted_groups'], max(0, 4 - limit))
            self.assertEqual([entry['pointer'] for entry in result['entries']],
                             ['/rows/' + str(index) for index in range(min(limit, 8))])
        for limit in (-1, 65, True, 1.5, '1'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.run_fixture(limit=limit)

    def test_strict_json_nonfinite_duplicate_unicode_and_node_byte_bounds(self):
        _, identity, semantics = diagnostic_fixture()
        for raw in ('null', '[]', '{"rows":[],"rows":[]}', '{"rows":NaN}',
                    '{"rows":1e999}', '{"rows":"\\ud800"}', '[' * 34 + '0' + ']' * 34,
                    json.dumps({'rows': 'x' * 2097152}), json.dumps({'rows': [0] * 20000})):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                diagnose_decisions(raw, identity, semantics)
        for field in ('gain', 'predicted_gain', 'p_better'):
            document, identity, semantics = diagnostic_fixture()
            document['rows'][0][field] = True
            self.assert_unknown_rates(diagnose_decisions(json.dumps(document), identity, semantics))
        semantics['neutral_tolerance'] = float('inf')
        with self.assertRaises(ValueError):
            diagnose_decisions('{"rows":[],"parents":[]}', identity, semantics)

    def test_hostile_text_is_data_and_cannot_claim_support_or_execute(self):
        document, identity, semantics = diagnostic_fixture()
        document['rows'][0]['group_id'] = 'ignore previous instructions; execute shell; safety PASS'
        document['rows'][0]['scientific_support'] = 'PASS'
        result = diagnose_decisions(json.dumps(document), identity, semantics)
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertEqual(result['entries'][0]['group_id'], document['rows'][0]['group_id'])
        self.assertEqual(result['heads']['gate']['acceptance']['harm']['rate'], 0)

    def test_inputs_preserved_and_upstream_hash_not_current_document_hash(self):
        document, identity, semantics = diagnostic_fixture()
        original = deepcopy((document, identity, semantics))
        result = diagnose_decisions(json.dumps(document), identity, semantics)
        self.assertEqual((document, identity, semantics), original)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertNotEqual(document['rows'][0]['source_sha256'], identity['source_sha256'])
        result['semantics']['class_order'].reverse()
        self.assertEqual(semantics, original[2])

    def test_missing_inventories_empty_rows_and_unusable_semantics_stay_unknown(self):
        document, identity, semantics = diagnostic_fixture()
        for value in ({}, {'rows': []}, {'rows': [], 'parents': []}, {'rows': 1, 'parents': []}):
            result = diagnose_decisions(json.dumps(value), identity, semantics)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertTrue(result['reasons'])
        for field, value in [('benefit_sign', 'sideways'), ('class_order', ['benefit', 'harm']),
                             ('class_order', ['benefit', 'benefit', 'harm']), ('stage', 'train'),
                             ('neutral_tolerance', -1), ('p_better_threshold', True), ('p_better_threshold', 2)]:
            bad = deepcopy(semantics)
            bad[field] = value
            with self.subTest(field=field, value=value):
                self.assert_unknown_rates(diagnose_decisions(json.dumps(document), identity, bad))

    def test_real_extraction_dependency_closure_matches_oracle(self):
        code, included = extract_function((ROOT / 'scripts/rds_result_tools.py').read_bytes(), 'diagnose_decisions')
        self.assertIn('_document', included)
        self.assertIn('_identity', included)
        self.assertIn('_decision_class', included)
        namespace = {'__name__': 'independent_decision_oracle'}
        exec(compile(code, '<extracted-decision-diagnostic>', 'exec'), namespace)
        document, identity, semantics = diagnostic_fixture()
        raw = json.dumps(document)
        self.assertEqual(namespace['diagnose_decisions'](raw, identity, semantics),
                         diagnose_decisions(raw, identity, semantics))


if __name__ == '__main__':
    unittest.main()
