"""Ancestor binding must preserve unfinished wheel recovery."""
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
import rds_wheel
import rds_workspace as workspace
from rds_project import ProjectStore
import test_pr311_wheel_binding_serialization as fixture


class PendingWheelBindingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.WheelBindingSerializationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root, self.wheel = self.f.root, self.f.wheel

    def refused_unchanged(self, reason):
        parent = ProjectStore(self.root)
        with parent._db(True) as db:
            before = list(db.iterdump())
        wheel_files = self.f.files()
        with self.assertRaisesRegex(ValueError, reason):
            workspace.bind(self.root)
        with parent._db(True) as db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertEqual(self.f.files(), wheel_files)
        self.assertFalse(workspace.pointer_path(self.root).exists())

    def test_interrupted_effects_recover_before_binding(self):
        state = rds_wheel.read_json(self.wheel.state_path('state.json'))
        after = self.wheel.state_path('dead.txt').read_text(encoding='utf-8') + 'journal_recovery\n'
        effect = self.wheel.effect('dead.txt', after, append=True)
        with patch.object(self.wheel, 'finish_commit', side_effect=OSError('interrupted wheel commit')):
            with self.assertRaisesRegex(OSError, 'interrupted wheel commit'):
                self.wheel.commit(state, [effect])
        self.assertIn('effects', rds_wheel.read_json(self.wheel.state_path('state.json')))
        snapshot = ProjectStore(self.wheel.root).snapshot()
        self.assertFalse(any(run['status'] in {'RESERVED', 'RUNNING'} for run in snapshot['runs']))
        self.assertEqual(snapshot['budget']['wall_seconds']['reserved'], 0)
        self.refused_unchanged('pending descendant wheel')
        self.wheel.prompt()
        self.assertNotIn('effects', rds_wheel.read_json(self.wheel.state_path('state.json')))
        self.assertEqual(self.wheel.state_path('dead.txt').read_text(encoding='utf-8'), after)
        self.wheel.prompt()
        self.assertEqual(self.wheel.state_path('dead.txt').read_text(encoding='utf-8'), after)
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_pending_dispatch_refuses_before_native_registration(self):
        self.assertTrue(self.wheel.propose('researcher', 'screen_change'))
        project = self.wheel.project

        def stop_create(action, *args):
            if action == 'create':
                raise OSError('interrupted wheel registration')
            return project(action, *args)

        with patch.object(self.wheel, 'project', side_effect=stop_create):
            with self.assertRaisesRegex(OSError, 'interrupted wheel registration'):
                self.wheel.tick()
        state = rds_wheel.read_json(self.wheel.state_path('state.json'))
        self.assertIsNotNone(state['pending'])
        self.assertNotIn('effects', state)
        self.assertFalse(state['pending']['submitted'])
        self.refused_unchanged('pending descendant wheel')
        self.assertEqual(self.wheel.tick(), 0)
        self.assertEqual(self.wheel.tick(), 0)
        self.assertIsNone(rds_wheel.read_json(self.wheel.state_path('state.json'))['pending'])
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_damaged_journal_refuses_without_identity_publication(self):
        path = self.wheel.state_path('state.json')
        original = path.read_bytes()
        for raw in (b'{', b'[]', b'{"pending":null,"pending":{}}'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                self.refused_unchanged('')
        path.write_bytes(original)
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_oversize_journal_refuses(self):
        with patch.object(workspace, 'MAX_BIND_WHEEL_STATE_BYTES', 1, create=True):
            self.refused_unchanged('byte bound')

    def test_hard_linked_journal_refuses(self):
        alias = self.root / 'journal-alias.json'
        os.link(self.wheel.state_path('state.json'), alias)
        self.refused_unchanged('multiple hard links')
        alias.unlink()
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_quiescent_descendant_and_own_journal_do_not_block(self):
        own = self.root / '.rds' / 'wheel'
        own.mkdir()
        (own / 'state.json').write_text(json.dumps({'effects': [], 'pending': None}), encoding='ascii')
        self.assertEqual(workspace.bind(self.root)['status'], 'BOUND')

    def test_linked_wheel_directory_cannot_hide_journal(self):
        directory = self.wheel.directory
        target = directory.with_name('retained-wheel')
        directory.rename(target)
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(directory), str(target)],
                                    capture_output=True, timeout=3,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.addCleanup(os.rmdir, directory)
        else:
            directory.symlink_to(target, target_is_directory=True)
            self.addCleanup(directory.unlink)
        self.refused_unchanged('linked workspace metadata')

    def test_nonregular_state_refuses(self):
        path = self.wheel.state_path('state.json')
        path.unlink()
        path.mkdir()
        self.refused_unchanged('regular file')


if __name__ == '__main__':
    unittest.main()
