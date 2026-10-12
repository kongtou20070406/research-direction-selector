"""An existing non-file workspace pointer is damage, never absence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
import test_rds_project as fixture


class PointerEntryDamageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProjectTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.root, self.store = self.f.root, self.f.store
        workspace.bind(self.root)
        self.path = workspace.pointer_path(self.root)
        self.saved = self.path.with_name('retained-pointer.json')

    def damage(self, kind):
        self.path.rename(self.saved)
        if kind == 'directory':
            self.path.mkdir()
        elif kind == 'fifo':
            os.mkfifo(self.path)
        else:
            try:
                self.path.symlink_to(self.root / 'missing-target')
            except OSError as exc:
                if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege unavailable')
                raise

    def refused(self, kind):
        self.damage(kind)
        before = self.store.path.read_bytes(), self.saved.read_bytes()
        self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
        with self.assertRaises(ValueError):
            self.store.register(self.f.spec())
        self.assertEqual((self.store.path.read_bytes(), self.saved.read_bytes()), before)

    def test_directory_pointer_is_damage(self):
        self.refused('directory')

    def test_broken_symlink_pointer_is_damage(self):
        self.refused('symlink')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO')
    def test_fifo_pointer_is_damage_without_blocking(self):
        self.refused('fifo')

    def test_non_file_pointer_rejects_supported_registration(self):
        self.damage('directory')
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.register(self.f.spec())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_pointer_damage_before_commit_rolls_back_accounting(self):
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            with self.store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE budget SET charged=charged+1 WHERE resource='wall_seconds'")
                self.damage('directory')
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_real_coverage_cli_reports_non_file_damage(self):
        self.damage('directory')
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
             'workspace', 'coverage'], capture_output=True, text=True, timeout=10,
            env={**os.environ, 'RDS_USAGE_LOG': '0'})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'MISMATCH')


if __name__ == '__main__':
    unittest.main()
