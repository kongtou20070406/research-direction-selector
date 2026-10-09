"""Serialization and final-partition reuse guards; not OS isolation tests."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_autonomy as autonomy
from rds_project import ProjectStore


class ConfirmationPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-confirmation-privacy-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = ProjectStore(self.root)
        self.label_bytes = b'{"private_target_canary": 9382174}'
        self.bindings = []
        for name, data, role in [('features.json', b'{"public_feature": 2}', 'data'),
                                  ('labels.json', self.label_bytes, 'data'),
                                  ('label-copy.json', self.label_bytes, 'config')]:
            (self.root / name).write_bytes(data)
            self.bindings.append({'path': name, 'role': role, 'sha256': hashlib.sha256(data).hexdigest()})
        confirmation = {'domain': 'continuous', 'rules': {'kind': 'numerical_expression_evaluation'},
                        'data': [{k: b[k] for k in ('path', 'sha256')} for b in self.bindings[:2]],
                        'confirmation_runs': ['final-check']}
        self.state = {'contract': {'bindings': self.bindings,
                                  'advisor_policy': {'confirmation': confirmation, 'autonomy': {}}},
                      'receipts': [], 'runs': []}

    def test_private_label_and_byte_identical_alias_not_serialized(self):
        result = autonomy.evidence_excerpts(self.store, self.state, set())
        self.assertEqual([x['path'] for x in result['frozen_inputs']], ['features.json'])
        self.assertNotIn('private_target_canary', json.dumps(result))
        self.assertEqual(len(result['withheld_confirmation_inputs']), 2)
        self.assertEqual(result['access_assurance'], 'SERIALIZATION_FILTER_ONLY_NOT_FILESYSTEM_ISOLATION')

    def test_canonical_path_alias_not_serialized(self):
        state = deepcopy(self.state)
        state['contract']['bindings'].append({**self.bindings[1], 'path': './labels.json'})
        result = autonomy.evidence_excerpts(self.store, state, set())
        self.assertEqual([x['path'] for x in result['frozen_inputs']], ['features.json'])
        self.assertEqual(len(result['withheld_confirmation_inputs']), 3)

    def test_final_output_not_opened_for_feedback(self):
        state = deepcopy(self.state)
        state['receipts'] = [{'run_id': 'final-check', 'ended_at': 1, 'artifacts': [
            {'kind': 'project_output', 'path': 'out/final.json', 'size': 32}]}]
        with patch('rds_owned_advisor._read_original', side_effect=AssertionError('must not read final output')):
            result = autonomy.evidence_excerpts(self.store, state, set())
        self.assertEqual(result['original_outputs'], [])

    def test_existing_noncontinuous_requests_unchanged(self):
        state = deepcopy(self.state)
        state['contract']['advisor_policy'].pop('confirmation')
        result = autonomy.evidence_excerpts(self.store, state, set())
        self.assertEqual(len(result['frozen_inputs']), 3)
        self.assertNotIn('withheld_confirmation_inputs', result)

    def test_invalid_continuous_holdout_fails_closed(self):
        state = deepcopy(self.state)
        state['contract']['advisor_policy']['confirmation']['data'].pop()
        with self.assertRaisesRegex(ValueError, 'features and private labels'):
            autonomy.evidence_excerpts(self.store, state, set())

    def test_final_attempt_blocks_new_request_before_preparation(self):
        # A real initialized ledger supplies the read transaction. The state
        # projection is reduced to the guard's input to avoid dispatching work.
        bindings = [self.bindings[0]]
        for role in ('code', 'config', 'evaluator', 'protocol'):
            path = self.root / (role + '.txt')
            path.write_bytes(b'{}')
            bindings.append({'path': path.name, 'role': role, 'sha256': hashlib.sha256(b'{}').hexdigest()})
        contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [[sys.executable, '-V']],
                    'output_roots': ['out'], 'budget': {'wall_seconds': 10}}
        protocol = {'data_split': 'synthetic', 'init': 'fresh', 'seed': 0, 'checkpoint': 'none',
                    'schedule': 'guard-only', 'sample_work': {'rows': 0}, 'numeric_protocol': 'none'}
        protocol.update({role + '_sha256': ProjectStore._role_sha(contract, role)
                         for role in ('code', 'config', 'data')})
        raw = json.dumps(protocol).encode()
        (self.root / 'protocol.txt').write_bytes(raw)
        bindings[-1]['sha256'] = hashlib.sha256(raw).hexdigest()
        self.store.initialize(contract)
        for status in ('COMPLETED', 'FAILED', 'INTERRUPTED', 'RUNNING'):
            with self.subTest(status=status):
                state = deepcopy(self.state)
                state['runs'] = [{'id': 'final-check', 'attempt_id': 'retained-original', 'status': status}]
                with patch('rds_owned_advisor._state', return_value=state), \
                        patch('rds_tool_workbench.prepare', side_effect=AssertionError('no new preparation')), \
                        patch('rds_jump.packet', side_effect=AssertionError('no new packet')):
                    self.assertEqual(autonomy.request_repair(self.store, {}), 'FINAL_CONFIRMATION_REACHED')
        self.assertEqual(self.store.snapshot()['runs'], [])


if __name__ == '__main__':
    unittest.main()
