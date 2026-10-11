"""Saved output reconciliation; no optional ML imports or retraining."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'benchmark/result-methods/replay.py'
RECORDS = ROOT / 'benchmark/result-methods/results/20261010'
spec = importlib.util.spec_from_file_location('saved_methods_replay', SCRIPT)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class SavedResultReplayTests(unittest.TestCase):
    def test_original_outputs_reconcile_and_unfavorable_gate_remains_fail(self):
        result = replay.replay(RECORDS)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['decision_requirements']['status'], 'FAIL')
        self.assertIs(result['decision_requirements']['met'], False)
        self.assertEqual(result['independent_oracle']['gate']['harm'], {'accepted': 29, 'support': 48})
        self.assertEqual(result['training_executions'], 0)
        self.assertEqual(result['evaluator_binding']['original_status'], 'NEWLINE_HASH_MISMATCH')
        self.assertEqual(result['evaluator_binding']['current_status'], 'REBOUND_TO_ACTUAL_BYTES')
        self.assertEqual(result['original_worker_wall_seconds'], 9.732613899999706)
        self.assertEqual(result['scientific_gain'], 'UNKNOWN')

    def mutate(self, name, mutation, reason):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / 'records'
            shutil.copytree(RECORDS, copied)
            target = copied / name
            value = json.loads(target.read_text(encoding='utf-8'))
            mutation(value)
            target.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, reason):
                replay.replay(copied)

    def test_modified_original_probability_cannot_borrow_hash(self):
        self.mutate('original-predictions.json', lambda d: d['candidate_probabilities'][0].__setitem__(0, 0.5),
                    'Original source hash mismatch')

    def test_decision_row_values_reconciled_before_candidate(self):
        self.mutate('decision-rows.json', lambda d: d['rows'][0].__setitem__('predicted_gain', 99),
                    'Original decision row/value/identity mismatch')

    def test_split_overlap_rejected(self):
        self.mutate('splits.json', lambda d: d['train'].__setitem__(0, d['heldout'][0]), 'split IDs overlap')

    def test_fake_oracle_rejected(self):
        self.mutate('independent-oracle.json', lambda d: d['gate']['harm'].__setitem__('accepted', 0),
                    'Independent oracle mismatch')

    def test_saved_tool_self_signed_pass_cannot_replace_recomputation(self):
        self.mutate('tool-results.json', lambda d: d['decision_diagnostics']['heads']['gate']['acceptance']['harm'].__setitem__('rate', 0),
                    'Saved result replay mismatch')

    def test_cost_and_receipt_binding_preserved(self):
        self.mutate('model-costs.json', lambda d: d[0].__setitem__('wall_seconds', 0), 'fitting costs mismatch')
        self.mutate('receipt-fields.json', lambda d: d['bindings_before'][0].__setitem__('sha256', '0' * 64),
                    'receipt source binding mismatch')

    def test_original_output_cannot_be_overwritten_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'existing.json'
            output.write_bytes(b'original evidence')
            proc = subprocess.run([sys.executable, '-B', str(SCRIPT), '--records', str(RECORDS), '--output', str(output)],
                                  capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(output.read_bytes(), b'original evidence')
        proc = subprocess.run([sys.executable, '-B', str(SCRIPT), '--records', str(RECORDS),
                               '--output', str(RECORDS / 'new-output.json')], capture_output=True,
                              text=True, encoding='utf-8', timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((RECORDS / 'new-output.json').exists())


if __name__ == '__main__':
    unittest.main()
