"""Lower layers must not import the reference CLI to use state/value services."""
import ast
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from import_graph import inventory


class EntrypointImportTests(unittest.TestCase):
    def test_no_lower_layer_cli_dependency(self):
        report = inventory(ROOT)
        self.assertEqual([], [edge for edge in report['upward']
                              if edge['target'] == 'rds_cli'])

    def test_lower_consumers_import_without_cli(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import rds_adversary, rds_campaign, rds_math, rds_project_lifecycle
import rds_tools, rds_tool_compare, rds_workspace
from rds_state import RDSState
assert 'rds_cli' not in sys.modules
assert RDSState.__module__ == 'rds_state'
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reference_values_only_depend_on_shared_values(self):
        tree = ast.parse((ROOT / 'scripts/rds_reference_values.py').read_text(encoding='utf-8'))
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        modules.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                       for alias in node.names)
        self.assertLessEqual({name for name in modules if name.startswith('rds_')}, {'rds_core'})


if __name__ == '__main__':
    unittest.main()
