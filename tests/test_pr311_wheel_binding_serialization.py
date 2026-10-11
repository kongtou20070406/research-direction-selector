"""Wheel file publication must not outlive its workspace admission."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_mutation
import rds_wheel
import rds_workspace as workspace
from rds_project import ProjectStore
import test_rds_project
from test_rds_wheel import fixture


class WheelBindingSerializationTests(unittest.TestCase):
    def setUp(self):
        parent = test_rds_project.ProjectTests()
        parent.setUp()
        self.addCleanup(parent.tearDown)
        temporary = tempfile.TemporaryDirectory(prefix='rds-wheel-bind-race-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.wheel = fixture.prepare(self.root / 'wheel-child', factors=['flat'])
        fixture.bootstrap(self.wheel)
        self.assertEqual(self.wheel.tick(), 0)
        self.assertEqual(self.wheel.tick(), 0)
        for binding in parent.contract['bindings']:
            shutil.copyfile(parent.root / binding['path'], self.root / binding['path'])
        ProjectStore(self.root).initialize(parent.contract)

    def files(self):
        return {str(path.relative_to(self.wheel.root)): path.read_bytes()
                for path in self.wheel.directory.rglob('*') if path.is_file()}

    def test_propose_rechecks_admission_before_final_append(self):
        gate = self.wheel.proposal_gate

        def bind_after_gate():
            state = gate()
            workspace.bind(self.root)
            return state

        before = self.files()
        with patch.object(self.wheel, 'proposal_gate', side_effect=bind_after_gate):
            with self.assertRaises(rds_wheel.WheelError):
                self.wheel.propose('researcher', 'screen_change')
        self.assertEqual(self.files(), before)

    def test_tick_rechecks_admission_after_readonly_status(self):
        project = self.wheel.project

        def bind_after_status(action, *args):
            result = project(action, *args)
            if action == 'status':
                workspace.bind(self.root)
            return result

        before = self.files()
        with patch.object(self.wheel, 'project', side_effect=bind_after_status):
            with self.assertRaises(rds_wheel.WheelError):
                self.wheel.tick()
        self.assertEqual(self.files(), before)

    def test_append_holds_mutation_until_filesystem_write_finishes(self):
        published, release, attempted = (threading.Event() for _ in range(3))
        fsync = os.fsync
        inbox = self.wheel.state_path('inbox.jsonl')

        def pause(fd):
            fsync(fd)
            if os.path.samestat(os.fstat(fd), inbox.stat()):
                published.set()
                if not release.wait(3):
                    raise RuntimeError('Wheel publication release deadline exceeded')

        def bind():
            attempted.set()
            return workspace.bind(self.root)

        with patch.object(rds_wheel.os, 'fsync', side_effect=pause), ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(self.wheel.propose, 'researcher', 'screen_change')
            try:
                self.assertTrue(published.wait(3))
                acquired = rds_mutation._gate.acquire(blocking=False)
                if acquired:
                    rds_mutation._gate.release()
                self.assertFalse(acquired, 'Filesystem publication must retain the mutation gate')
                binding = pool.submit(bind)
                self.assertTrue(attempted.wait(3))
                self.assertFalse(binding.done())
            finally:
                release.set()
            self.assertTrue(writing.result(timeout=5))
            self.assertEqual(binding.result(timeout=5)['status'], 'BOUND')
        with self.assertRaises(rds_wheel.WheelError):
            self.wheel.propose('researcher', 'screen_change')
