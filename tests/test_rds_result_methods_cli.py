"""Reach finite method values through qualification, application, goal predicates and recovery."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ResultMethodsConsumerTests(unittest.TestCase):
    def test_ambiguous_original_point_json_is_rejected(self):
        modules = []
        for name in ('specs', 'run'):
            spec = importlib.util.spec_from_file_location('strict_method_' + name, ROOT / ('examples/result-methods/' + name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
        selected = modules[0].specifications()[0]
        point = selected['args'][0]
        raw = b'{"values":[99,99],"values":[1,3],"sample_ids":["a","b"]}'
        point['identity']['source_sha256'] = hashlib.sha256(raw).hexdigest()
        source_map = {s['path']: s['text'].encode('utf-8') for s in selected['sources']}
        source_map[point['identity']['source_path']] = raw
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
            modules[1].check_original_inputs(selected, source_map)

    def test_modified_inline_inputs_cannot_borrow_original_hashes(self):
        modules = []
        for name in ('specs', 'run'):
            spec = importlib.util.spec_from_file_location('method_' + name, ROOT / ('examples/result-methods/' + name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
        for index in (0, 2, 3):
            specifications = modules[0].specifications()
            selected = specifications[index]
            if index == 0:
                selected['args'][0]['values'] = [99, 99]
            else:
                selected['args'][0] = selected['args'][0].replace('0.9', '0.8')
            source_map = {s['path']: s['text'].encode('utf-8') for s in selected['sources']}
            with self.assertRaises(ValueError):
                modules[1].check_original_inputs(selected, source_map)

    def test_public_owned_methods_consume_values_and_preserve_recovery(self):
        spec = importlib.util.spec_from_file_location('independent_method_specs', ROOT / 'examples/result-methods/specs.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            frozen = parent / 'specs.json'
            frozen.write_text(json.dumps(module.specifications(), indent=2, allow_nan=False), encoding='utf-8-sig')
            proc = subprocess.run([sys.executable, '-B', str(ROOT / 'examples/result-methods/run.py'),
                                   '--workspace', str(parent / 'owned'), '--specs', str(frozen)],
                                  capture_output=True, text=True, encoding='utf-8', timeout=180,
                                  env={**os.environ, 'RDS_USAGE_DB': str(parent / 'usage.sqlite3')})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            summary = json.loads(proc.stdout)
            counts = summary['tool_utilization']['counts']
            self.assertEqual(counts['applicable'], 4)
            self.assertEqual(counts['applicable_used'], 4)
            self.assertEqual(counts['applicable_consumed'], 4)
            self.assertTrue(summary['recovery_preserved_runs_and_budget'])
            final = json.loads((parent / 'owned/reports/after.json').read_text(encoding='utf-8'))
            self.assertIsNone(final['selected_run'])
            output = json.loads((parent / 'owned/outputs/diagnostic.json').read_text(encoding='utf-8'))
            self.assertEqual(output['cases'][0]['value']['heads']['gate']['acceptance']['harm']['rate'], 0)
            requirement = json.loads((parent / 'owned/outputs/requirements.json').read_text(encoding='utf-8'))
            self.assertEqual(requirement['status'], 'PASS')  # Driver qualification, distinct from returned observation.
            self.assertEqual(requirement['cases'][0]['value']['status'], 'FAIL')
            self.assertIs(requirement['cases'][0]['value']['met'], False)
            self.assertEqual(summary['scientific_gain'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
