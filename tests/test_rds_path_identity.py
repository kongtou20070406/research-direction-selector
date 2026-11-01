"""Spelling-independent root identity for ledger and workspace guards (#207)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_path_identity as identity


class PathIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.real = self.base / 'runneradmin' / 'project'
        self.real.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_symlinked_alias_is_the_same_root(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.base / 'runneradmin', target_is_directory=True)
        self.assertTrue(identity.same(alias / 'project', self.real))
        self.assertEqual(identity.identity(alias / 'project'), identity.identity(self.real))
        self.assertFalse(identity.canonical_spelling(str(alias / 'project')))

    def test_short_name_alias_expands_on_the_existing_ancestor(self):
        # Simulate Windows 8.3 names: the alias exists, and GetLongPathNameW expands only
        # existing paths, so a not-yet-created ledger tail keeps its own spelling.
        short = str(self.base.resolve() / 'RUNNER~1')
        long = str(self.base.resolve() / 'runneradmin')
        real_exists = Path.exists
        with patch.object(identity, '_long', side_effect=lambda text: text.replace(short, long)), \
                patch.object(Path, 'exists', lambda path: real_exists(Path(str(path).replace(short, long)))):
            spelled = os.path.join(short, 'project', '.rds')
            self.assertEqual(identity._expand(spelled), os.path.join(long, 'project', '.rds'))

    def test_extended_length_prefixes_are_not_identities(self):
        self.assertEqual(identity._strip('\\\\?\\C:\\work\\rds'), 'C:\\work\\rds')
        self.assertEqual(identity._strip('\\\\?\\UNC\\server\\share'), '\\\\server\\share')

    def test_distinct_roots_and_non_paths_differ(self):
        other = self.base / 'other'
        other.mkdir()
        self.assertFalse(identity.same(other, self.real))
        self.assertFalse(identity.same(None, self.real))
        self.assertFalse(identity.same(self.real / 'missing-a', self.real / 'missing-b'))


if __name__ == '__main__':
    unittest.main()
