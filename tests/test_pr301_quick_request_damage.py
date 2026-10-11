"""Damaged secondary QUICK identity inputs report bounded coverage mismatch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
import test_pr301_partial_quick as fixture


class QuickRequestDamageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PartialQuickTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.crash()
        workspace.bind(self.f.child_root)
        self.root = self.f.child_root
        self.request = self.root / 'rds-exec-request.json'

    def test_deep_and_invalid_requests_report_mismatch_without_mutation(self):
        original = self.request.read_bytes()
        before = self.f.dump(self.f.parent), self.f.dump(self.f.child)
        for damaged in (b'{"nested":' + b'[' * 5000 + b'0' + b']' * 5000 + b'}',
                        b'{"nested":' + b'[' * 100 + b'0' + b']' * 100 + b'}',
                        b'\xff', b'{"a":1,"a":2}', b'{"x":NaN}', b'[]'):
            with self.subTest(prefix=damaged[:16]):
                self.request.write_bytes(damaged)
                self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
                with self.assertRaises(ValueError):
                    workspace.check_admission(self.root)
                self.assertEqual((self.f.dump(self.f.parent), self.f.dump(self.f.child)), before)
                self.assertEqual(self.request.read_bytes(), damaged)
        self.request.write_bytes(original)
        self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')

    def test_request_byte_bound_is_enforced(self):
        self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')
        with patch.object(workspace, 'MAX_QUICK_REQUEST_BYTES', 1, create=True):
            report = workspace.coverage(self.root)
        self.assertEqual(report['status'], 'MISMATCH')
        self.assertIn('byte bound', report['mismatch'])

    def test_real_coverage_cli_reports_json_and_exit_two(self):
        self.request.write_bytes(b'{"nested":' + b'[' * 5000 + b'0' + b']' * 5000 + b'}')
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
             'workspace', 'coverage'], capture_output=True, text=True, timeout=15,
            env={**os.environ, 'RDS_USAGE_LOG': '0'})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'MISMATCH')


if __name__ == '__main__':
    unittest.main()

