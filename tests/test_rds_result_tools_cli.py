"""Result values must reach real owned predicates with original provenance."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ResultToolsCLITests(unittest.TestCase):
    def test_three_qualified_operations_are_consumed_and_recovery_does_not_repeat(self):
        self.exercise_consumers(recipe=False)

    def test_recipe_uses_the_same_three_qualified_consumers_and_original_costs(self):
        self.exercise_consumers(recipe=True)

    def exercise_consumers(self, *, recipe):
        spec = importlib.util.spec_from_file_location('result_tools_example', ROOT / 'examples/result-tools/run.py')
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        with tempfile.TemporaryDirectory(prefix='rds-results-cli-') as folder:
            root = Path(folder)
            summary = example.run(root, recipe=recipe)
            use = summary['tool_utilization']
            self.assertEqual(use['counts']['applicable'], 3)
            self.assertEqual(use['counts']['used'], 3)
            self.assertEqual(use['counts']['consumed'], 3)
            by_name = {row['candidate']: row for row in use['tools']}
            for name, fact in [('extract', 'extract.value'), ('compare', 'compare.improvement'),
                               ('inventory', 'inventory.failure_count')]:
                row = by_name[name]
                self.assertTrue(row['result_consumed'])
                self.assertIn(fact, row['consumed_facts'])
                self.assertEqual(len(row['receipt_sha256']), 64)
                self.assertEqual(len(row['result_sha256']), 64)
            before = json.loads((root / 'reports/status-before-recovery.json').read_text(encoding='utf-8'))
            after = json.loads((root / 'reports/status-after-recovery.json').read_text(encoding='utf-8'))
            self.assertEqual(before['runs'], after['runs'])
            self.assertEqual(before['budget'], after['budget'])
            self.assertEqual(len(before['runs']), 3)
            self.assertEqual(len(before['receipts']), 3)
            self.assertTrue(summary['recovery_preserved_runs_and_budget'])
            costs = json.loads((root / 'reports/preparation-costs.json').read_text(encoding='utf-8'))
            self.assertEqual(len(costs), 1)
            self.assertEqual(len(costs[0]['validations']), 3)
            self.assertGreater(costs[0]['wall_seconds'], 0)
            self.assertEqual(summary['real_model_round_trips'], 'NOT_RUN')
            sizes = {row['operation']: row for row in summary['material_bytes']}
            self.assertGreater(sizes['extract']['bytes_difference'], 0)
            self.assertGreater(sizes['inventory']['bytes_difference'], 0)
            self.assertLess(sizes['compare']['bytes_difference'], 0)
            with self.assertRaisesRegex(ValueError, 'empty'):
                example.run(root, recipe=recipe)
