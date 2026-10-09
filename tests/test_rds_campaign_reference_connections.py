"""Public reference writers must retain the native mutation transaction."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark.run import Project
import rds_cli as cli
import rds_campaign as campaign
from rds_project import ProjectStore
import test_rds_project as fixture


class CampaignReferenceConnectionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(campaign.ENVIRONMENT, None)
        temporary = tempfile.TemporaryDirectory(prefix='rds-reference-connection-')
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        helper = fixture.ProjectTests('runTest')
        helper.setUp()
        self.addCleanup(helper.tearDown)
        project_root = self.workspace / 'project'
        project_root.mkdir()
        for binding in helper.contract['bindings']:
            shutil.copyfile(helper.root / binding['path'], project_root / binding['path'])
        self.store = ProjectStore(project_root)
        self.store.initialize(helper.contract)
        reference = Project()
        self.addCleanup(reference.close)
        reference_root = self.workspace / 'reference'
        reference_root.mkdir()
        for name in ('model.py', 'dev.csv', 'test.csv'):
            shutil.copyfile(reference.root / name, reference_root / name)
        self.state = cli.RDSState(reference_root)
        contract = reference_root / 'reference-contract.json'
        contract.write_text(json.dumps(reference.contract), encoding='utf-8')
        cli.cmd_init(SimpleNamespace(contract=str(contract)), self.state)

    def test_public_writable_connect_refuses_before_creating_any_state(self):
        fresh_root = self.workspace / 'fresh'
        fresh_root.mkdir()
        fresh = cli.RDSState(fresh_root)
        with self.assertRaisesRegex(ValueError, 'transaction'):
            # If the original implementation returns a connection, close it
            # before assertion failure; the original created ledger is evidence.
            connection = fresh.connect(create=True)
            connection.close()
        self.assertFalse(fresh.directory.exists())
        with self.assertRaisesRegex(ValueError, 'cannot create state'):
            connection = fresh.connect(create=True, readonly=True)
            connection.close()
        self.assertFalse(fresh.directory.exists())
        with self.assertRaisesRegex(ValueError, 'transaction'):
            connection = self.state.connect()
            connection.close()

    def test_public_readonly_connection_remains_readable_after_binding(self):
        binding = campaign.bind(self.store, self.workspace)
        connection = self.state.connect(readonly=True)
        try:
            state = cli.RDSState.read_state(connection)
            self.assertEqual(state['budget']['spent']['runs'], 0)
            self.assertEqual(campaign.binding(self.state.root), binding)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("INSERT INTO events(body) VALUES ('{}')")
        finally:
            connection.close()

    def test_reference_transaction_commits_before_waiting_bind_and_postbind_writes_refuse(self):
        entered, release_writer, bind_attempting, bind_done = Event(), Event(), Event(), Event()
        marker = self.workspace / campaign.MARKER
        before_budget = cli.cmd_status(None, self.state)['budget']
        retained = {}

        def writer():
            with self.state.transaction() as (db, _):
                cli.RDSState.event(db, 'REFERENCE_CONNECTION_TEST_WRITE', source='original transaction')
                retained.update(connection=db, cursor=db.cursor())
                entered.set()
                if not release_writer.wait(3):
                    raise RuntimeError('Reference transaction release timed out')
            if not bind_done.wait(3):
                raise RuntimeError('Binding did not complete after original writer commit')
            # Check on the original SQLite owner thread: thread-affinity
            # rejection must not stand in for proving the handles were closed.
            for connection in retained.values():
                with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed'):
                    connection.execute("INSERT INTO events(body) VALUES ('{}')")

        def binder():
            bind_attempting.set()
            try:
                return campaign.bind(self.store, self.workspace)
            finally:
                bind_done.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(writer)
            binding = None
            try:
                self.assertTrue(entered.wait(3), 'Real reference writer did not enter its transaction')
                binding = pool.submit(binder)
                self.assertTrue(bind_attempting.wait(3), 'Binding was never attempted')
                self.assertFalse(bind_done.wait(.1), 'Binding completed before original writer commit')
                self.assertFalse(marker.exists())
            finally:
                release_writer.set()
            writing.result(timeout=3)
            binding.result(timeout=3)
        self.assertTrue(marker.is_file())
        with self.state.snapshot() as (db, state):
            self.assertEqual(state['budget'], before_budget)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM events WHERE json_extract(body,'$.kind')='REFERENCE_CONNECTION_TEST_WRITE'"
            ).fetchone()[0], 1)
            original_events = db.execute('SELECT seq,body FROM events ORDER BY seq').fetchall()
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            with self.state.transaction() as (db, _):
                cli.RDSState.event(db, 'FORBIDDEN_POST_BIND_WRITE')
        with self.assertRaisesRegex(ValueError, 'transaction'):
            connection = self.state.connect()
            connection.close()
        with self.state.snapshot() as (db, state):
            self.assertEqual(db.execute('SELECT seq,body FROM events ORDER BY seq').fetchall(), original_events)
            self.assertEqual(state['budget'], before_budget)


if __name__ == '__main__':
    unittest.main()
