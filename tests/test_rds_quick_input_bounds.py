"""Source discovery keeps the original cap even as RDS itself grows."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_quick import _inputs, MAX_FILES, MAX_TOTAL_BYTES


class QuickInputBoundsTests(unittest.TestCase):
    def test_128_sources_bind_bytes_and_129_are_rejected_without_execution(self):
        self.assertEqual(MAX_FILES, 128)
        self.assertEqual(MAX_TOTAL_BYTES, 64 * 1024 * 1024)
        with tempfile.TemporaryDirectory(prefix='rds-input-bound-') as tmp:
            root = Path(tmp).resolve()
            entry = root / 'entry.py'
            # Discovery is syntax-only. Importing any member would fail.
            for i in range(128):
                (root / f'part_{i}.py').write_text(
                    "raise RuntimeError('discovery must not execute source')\n", encoding='utf-8')
            entry.write_text('\n'.join(f'import part_{i}' for i in range(127)), encoding='utf-8')
            files, raw = _inputs(root, [sys.executable, 'entry.py'], [])
            expected = {entry, *(root / f'part_{i}.py' for i in range(127))}
            self.assertEqual(set(files), expected)
            self.assertEqual(set(raw), expected)
            for path in expected:
                self.assertIn('code', files[path])
                self.assertEqual(raw[path], path.read_bytes())
            entry.write_text(entry.read_text(encoding='utf-8')+'\nimport part_127\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'exceeds 128 files or 64 MiB'):
                _inputs(root, [sys.executable, 'entry.py'], [])


if __name__ == '__main__':
    unittest.main()
