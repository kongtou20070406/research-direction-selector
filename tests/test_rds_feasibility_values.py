"""Leaf contract and equivalence tests for rds_feasibility_values."""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_feasibility
import rds_feasibility_values as values
import rds_project


class FeasibilityValuesTests(unittest.TestCase):
    def test_module_imports_only_stdlib_and_rds_project_values(self):
        tree = ast.parse((ROOT / "scripts/rds_feasibility_values.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - {"__future__", "hashlib", "json", "os", "pathlib", "platform", "shlex"},
                         {"rds_project_values"})

    def test_runtime_fingerprint_and_shell_argument_match_consumers(self):
        self.assertEqual(values.runtime_fingerprint(), rds_feasibility.runtime_fingerprint())
        self.assertEqual(values.IDENTITY, rds_project.IDENTITY)
        for sample in ("simple/path", "D:/Myfile/work space/RDS", "quote'test", "typo‘quote’"):
            self.assertEqual(values._shell_argument(sample), rds_project._shell_argument(sample))


if __name__ == "__main__":
    unittest.main()
