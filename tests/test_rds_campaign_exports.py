"""Binding waits for real filesystem-only export tails, then forbids refresh."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_rds_project as project_fixture
import test_rds_owned_tools as application_fixture
import test_rds_tool_workbench as workbench_fixture
import rds_campaign as campaign
from rds_project import ProjectStore
import rds_tool_application as application
import rds_tool_workbench as workbench
import rds_tools as tools


class CampaignExportTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(campaign.ENVIRONMENT, None)
        self.f = project_fixture.ProjectTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        temporary = tempfile.TemporaryDirectory(prefix='rds-export-workspace-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        canonical = self.root / 'canonical'
        canonical.mkdir()
        for binding in self.f.contract['bindings']:
            shutil.copyfile(self.f.root / binding['path'], canonical / binding['path'])
        self.store = ProjectStore(canonical)
        self.store.initialize(self.f.contract)
        self.child = self.root / 'sibling'
        self.child.mkdir()

    def race(self, operation, module, attribute, paths, *, pause_after=False):
        entered, release, started, finished = [threading.Event() for _ in range(4)]
        original = getattr(module, attribute)

        def pause(*args, **kwargs):
            result = original(*args, **kwargs) if pause_after else None
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Export fixture release timed out')
            return result if pause_after else original(*args, **kwargs)

        def bind():
            started.set()
            return campaign.bind(self.store, self.root)

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(module, attribute, pause):
            writing = pool.submit(operation)
            binding = None
            try:
                self.assertTrue(entered.wait(4), 'Real export did not reach its filesystem tail')
                binding = pool.submit(bind)
                binding.add_done_callback(lambda _: finished.set())
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.15), 'Binding overtook a filesystem export')
                self.assertFalse((self.root / campaign.MARKER).exists())
            finally:
                release.set()
            writing.result(timeout=5)
            binding.result(timeout=5)
        before = {str(path): path.read_bytes() for path in paths}
        self.assertTrue((self.root / campaign.MARKER).exists())
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            operation()
        self.assertEqual({str(path): path.read_bytes() for path in paths}, before)

    def test_application_export_tail_blocks_binding_and_cannot_write_after_it(self):
        fixture = application_fixture.OwnedToolsCLITests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.root = self.child
        fixture.store = ProjectStore(self.child)
        fixture.env['RDS_USAGE_DB'] = str(self.child / 'usage.sqlite3')
        fixture.setup_campaign(initialize=False)
        # Repeat only export, using the original native qualification/receipt.
        paths = [self.child / name for name in ('apply.py', 'request.json')]
        for path in paths:
            path.unlink()
        args = SimpleNamespace(root=self.child, name='squares', inputs='inputs.json', cases='cases.json',
            code_path='qualified.py', driver='apply.py', request='request.json', output='outputs/application.json',
            decision=self.child / 'decision.json', action_file=self.child / 'action.json', candidate='apply',
            run_id='apply', obligation='task.status', observation_fact=['task.status'])
        self.race(lambda: application.prepare(args), tools, 'command', paths, pause_after=True)
        self.assertFalse((self.child / 'outputs/application.json').exists())

    def test_workbench_refresh_tail_blocks_binding_and_cannot_write_after_it(self):
        contract = workbench_fixture.fixture.prepare(self.child)
        store = ProjectStore(self.child)
        store.initialize(contract)
        operation = lambda: workbench.prepare(store, 'solver.py', 'improve')
        first = operation()
        source = Path(first['candidate_source'])
        source.write_bytes(source.read_bytes().replace(b'sum(range(100))', b'sum(i for i in range(100))'))
        self.race(operation, workbench.tempfile, 'mkstemp', [Path(first['proposal'])])
        self.assertEqual(store.snapshot()['runs'], [])


if __name__ == '__main__':
    unittest.main()
