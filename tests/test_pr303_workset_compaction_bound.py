"""Byte-bound regressions using logical paths, not OS long-path support."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor_workset import _compact, DETAIL_BYTES
from rds_campaign import _json_cas
from rds_project import canonical, digest
from rds_quick import cas_json


def selection():
    return {'status': 'REVIEWED', 'selected_run': None, 'observation': 'UNKNOWN',
            'next_move': {'kind': 'RESOLVE_PREMISE'}, 'selection_basis': None,
            'authorization': 'UNCHANGED', 'execution_started': False,
            'planning': {'scope': 'GLOBAL', 'open_predicates': ['p' * 300] * 8,
                         'local_checks': [], 'ready_obligations': []}}


class WorksetCompactionBoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def logical_root(self, size, unicode=False):
        unit = '\u76ee\u5f55' if unicode else 'deep-path-'
        parts = []
        while len(str(self.root.joinpath(*parts)).encode('utf-8')) < size:
            parts.append(unit * 4)
        return self.root.joinpath(*parts)

    def compact_at(self, value, logical):
        original = deepcopy(value)
        publications = []

        def publish(root, item):
            self.assertEqual(root, logical)
            ref = cas_json(self.root, item)
            publications.append((deepcopy(item), ref))
            return {**ref, 'path': str(logical / Path(ref['path']).relative_to(self.root))}

        with patch('rds_advisor_workset.cas_json', side_effect=publish):
            result = _compact(SimpleNamespace(root=logical), value)
        size = len(canonical(result).encode('utf-8'))
        self.assertLessEqual(size, DETAIL_BYTES,
                             f'root_bytes={len(str(logical).encode("utf-8"))}, result_bytes={size}')
        self.assertEqual(value, original)
        self.assertTrue(result['details_omitted'])
        self.read_original(result['original'], logical, original)
        if isinstance(value, dict) and isinstance(value.get('planning'), dict):
            self.assertTrue(any(item == value['planning'] for item, _ in publications))
            if 'planning' in result:
                self.assertEqual(result['planning']['scope'], value['planning'].get('scope'))
                self.assertTrue(result['planning']['details_omitted'])
                if 'original' in result['planning']:
                    self.read_original(result['planning']['original'], logical, value['planning'])
        return result

    def read_original(self, ref, logical, expected):
        path = Path(ref['path'])
        if path.is_absolute():
            relative = path.relative_to(logical)
            physical_ref = {**ref, 'path': str(self.root / relative)}
        else:
            self.assertEqual(ref['path_base'], 'project_root')
            relative = path
            physical_ref = ref
        raw = (self.root / relative).read_bytes()
        self.assertEqual(raw, canonical(expected).encode('utf-8'))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ref['sha256'])
        self.assertEqual(ref['sha256'], digest(expected))
        self.assertEqual(ref['bytes'], len(raw))
        # Existing native JSON-CAS consumers accept absolute and root-relative paths.
        self.assertEqual(_json_cas(self.root, physical_ref), expected)

    def test_deep_900_byte_root_uses_single_absolute_original(self):
        logical = self.logical_root(900)
        result = self.compact_at(selection(), logical)
        self.assertTrue(Path(result['original']['path']).is_absolute())
        self.assertNotIn('original', result['planning'])
        self.assertEqual(result['observation'], 'UNKNOWN')
        self.assertEqual(result['status'], 'REVIEWED')
        self.assertIsNone(result['selected_run'])

    def test_unicode_root_is_bounded_by_utf8_bytes(self):
        logical = self.logical_root(900, unicode=True)
        self.assertGreater(len(str(logical).encode('utf-8')), len(str(logical)))
        result = self.compact_at(selection(), logical)
        self.assertNotIn('original', result['planning'])
        self.assertTrue(Path(result['original']['path']).is_absolute())

    def test_locator_alone_over_cap_uses_existing_relative_cas_convention(self):
        result = self.compact_at(selection(), self.logical_root(5000, unicode=True))
        ref = result['original']
        self.assertFalse(Path(ref['path']).is_absolute())
        self.assertEqual(ref['path'], '.rds/cas/' + ref['sha256'] + '.json')
        self.assertEqual(result['planning']['scope'], 'GLOBAL')
        self.assertEqual(result['observation'], 'UNKNOWN')

    def test_oversized_identities_are_omitted_whole_and_exact_in_cas(self):
        value = selection()
        for key in ('id', 'feedback_sha256', 'proposal_sha256', 'hypothesis_key', 'selected_run'):
            value[key] = key + '\u8eab\u4efd' * 2000
        result = self.compact_at(value, self.logical_root(900))
        self.assertTrue(result['identity_omitted'])
        for key in ('id', 'feedback_sha256', 'proposal_sha256', 'hypothesis_key', 'selected_run'):
            self.assertNotIn(key, result)
        self.assertEqual(result['observation'], 'UNKNOWN')
        self.assertEqual(result['status'], 'REVIEWED')
        self.assertEqual(result['planning']['scope'], 'GLOBAL')
        self.assertNotIn('authorization', result)
        self.assertNotIn('execution_started', result)

    def test_oversized_planning_scope_is_omitted_without_truncation(self):
        value = selection()
        value['planning']['scope'] = 'scope:' + '\u5206\u533a' * 2000
        result = self.compact_at(value, self.root)
        self.assertNotIn('planning', result)
        self.assertTrue(result['planning_omitted'])
        self.assertEqual(result['observation'], 'UNKNOWN')

    def test_existing_short_root_keeps_both_absolute_locators_and_fields(self):
        value = selection()
        original = deepcopy(value)
        result = _compact(SimpleNamespace(root=self.root), value)
        self.assertLessEqual(len(canonical(result).encode('utf-8')), DETAIL_BYTES)
        self.assertEqual(result['next_move'], value['next_move'])
        self.assertIsNone(result['selection_basis'])
        self.assertNotIn('identity_omitted', result)
        self.assertNotIn('planning_omitted', result)
        for ref, item in ((result['original'], value),
                          (result['planning']['original'], value['planning'])):
            self.assertTrue(Path(ref['path']).is_absolute())
            self.assertNotIn('path_base', ref)
            self.assertEqual(_json_cas(self.root, ref), item)
        result['planning']['scope'] = 'changed'
        self.assertEqual(value, original)

    def test_small_values_remain_deep_copies_without_cas_publication(self):
        for value in ({'observation': 'UNKNOWN', 'authorization': 'UNCHANGED',
                       'planning': {'scope': 'LOCAL', 'local_checks': []}},
                      ['small', {'nested': []}], None, '\u5b57' * 500):
            with self.subTest(value_type=type(value).__name__):
                before = deepcopy(value)
                with patch('rds_advisor_workset.cas_json') as publish:
                    result = _compact(SimpleNamespace(root=self.root), value)
                publish.assert_not_called()
                self.assertEqual(result, before)
                if isinstance(result, dict):
                    result['planning']['local_checks'].append('changed')
                if isinstance(result, list):
                    result[1]['nested'].append('changed')
                self.assertEqual(value, before)

    def test_large_non_dict_original_stays_exact_at_long_root(self):
        self.compact_at(['\u6761\u4ef6' * 2000, {'status': 'UNKNOWN'}], self.logical_root(5000))

    def test_byte_bound_across_path_and_identity_sizes(self):
        for size in (500, 800, 900, 1200, 1800, 2000, 5000):
            for unicode in (False, True):
                with self.subTest(root_bytes=size, unicode=unicode):
                    value = selection()
                    value['id'] = 'identity:' + '\u76ee' * size
                    result = self.compact_at(value, self.logical_root(size, unicode))
                    self.assertEqual(result['observation'], 'UNKNOWN')
                    if 'id' in result:
                        self.assertEqual(result['id'], value['id'])
                    else:
                        self.assertTrue(result['identity_omitted'])
                    self.assertEqual(result['planning']['scope'], 'GLOBAL')


if __name__ == '__main__':
    unittest.main()
