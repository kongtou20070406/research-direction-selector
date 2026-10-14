"""Export original checkpoints/receipts to fit-ready rows with split isolation."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / p) for p in ('scripts', 'tests')]
from rds_bayes_scorer import fit_blr
from rds_cpu_jepa import fit_transitions
from rds_training_records import freeze_packet, export_training_records, split_for
from rds_transition import export_transitions
from test_rds_transition import receipt_row, checkpoint_row, reseal_checkpoint

BASE = 'LR = 1\ndef control(x):\n return x\ndef treatment(x):\n return x\n'
CODE = BASE.replace('LR = 1', 'LR = 2')
PLAN = dict(kind='project', train=['project-a'], validation=['project-b'], confirmation=['project-c'])


def fixture(code=CODE, project='project-a'):
    raw = b'{"score":0.6}'
    rr = receipt_row(status='SUCCEEDED', artifacts=[dict(path='result.json', kind='project_output', sha256=hashlib.sha256(raw).hexdigest())])
    receipt = json.loads(rr['body'])
    packet = dict(schema=1, project_id=project, decided_at=10, base_code=BASE,
                  before_state={'score': .5, 'diagnostics': {'gap': .2}},
                  candidates=[dict(id='c1', code=code, label={'path':'result.json','score_pointer':'/score'})])
    checkpoints = []
    for stage in ('before', 'after'):
        record = json.loads(checkpoint_row(receipt, stage)['body'])
        record['decision']['training_packet'] = freeze_packet(packet, 'c1')
        checkpoints.append(reseal_checkpoint(record))
    documents = [dict(run_id='r1', receipt_sha256=rr['sha256'], path='result.json', body=raw)]
    return rr, checkpoints, documents


class TrainingTests(unittest.TestCase):
    def test_export_fit_and_original_byte_binding(self):
        rr, cps, docs = fixture()
        exported = export_transitions([rr], cps)
        rows = export_training_records(exported, docs, PLAN)
        self.assertEqual(rows[0]['before_state']['diagnostics']['gap'], .2)
        self.assertEqual(rows[0]['outcome_score'], .6)
        self.assertEqual(rows[0]['scientific_support'], 'UNKNOWN')
        self.assertTrue(fit_blr(rows)['mean'])
        self.assertIsNotNone(fit_transitions(rows))
        docs[0]['body'] = b'{"score":1.0}'
        with self.assertRaisesRegex(ValueError, 'receipt-bound'):
            export_training_records(exported, docs, PLAN)

    def test_invalid_and_missing_are_not_zero(self):
        for code in (CODE.replace('def control(x):\n return x', 'def control(x):\n return 2*x'), CODE.replace('treatment(x)', 'treatment(x,y)')):
            rr, cps, docs = fixture(code)
            rows = export_training_records(export_transitions([rr], cps), docs, PLAN)
            self.assertEqual(rows[0]['validity'], 'INVALID')
            self.assertIsNone(rows[0]['outcome_score'])
        rr, cps, _ = fixture()
        rows = export_training_records(export_transitions([rr], cps), [], PLAN)
        self.assertEqual(rows[0]['validity'], 'UNKNOWN')
        self.assertIsNone(rows[0]['outcome_score'])

    def test_confirmation_is_exported_but_never_fitted_and_time_boundaries(self):
        rr, cps, docs = fixture(project='project-c')
        rows = export_training_records(export_transitions([rr], cps), docs, PLAN)
        self.assertEqual(rows[0]['split'], 'confirmation')
        for fit in (fit_blr, fit_transitions):
            with self.assertRaisesRegex(ValueError, 'Confirmation'):
                fit(rows)
        for t, expected in ((9,'train'),(10,'validation'),(20,'confirmation')):
            self.assertEqual(split_for({'decided_at':t}, dict(kind='time',train_before=10,validation_before=20)),expected)


if __name__ == '__main__':
    unittest.main()
