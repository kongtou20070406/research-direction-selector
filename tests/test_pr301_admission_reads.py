"""Admission observations serialize with pending pointer publication."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
from rds_project import canonical
import test_rds_project


class AdmissionReadTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store = helper.root, helper.store

    def observation_during_publication(self, observe, expected, *, initial=False):
        if not initial:
            workspace.bind(self.root)
        published, release, attempted = (threading.Event() for _ in range(3))
        publish = workspace._write_pointer

        def pause(root, pointer):
            publish(root, pointer)
            published.set()
            if not release.wait(5):
                raise RuntimeError('Admission read publication release timed out')

        def write():
            if initial:
                return workspace.bind(self.root)
            with self.store._db() as db:
                db.execute('INSERT INTO events(body) VALUES (?)',
                           (canonical({'kind': 'READ_PUBLICATION_PROBE'}),))

        def read():
            attempted.set()
            return observe()

        with patch.object(workspace, '_write_pointer', side_effect=pause), \
                ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(write)
            try:
                self.assertTrue(published.wait(3))
                pointer = json.loads(workspace.pointer_path(self.root).read_text(encoding='utf-8'))
                with closing(sqlite3.connect(self.store.path)) as db:
                    committed = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
                self.assertGreater(pointer['event_id'], committed)
                reading = pool.submit(read)
                self.assertTrue(attempted.wait(3))
                # The reader must not interpret the writer's pending pointer
                # against an older committed ledger as a restored snapshot.
                threading.Event().wait(0.2)
                self.assertFalse(reading.done(), 'Admission must wait for pointer and ledger commit')
            finally:
                release.set()
            writing.result(timeout=5)
            self.assertEqual(reading.result(timeout=5), expected)
        self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')

    def test_admission_waits_for_progress_commit(self):
        self.observation_during_publication(
            lambda: str(workspace.check_admission(self.root).root), str(self.root))

    def test_coverage_waits_for_progress_commit(self):
        self.observation_during_publication(lambda: workspace.coverage(self.root)['status'], 'BOUND')

    def test_project_snapshot_coverage_waits_for_progress_commit(self):
        self.observation_during_publication(
            lambda: self.store.snapshot()['workspace_binding']['status'], 'BOUND')

    def test_coverage_waits_for_initial_binding_commit(self):
        self.observation_during_publication(
            lambda: workspace.coverage(self.root)['status'], 'BOUND', initial=True)

    def test_nonobject_event_damage_returns_mismatch_json(self):
        workspace.bind(self.root)
        pointer = workspace.pointer_path(self.root).read_bytes()
        with self.store._db(True) as db:
            row = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')=?",
                             (workspace.EVENT_KIND,)).fetchone()
            original = row['body']
            budget = list(db.execute('SELECT * FROM budget'))
        for body in ([], [1], None, True, False, 1, 'not an event'):
            with self.subTest(body=body):
                with closing(sqlite3.connect(self.store.path)) as db, db:
                    db.execute('DROP TRIGGER IF EXISTS events_no_update')
                    db.execute('UPDATE events SET body=? WHERE id=?', (canonical(body), row['id']))
                self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
                with self.assertRaises(ValueError):
                    workspace.check_admission(self.root)
                result = subprocess.run(
                    [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
                     'workspace', 'coverage'], capture_output=True, text=True,
                    encoding='utf-8', timeout=15)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'MISMATCH')
                self.assertEqual(workspace.pointer_path(self.root).read_bytes(), pointer)
                with self.store._db(True) as db:
                    self.assertEqual(list(db.execute('SELECT * FROM budget')), budget)
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('UPDATE events SET body=? WHERE id=?', (original, row['id']))
        self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')

    def test_committed_pointer_still_refuses_restored_snapshot(self):
        workspace.bind(self.root)
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'bound.sqlite3'
            shutil.copyfile(self.store.path, backup)
            with self.store._db() as db:
                db.execute('INSERT INTO events(body) VALUES (?)',
                           (canonical({'kind': 'COMMITTED_READER_CONTROL'}),))
            pointer = workspace.pointer_path(self.root).read_bytes()
            shutil.copyfile(backup, self.store.path)
            self.assertEqual(workspace.coverage(self.root)['status'], 'MISMATCH')
            with self.assertRaisesRegex(ValueError, 'snapshot was restored'):
                workspace.check_admission(self.root)
            self.assertEqual(workspace.pointer_path(self.root).read_bytes(), pointer)


if __name__ == '__main__':
    unittest.main()
