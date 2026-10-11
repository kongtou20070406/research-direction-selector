"""Reject an existing benchmark workspace without changing its original evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BenchmarkPreflightTests(unittest.TestCase):
    def test_cli_preserves_existing_successful_originals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = json.dumps({'status': 'PASS'}).encode('utf-8')
            (root / 'summary.json').write_bytes(original)
            proc = subprocess.run([sys.executable, '-B', str(ROOT / 'benchmark/result-methods/run.py'),
                                   '--workspace', str(root)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('WorkspacePreflightError', proc.stderr)
            self.assertEqual(list(root.iterdir()), [root / 'summary.json'])
            self.assertEqual((root / 'summary.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
