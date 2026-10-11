"""Binding must not silently hide ledgers behind metadata links."""
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
from rds_project import ProjectStore
import test_rds_project as fixture


class LinkedStateBindingTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixture.ProjectTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.root, self.store = self.helper.root, self.helper.store

    def child(self):
        root = self.root / 'child'
        root.mkdir()
        for binding in self.helper.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], root / binding['path'])
        return root

    def link(self, link, target, directory=True):
        if os.name == 'nt' and directory:
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, timeout=3,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.addCleanup(os.rmdir, link)
        else:
            try:
                link.symlink_to(target, target_is_directory=directory)
            except OSError as exc:
                if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege unavailable')
                raise
            self.addCleanup(link.unlink)

    def assert_refused_without_changes(self, child):
        before = self.store.path.read_bytes(), child.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'linked workspace metadata'):
            workspace.bind(self.root)
        self.assertFalse(workspace.pointer_path(self.root).exists())
        self.assertEqual((self.store.path.read_bytes(), child.path.read_bytes()), before)

    def test_linked_state_preserves_original_reservation_and_settlement(self):
        root = self.child()
        target = root / 'state'
        target.mkdir()
        self.link(root / '.rds', target)
        child = ProjectStore(root)
        child.initialize(deepcopy(self.helper.contract))
        child.register(self.helper.spec())
        self.assertEqual(child.snapshot()['runs'][0]['status'], 'RESERVED')
        self.assert_refused_without_changes(child)
        receipt = child.execute('r1')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(child.recover('r1')['sha256'], receipt['sha256'])
        self.assertEqual(child.snapshot()['budget']['wall_seconds']['reserved'], 0)

    def test_quiescent_linked_metadata_refuses_before_identity_publication(self):
        root = self.child()
        target = root / 'state'
        target.mkdir()
        self.link(root / '.rds', target)
        child = ProjectStore(root)
        child.initialize(deepcopy(self.helper.contract))
        self.assert_refused_without_changes(child)

    def test_linked_database_cannot_hide_active_reservation(self):
        root = self.child()
        child = ProjectStore(root)
        child.initialize(deepcopy(self.helper.contract))
        target = root / 'original.sqlite3'
        child.path.rename(target)
        self.link(child.path, target, directory=False)
        child.register(self.helper.spec())
        self.assert_refused_without_changes(child)
        self.assertEqual(child.execute('r1')['run_status'], 'SUCCEEDED')


if __name__ == '__main__':
    unittest.main()
