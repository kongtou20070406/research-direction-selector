"""Binding publication must remain inside the ledger's writer transaction."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import test_rds_project
import rds_workspace as workspace
from rds_project import canonical


class BindingPublicationTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store = helper.root, helper.store

    def dump(self):
        with self.store._db(True) as db:
            return list(db.iterdump())

    def test_initial_pointer_is_published_under_sqlite_writer_lock(self):
        publish = workspace._write_pointer
        observed = []

        def locked_publish(root, pointer):
            with closing(sqlite3.connect(self.store.path, timeout=0)) as competing:
                try:
                    competing.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError as exc:
                    self.assertIn('locked', str(exc).lower())
                    observed.append('LOCKED')
                else:
                    competing.rollback()
                    observed.append('WRITER_ADMITTED')
            publish(root, pointer)

        with patch.object(workspace, '_write_pointer', side_effect=locked_publish):
            workspace.bind(self.root)
        self.assertEqual(observed, ['LOCKED'])
        self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')

    def test_generic_writer_cannot_spend_between_event_and_pointer(self):
        publish = workspace._write_pointer
        outcomes = []

        def interposed_writer(root, pointer):
            with self.store._db() as competing:
                competing.execute('PRAGMA busy_timeout=0')
                try:
                    competing.execute("UPDATE budget SET charged=charged+1 WHERE resource='wall_seconds'")
                except sqlite3.OperationalError as exc:
                    self.assertIn('locked', str(exc).lower())
                    outcomes.append('LOCKED')
                else:
                    competing.execute('INSERT INTO events(body) VALUES (?)',
                                      (canonical({'kind': 'BIND_WINDOW_SPEND'}),))
                    outcomes.append('SPENT_WITHOUT_POINTER')
            publish(root, pointer)

        with patch.object(workspace, '_write_pointer', side_effect=interposed_writer):
            workspace.bind(self.root)
        self.assertEqual(outcomes, ['LOCKED'])
        with self.store._db(True) as db:
            self.assertEqual(db.execute("SELECT charged FROM budget WHERE resource='wall_seconds'").fetchone()[0], 0)
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'bound.sqlite3'
            shutil.copyfile(self.store.path, backup)
            with self.store._db() as db:
                db.execute("UPDATE budget SET charged=charged+1 WHERE resource='wall_seconds'")
            shutil.copyfile(backup, self.store.path)
            self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')

    def test_initial_publication_failure_rolls_back_binding_event(self):
        before = self.dump()
        with patch.object(workspace, '_write_pointer', side_effect=OSError('initial pointer failed')):
            with self.assertRaisesRegex(OSError, 'initial pointer failed'):
                workspace.bind(self.root)
        self.assertEqual(self.dump(), before)
        self.assertFalse(workspace.pointer_path(self.root).exists())

    def test_uncertain_initial_publication_keeps_pointer_and_refuses_admission(self):
        publish = workspace._write_pointer
        before = self.dump()

        def uncertain_publish(root, pointer):
            publish(root, pointer)
            raise OSError('publication durability uncertain')

        with patch.object(workspace, '_write_pointer', side_effect=uncertain_publish):
            with self.assertRaisesRegex(OSError, 'publication durability uncertain'):
                workspace.bind(self.root)
        self.assertEqual(self.dump(), before)
        self.assertTrue(workspace.pointer_path(self.root).exists())
        self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
        with self.assertRaises(ValueError):
            workspace.check_admission(self.root)


if __name__ == '__main__':
    unittest.main()
