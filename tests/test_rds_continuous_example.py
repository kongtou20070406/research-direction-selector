"""Exercise public CLI init/drive/continuation, including failed final scoring."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('continuous_example', ROOT / 'examples/continuous-confirmation/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class ContinuousExampleTests(unittest.TestCase):
    def test_initialized_contract_does_not_serialize_private_labels(self):
        import rds_autonomy
        with tempfile.TemporaryDirectory(prefix='rds-continuous-private-') as parent:
            root, contract = example.build(Path(parent) / 'project')
            store = example.ProjectStore(root)
            store.initialize(contract)
            excerpts = rds_autonomy.evidence_excerpts(store, store.snapshot(), set())
            paths = [v['path'] for v in excerpts['frozen_inputs']]
            self.assertIn('features.json', paths)
            self.assertNotIn('labels.json', paths)
            self.assertEqual(excerpts['access_assurance'], 'SERIALIZATION_FILTER_ONLY_NOT_FILESYSTEM_ISOLATION')

    def test_four_cli_outcomes_remain_scoped_and_do_not_repeat(self):
        expected = {'correct': ('PASS', 'GOAL_CONFIRMED'),
                    'wrong': ('FAIL', 'FINAL_CONFIRMATION_REACHED'),
                    'undefined': ('UNKNOWN', 'DOMAIN_CONFIRMATION_UNKNOWN'),
                    'abstain': ('UNKNOWN', 'DOMAIN_CONFIRMATION_UNKNOWN')}
        with tempfile.TemporaryDirectory(prefix='rds-continuous-cli-') as parent:
            for mode, (verdict, terminal) in expected.items():
                with self.subTest(mode=mode):
                    result = example.run(Path(parent) / mode, mode)
                    self.assertEqual(result['confirmation']['task_confirmation'], verdict)
                    self.assertEqual(result['controller_status'], terminal)
                    self.assertEqual(result['receipts'], 2)
                    self.assertEqual(result['repeat_executed'], [])
                    self.assertEqual(result['model_calls'], 0)
                    self.assertEqual(result['scientific_gain'], 'UNMEASURED')
                    if mode in ('correct', 'wrong', 'abstain'):
                        self.assertEqual(result['confirmation']['confirmation_independence'], 'DECLARED_EXPOSED')
                        self.assertEqual(result['confirmation']['generalization'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
