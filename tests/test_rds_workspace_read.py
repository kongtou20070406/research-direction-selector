"""Workspace identity reads cannot use pending writes or Store callbacks."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from rds_sqlite import readonly_connection
import rds_cas
import rds_workspace as workspace
from rds_project import ProjectStore
import test_rds_project


class ReadonlyConnectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='rds readonly # ')
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'db.sqlite3'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE sample (value INTEGER)')
            db.execute('INSERT INTO sample VALUES (1)')
        db.close()

    def test_platform_import_does_not_load_domain(self):
        code = "import sys; import rds_sqlite; assert [n for n in sys.modules if n.startswith('rds_')] == ['rds_sqlite']"
        result = subprocess.run([sys.executable, '-B', '-c', code],
                                cwd=ROOT / 'scripts', capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_database_is_not_created(self):
        missing = self.path.with_name('missing.sqlite3')
        with self.assertRaises(sqlite3.OperationalError):
            with readonly_connection(missing):
                self.fail('read opened a missing database')
        self.assertFalse(missing.exists())

    def test_readonly_settings_and_close_after_error(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            with readonly_connection(self.path) as db:
                self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                self.assertEqual(db.execute('PRAGMA busy_timeout').fetchone()[0], 10000)
                self.assertEqual(db.execute('SELECT value FROM sample').fetchone()['value'], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute('UPDATE sample SET value=2')
                raise RuntimeError('fixture')
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
        self.assertEqual(self.path.read_bytes(), before)

    def test_separate_reader_sees_committed_values_in_both_journal_modes(self):
        for journal in ('delete', 'wal'):
            with self.subTest(journal=journal):
                writer = sqlite3.connect(self.path)
                try:
                    self.assertEqual(writer.execute('PRAGMA journal_mode=' + journal).fetchone()[0], journal)
                    writer.execute('BEGIN IMMEDIATE')
                    writer.execute('UPDATE sample SET value=99')
                    with readonly_connection(self.path) as reader:
                        self.assertEqual(reader.execute('SELECT value FROM sample').fetchone()[0], 1)
                        self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0], journal)
                    self.assertTrue(writer.in_transaction)
                finally:
                    writer.rollback()
                    writer.close()


class WorkspaceReadTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store = helper.root, helper.store

    def test_bound_admission_and_cas_do_not_call_store_read_or_policy(self):
        workspace.bind(self.root)
        before = self.store.path.read_bytes(), workspace.pointer_path(self.root).read_bytes()
        with patch.object(ProjectStore, '_db', side_effect=AssertionError('Store callback')), \
                patch.object(ProjectStore, '_contract', side_effect=AssertionError('Store contract')):
            admitted = workspace.check_admission(self.root)
            self.assertIsInstance(admitted, ProjectStore)
            self.assertEqual(admitted.root, self.root)
            self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')
            ref = rds_cas.cas_bytes(self.root, b'read-boundary fixture')
        self.assertEqual(Path(ref['path']).read_bytes(), b'read-boundary fixture')
        self.assertEqual((self.store.path.read_bytes(), workspace.pointer_path(self.root).read_bytes()), before)

    def test_pending_events_cannot_hide_committed_rollback(self):
        workspace.bind(self.root)
        backup = self.root / 'snapshot.sqlite3'
        shutil.copyfile(self.store.path, backup)
        with self.store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO events(body) VALUES (?)", (json.dumps({'kind': 'READ_TEST'}),))
        anchor = workspace.read_pointer(self.root)['event_id']
        shutil.copyfile(backup, self.store.path)
        before = self.store.path.read_bytes(), workspace.pointer_path(self.root).read_bytes()
        writer = sqlite3.connect(self.store.path)
        try:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('INSERT INTO events(id,body) VALUES (?,?)',
                           (anchor, json.dumps({'kind': 'PENDING_REPAIR'})))
            self.assertEqual(writer.execute('SELECT MAX(id) FROM events').fetchone()[0], anchor)
            with self.assertRaisesRegex(ValueError, 'fewer events'):
                rds_cas.cas_bytes(self.root, b'must not publish')
            self.assertTrue(writer.in_transaction)
            self.assertFalse((self.root / '.rds/cas').exists())
        finally:
            writer.rollback()
            writer.close()
        self.assertEqual((self.store.path.read_bytes(), workspace.pointer_path(self.root).read_bytes()), before)

    def test_discovery_uses_reader_not_store_constructor(self):
        with patch.object(workspace, '_store', side_effect=AssertionError('Store discovery')):
            self.assertTrue(workspace._initialized(self.root))
            self.assertIsNone(workspace.read_pointer(self.root))
        missing = self.root / 'no-ledger'
        missing.mkdir()
        self.assertFalse(workspace._initialized(missing))
        with self.assertRaises(FileNotFoundError):
            with workspace._read_db(missing):
                self.fail('missing database opened')
        self.assertFalse((missing / '.rds').exists())

    def test_empty_ledger_remains_uninitialized(self):
        empty = self.root / 'empty'
        (empty / '.rds').mkdir(parents=True)
        db = sqlite3.connect(empty / '.rds/project.sqlite3')
        db.close()
        self.assertFalse(workspace._initialized(empty))


if __name__ == '__main__':
    unittest.main()
