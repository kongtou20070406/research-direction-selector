"""Independent finite oracles for data-only result tools and AST admission.

These checks establish software behavior, not scientific support or permission
to reuse a function on inputs outside its native exact-task qualification.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_result_tools import extract_json_result, compare_metrics, summarize_failures
from rds_tools import extract_function


def source_identity(**changes):
    result = {'run_id': 'development-1', 'source_path': 'out/test-results.json',
              'source_sha256': 'a' * 64}
    result.update(changes)
    return result


def metric_identity(**changes):
    result = source_identity(data_sha256='b' * 64, data_split='public-development',
                             evaluator_sha256='c' * 64, metric='loss',
                             metric_definition='mean absolute residual', reduction='mean',
                             unit='dimensionless', direction='minimize',
                             code_sha256='d' * 64, config_sha256='e' * 64)
    result.update(changes)
    return result


def failure_document():
    return {'case_count': 5, 'failure_count': 2, 'skipped_count': 1,
            'failures': [{'case_id': 'tests.A.test_one', 'trace': 'AssertionError: 2 != 3'},
                         {'case_id': 'tests.B.test_two', 'trace': 'ValueError: original failure'}],
            'skipped': [{'case_id': 'tests.C.test_three', 'reason': 'optional backend unavailable'}]}


class JSONResultTests(unittest.TestCase):
    def test_scalar_extraction_preserves_identity_and_unicode_pointer(self):
        identity = source_identity(source_path='输出/结果.json')
        original = deepcopy(identity)
        result = extract_json_result('{"结果":{"a/b~c":7.5}}', '/结果/a~1b~0c', identity)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['value'], 7.5)
        self.assertEqual(result['identity'], original)
        self.assertEqual(result['pointer'], '/结果/a~1b~0c')
        self.assertEqual(result['missing_fields'], [])
        self.assertEqual(result['conflicting_fields'], [])
        self.assertEqual(result['identity_assurance'], 'CALLER_METADATA')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        result['identity']['run_id'] = 'changed-result'
        self.assertEqual(identity, original)

    def test_scalar_types_are_preserved(self):
        for raw, expected in [('0', 0), ('false', False), ('true', True), ('"原始文本"', '原始文本')]:
            with self.subTest(raw=raw):
                result = extract_json_result(raw, '', source_identity())
                self.assertEqual(result['status'], 'OBSERVED')
                self.assertEqual(result['value'], expected)
                self.assertIs(type(result['value']), type(expected))

    def test_in_band_success_flags_do_not_certify_source_or_scientific_support(self):
        result = extract_json_result('{"verified":true,"scientific_support":"PASS","x":2}',
                                     '/x', source_identity())
        self.assertEqual(result['value'], 2)
        self.assertEqual(result['identity_assurance'], 'CALLER_METADATA')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_missing_null_and_compound_values_remain_unknown(self):
        for raw, pointer, reason in [('{"x":1}', '/absent', 'MISSING_VALUE'),
                                    ('[1]', '/2', 'MISSING_VALUE'),
                                    ('{"x":null}', '/x', 'NULL_VALUE'),
                                    ('{"x":[]}', '/x', 'NON_SCALAR_VALUE_EXPAND_ORIGINAL'),
                                    ('{"x":{}}', '/x', 'NON_SCALAR_VALUE_EXPAND_ORIGINAL')]:
            with self.subTest(raw=raw, pointer=pointer):
                result = extract_json_result(raw, pointer, source_identity())
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIsNone(result['value'])
                self.assertEqual(result['reason'], reason)

    def test_each_required_source_field_must_be_known(self):
        for field in ('run_id', 'source_path', 'source_sha256'):
            unknowns = (None, 'UNKNOWN') if field.endswith('_sha256') else (None, '', 'UNKNOWN')
            for unknown in unknowns:
                with self.subTest(field=field, unknown=unknown):
                    result = extract_json_result('{"x":2}', '/x', source_identity(**{field: unknown}))
                    self.assertEqual(result['status'], 'UNKNOWN')
                    self.assertIsNone(result['value'])
                    self.assertIn(field, result['missing_fields'])
            identity = source_identity()
            del identity[field]
            self.assertEqual(extract_json_result('{"x":2}', '/x', identity)['status'], 'UNKNOWN')

    def test_source_claim_conflicts_are_visible_at_every_supported_origin(self):
        for field, claimed in [('run_id', 'another-run'), ('source_path', 'other.json'),
                               ('source_sha256', 'f' * 64), ('data_split', 'test'),
                               ('code_sha256', '1' * 64), ('config_sha256', '2' * 64)]:
            identity = source_identity(data_split='development', code_sha256='d' * 64, config_sha256='e' * 64)
            for origin in (None, 'binding', 'protocol', 'identity'):
                with self.subTest(field=field, origin=origin):
                    document = {'x': 4}
                    if origin is None:
                        document[field] = claimed
                    else:
                        document[origin] = {field: claimed}
                    result = extract_json_result(json.dumps(document), '/x', identity)
                    self.assertEqual(result['status'], 'UNKNOWN')
                    self.assertIsNone(result['value'])
                    self.assertIn(field, result['conflicting_fields'])

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        for raw in ('{"x":1,"x":2}', '{"nested":{"x":1,"x":2}}',
                    '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e400}',
                    '{"x":1} trailing', '{"x":'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                extract_json_result(raw, '/x', source_identity())

    def test_invalid_pointer_escapes_and_array_aliases_are_rejected(self):
        for pointer in ('x', '/~', '/~2', '/~~01', '/00', '/01', '/+0', '/-1', '/1.0', '/-', '/١'):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                extract_json_result('[10,20]', pointer, source_identity())
        self.assertEqual(extract_json_result('[10,20]', '/0', source_identity())['value'], 10)
        self.assertEqual(extract_json_result('{"00":3,"~1":4}', '/00', source_identity())['value'], 3)
        self.assertEqual(extract_json_result('{"00":3,"~1":4}', '/~01', source_identity())['value'], 4)

    def test_malformed_identity_shape_hash_and_unicode_are_rejected(self):
        for identity in (None, [], source_identity(extra='unsupported'), source_identity(run_id=3),
                         source_identity(source_sha256='ABCDEF' * 10 + 'ABCD'),
                         source_identity(source_sha256='a' * 63), source_identity(run_id='x' * 1025),
                         source_identity(source_path='中' * 342),
                         source_identity(source_path='bad\ud800')):
            with self.subTest(identity=repr(identity)), self.assertRaises(ValueError):
                extract_json_result('1', '', identity)
        with self.assertRaises(ValueError):
            extract_json_result('{"x":"\\ud800"}', '/x', source_identity())

    def test_raw_bytes_depth_and_node_bounds(self):
        for raw in (' ' * 2097153, json.dumps('中' * 700000, ensure_ascii=False),
                    '[' * 33 + '0' + ']' * 33, '[' + ','.join(['0'] * 20000) + ']'):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                extract_json_result(raw, '', source_identity())
        with self.assertRaises(ValueError):
            extract_json_result('{}', '/x' * 33, source_identity())
        with self.assertRaises(ValueError):
            extract_json_result('{}', '/' + 'x' * 4096, source_identity())

    def test_large_selected_material_is_not_returned_as_a_scalar_fact(self):
        result = extract_json_result(json.dumps({'x': '中' * 683}, ensure_ascii=False), '/x', source_identity())
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIsNone(result['value'])
        self.assertEqual(result['reason'], 'VALUE_EXCEEDS_DISPLAY_LIMIT_EXPAND_ORIGINAL')
        self.assertEqual(extract_json_result(json.dumps({'x': 'x' * 2048}), '/x', source_identity())['status'], 'OBSERVED')


class MetricComparisonTests(unittest.TestCase):
    def points(self, direction='minimize'):
        candidate = {'status': 'OBSERVED', 'value': 6,
                     'identity': metric_identity(run_id='candidate', source_path='out/candidate.json',
                                                 source_sha256='1' * 64, code_sha256='2' * 64,
                                                 config_sha256='3' * 64, direction=direction)}
        baseline = {'status': 'OBSERVED', 'value': 10, 'identity': metric_identity(direction=direction)}
        return candidate, baseline

    def test_difference_and_direction_preserve_distinct_run_source_code_config(self):
        for direction, improvement in [('minimize', 4), ('maximize', -4)]:
            with self.subTest(direction=direction):
                candidate, baseline = self.points(direction)
                originals = deepcopy((candidate, baseline))
                result = compare_metrics(candidate, baseline)
                self.assertEqual(result['status'], 'COMPARABLE')
                self.assertEqual(result['delta'], -4)
                self.assertEqual(result['improvement'], improvement)
                self.assertEqual(result['candidate'], {'value': 6, 'value_omitted': False,
                                                       'identity': originals[0]['identity']})
                self.assertEqual(result['baseline'], {'value': 10, 'value_omitted': False,
                                                      'identity': originals[1]['identity']})
                self.assertEqual(set(result['matched_fields']), {'data_sha256', 'data_split', 'evaluator_sha256',
                                 'metric', 'metric_definition', 'reduction', 'unit', 'direction'})
                self.assertEqual(result['scientific_support'], 'UNKNOWN')
                self.assertEqual((candidate, baseline), originals)

    def test_zero_delta_is_a_known_observation(self):
        candidate, baseline = self.points()
        candidate['value'] = baseline['value']
        result = compare_metrics(candidate, baseline)
        self.assertEqual(result['status'], 'COMPARABLE')
        self.assertEqual((result['delta'], result['improvement']), (0, 0))

    def test_each_comparison_identity_is_required_and_must_match(self):
        fields = ('data_sha256', 'data_split', 'evaluator_sha256', 'metric', 'metric_definition',
                  'reduction', 'unit', 'direction')
        for field in fields:
            candidate, baseline = self.points()
            del candidate['identity'][field]
            with self.subTest(field=field, mode='missing'):
                result = compare_metrics(candidate, baseline)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIsNone(result['delta'])
                self.assertIn('candidate.' + field, result['missing_fields'])
            candidate, baseline = self.points()
            candidate['identity'][field] = 'f' * 64 if field.endswith('_sha256') else 'different'
            with self.subTest(field=field, mode='conflicting'):
                result = compare_metrics(candidate, baseline)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIsNone(result['improvement'])
                self.assertIn(field, result['conflicting_fields'])

    def test_unknown_source_missing_value_bool_and_unresolved_input(self):
        for change in ('source', 'missing', 'null', 'bool', 'text', 'status'):
            candidate, baseline = self.points()
            if change == 'source':
                baseline['identity']['source_sha256'] = 'UNKNOWN'
            elif change == 'missing':
                del candidate['value']
            elif change == 'null':
                candidate['value'] = None
            elif change == 'bool':
                candidate['value'] = True
            elif change == 'text':
                candidate['value'] = '6'
            else:
                candidate['status'] = 'UNKNOWN'
            with self.subTest(change=change):
                result = compare_metrics(candidate, baseline)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIsNone(result['delta'])
                self.assertIsNone(result['improvement'])

    def test_nonfinite_and_compound_metric_inputs_are_malformed(self):
        for value in (float('nan'), float('inf'), -float('inf'), [], {}):
            candidate, baseline = self.points()
            candidate['value'] = value
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                compare_metrics(candidate, baseline)
        for candidate, baseline in ((None, {}), ({}, []), ({'value': 1}, {'value': 0})):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                compare_metrics(candidate, baseline)

    def test_finite_operands_with_unrepresentable_difference_remain_unknown(self):
        for cv, bv in ((1e308, -1e308), (10 ** 400, 1.0)):
            candidate, baseline = self.points()
            candidate['value'], baseline['value'] = cv, bv
            result = compare_metrics(candidate, baseline)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertIsNone(result['delta'])
            self.assertEqual(result['reason'], 'UNREPRESENTABLE_DIFFERENCE')

    def test_same_run_conflicting_source_and_unsupported_direction_are_unknown(self):
        candidate, baseline = self.points()
        candidate['identity']['run_id'] = baseline['identity']['run_id']
        result = compare_metrics(candidate, baseline)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('same_run_different_sources', result['conflicting_fields'])
        candidate, baseline = self.points('sideways')
        result = compare_metrics(candidate, baseline)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['reason'], 'UNSUPPORTED_METRIC_DIRECTION')


class FailureInventoryTests(unittest.TestCase):
    def summarize(self, document, **kwargs):
        return summarize_failures(json.dumps(document, ensure_ascii=False), source_identity(), **kwargs)

    def test_existing_format_counts_and_original_entries(self):
        result = self.summarize(failure_document())
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual((result['case_count'], result['failure_count'], result['skipped_count'],
                          result['unknown_count'], result['total_records'], result['omitted_records']),
                         (5, 2, 1, 0, 3, 0))
        self.assertEqual(result['entries'], [
            {'kind': 'failures', 'case_id': 'tests.A.test_one', 'pointer': '/failures/0', 'field': 'trace',
             'text': 'AssertionError: 2 != 3', 'text_bytes': 22, 'text_omitted': False, 'status': 'OBSERVED'},
            {'kind': 'failures', 'case_id': 'tests.B.test_two', 'pointer': '/failures/1', 'field': 'trace',
             'text': 'ValueError: original failure', 'text_bytes': 28, 'text_omitted': False, 'status': 'OBSERVED'},
            {'kind': 'skipped', 'case_id': 'tests.C.test_three', 'pointer': '/skipped/0', 'field': 'reason',
             'text': 'optional backend unavailable', 'text_bytes': 28, 'text_omitted': False, 'status': 'OBSERVED'}])
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertNotIn('root_cause', result)

    def test_explicit_empty_inventory_differs_from_absent_inventory(self):
        document = {'case_count': 0, 'failure_count': 0, 'skipped_count': 0, 'failures': [], 'skipped': []}
        result = self.summarize(document)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual((result['failure_count'], result['unknown_count'], result['omitted_records']), (0, 0, 0))
        for field in ('failures', 'skipped'):
            missing = deepcopy(document)
            del missing[field]
            with self.subTest(field=field):
                result = self.summarize(missing)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIsNone(result['failure_count'])
                self.assertIsNone(result['unknown_count'])
                self.assertIn('MISSING_OR_INVALID_INVENTORY', result['reasons'])

    def test_duplicate_case_ids_across_or_within_inventories_remain_visible(self):
        for field in ('failures', 'skipped'):
            document = failure_document()
            document[field][-1]['case_id'] = document['failures'][0]['case_id']
            with self.subTest(field=field):
                result = self.summarize(document)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertEqual(result['unknown_count'], 1)
                self.assertIn('DUPLICATE_CASE_ID', result['reasons'])
                self.assertEqual(result['entries'][-1 if field == 'skipped' else 1]['status'], 'UNKNOWN')

    def test_malformed_records_are_not_silently_removed_or_classified(self):
        for row in (None, {'case_id': '', 'trace': 'failure'}, {'case_id': 'A', 'trace': 3},
                    {'case_id': 'A', 'trace': 'failure', 'cause': 'invented'}):
            document = {'case_count': 1, 'failure_count': 1, 'skipped_count': 0, 'failures': [row], 'skipped': []}
            with self.subTest(row=row):
                result = self.summarize(document)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertEqual(result['observed_failure_records'], 1)
                self.assertEqual(result['unknown_count'], 1)
                self.assertEqual(result['entries'][0]['status'], 'UNKNOWN')
                self.assertIsNone(result['entries'][0]['text'])

    def test_missing_bool_negative_and_conflicting_counts_do_not_create_zero(self):
        for field in ('case_count', 'failure_count', 'skipped_count'):
            for value in (None, True, -1, '0'):
                document = failure_document()
                document[field] = value
                with self.subTest(field=field, value=value):
                    result = self.summarize(document)
                    self.assertEqual(result['status'], 'UNKNOWN')
                    self.assertIn('MISSING_OR_INVALID_COUNTS', result['reasons'])
            document = failure_document()
            del document[field]
            self.assertEqual(self.summarize(document)['status'], 'UNKNOWN')
        for field, value in [('case_count', 2), ('failure_count', 0), ('skipped_count', 2)]:
            document = failure_document()
            document[field] = value
            result = self.summarize(document)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertIsNone(result['failure_count'])
            self.assertIn('COUNT_INVENTORY_CONFLICT', result['reasons'])

    def test_display_limits_preserve_omission_counts_and_original_locators(self):
        for limit, pointers in [(0, []), (1, ['/failures/0']), (2, ['/failures/0', '/failures/1']),
                                (3, ['/failures/0', '/failures/1', '/skipped/0'])]:
            with self.subTest(limit=limit):
                result = self.summarize(failure_document(), limit=limit)
                self.assertEqual(result['status'], 'OBSERVED')
                self.assertEqual([entry['pointer'] for entry in result['entries']], pointers)
                self.assertEqual(result['omitted_records'], 3 - limit)
                self.assertEqual(result['failure_count'], 2)
        for limit in (-1, 65, True, 1.0, '1'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.summarize(failure_document(), limit=limit)

    def test_unicode_and_long_trace_do_not_invent_a_truncated_cause(self):
        document = {'case_count': 1, 'failure_count': 1, 'skipped_count': 0,
                    'failures': [{'case_id': '测试.失败', 'trace': '错' * 683}], 'skipped': []}
        result = self.summarize(document)
        self.assertEqual(result['status'], 'OBSERVED')
        entry = result['entries'][0]
        self.assertEqual(entry['case_id'], '测试.失败')
        self.assertEqual(entry['pointer'], '/failures/0')
        self.assertEqual(entry['text_bytes'], 2049)
        self.assertIsNone(entry['text'])
        self.assertTrue(entry['text_omitted'])
        self.assertEqual(result['omitted_records'], 0)

    def test_hidden_duplicate_is_still_counted_when_display_limit_is_zero(self):
        document = failure_document()
        document['skipped'][0]['case_id'] = document['failures'][0]['case_id']
        result = self.summarize(document, limit=0)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['unknown_count'], 1)
        self.assertEqual(result['entries'], [])
        self.assertEqual(result['omitted_records'], 3)
        self.assertIn('DUPLICATE_CASE_ID', result['reasons'])

    def test_source_claim_conflicts_and_missing_identity_stay_unknown(self):
        for field, claim in [('run_id', 'other'), ('source_path', 'other.json'), ('source_sha256', 'f' * 64)]:
            document = failure_document()
            document['identity'] = {field: claim}
            result = self.summarize(document)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertIn('INCOMPLETE_OR_CONFLICTING_IDENTITY', result['reasons'])
        result = summarize_failures(json.dumps(failure_document()), source_identity(run_id=None))
        self.assertEqual(result['status'], 'UNKNOWN')

    def test_non_object_duplicate_nonfinite_and_oversized_reports_are_rejected(self):
        for raw in ('[]', 'null', '{"failures":[],"failures":[]}', '{"case_count":NaN}',
                    json.dumps({'trace': 'x' * 2097152})):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                summarize_failures(raw, source_identity())


class ResultToolExtractionTests(unittest.TestCase):
    def test_all_three_tools_extract_with_real_dependency_closures(self):
        raw = (ROOT / 'scripts/rds_result_tools.py').read_bytes()
        calls = [
            ('extract_json_result', ['{"x":7}', '/x', source_identity()],
             {'status': 'OBSERVED', 'value': 7, 'scientific_support': 'UNKNOWN'}),
            ('compare_metrics', [{'value': 3, 'identity': metric_identity(run_id='candidate', source_sha256='1' * 64)},
                                 {'value': 5, 'identity': metric_identity()}],
             {'status': 'COMPARABLE', 'delta': -2, 'improvement': 2, 'scientific_support': 'UNKNOWN'}),
            ('summarize_failures', [json.dumps({'case_count': 0, 'failure_count': 0, 'skipped_count': 0,
                                             'failures': [], 'skipped': []}), source_identity()],
             {'status': 'OBSERVED', 'failure_count': 0, 'unknown_count': 0, 'scientific_support': 'UNKNOWN'})]
        for entry, args, expected_fields in calls:
            with self.subTest(entry=entry):
                code, included = extract_function(raw, entry)
                self.assertIn(entry, included)
                self.assertIn('_identity', included)
                namespace = {'__name__': 'independent_result_tool_test'}
                exec(compile(code, '<extracted-result-tool>', 'exec'), namespace)
                result = namespace[entry](*args)
                self.assertEqual({key: result[key] for key in expected_fields}, expected_fields)

    def test_json_loads_import_and_alias_are_admitted(self):
        for prefix, call in [('from json import loads', 'loads'),
                             ('from json import loads as parse_data', 'parse_data')]:
            code, included = extract_function((prefix + '\ndef tool(raw):\n    return ' + call + '(raw)\n').encode(), 'tool')
            self.assertIn(call, included)
            namespace = {}
            exec(compile(code, '<loads-alias>', 'exec'), namespace)
            self.assertEqual(namespace['tool']('{"x":3}'), {'x': 3})

    def test_other_json_and_unsafe_imports_are_rejected(self):
        sources = [
            'import json\ndef tool(raw):\n    return json.loads(raw)\n',
            'from json import dumps\ndef tool(raw):\n    return dumps(raw)\n',
            'from json import JSONDecoder\ndef tool(raw):\n    return JSONDecoder().decode(raw)\n',
            'from json import loads, dumps\ndef tool(raw):\n    return loads(raw)\n',
            'from json import *\ndef tool(raw):\n    return loads(raw)\n',
            'from .json import loads\ndef tool(raw):\n    return loads(raw)\n',
            'import os\ndef tool(raw):\n    return os.system(raw)\n',
            'from pathlib import Path\ndef tool(raw):\n    return Path(raw).read_text()\n',
            'from subprocess import run\ndef tool(raw):\n    return run(raw)\n']
        for source in sources:
            with self.subTest(source=source.splitlines()[0]), self.assertRaises(ValueError):
                extract_function(source.encode(), 'tool')

    def test_prior_dynamic_private_stateful_and_default_restrictions_remain(self):
        sources = [
            'def tool(raw):\n    import json\n    return json.loads(raw)\n',
            'def tool(raw):\n    def nested():\n        return raw\n    return nested()\n',
            'def tool(raw):\n    return (lambda: raw)()\n',
            'def tool(raw):\n    return raw.__class__\n',
            'def tool(raw):\n    return raw.read_text()\n',
            'def tool(raw=[]):\n    return raw\n',
            'STATE = []\ndef tool(raw):\n    return STATE\n',
            'def tool(raw):\n    global STATE\n    STATE = raw\n    return STATE\n',
            'def tool(raw):\n    with raw as handle:\n        return handle\n',
            '@decorator\ndef tool(raw):\n    return raw\n']
        for source in sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                extract_function(source.encode(), 'tool')


if __name__ == '__main__':
    unittest.main()
