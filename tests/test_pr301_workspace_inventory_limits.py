"""Incomplete binding preflight must refuse without publishing an identity."""
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_campaign as campaign
import rds_workspace as workspace
from rds_project import file_sha
import test_rds_project as fixture


class WorkspaceInventoryLimitsTests(unittest.TestCase):
    def setUp(self):
        helper = fixture.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store = helper.root, helper.store

    def dump(self):
        with self.store._db(True) as db:
            rows = list(db.iterdump())
        pointer = workspace.pointer_path(self.root)
        return rows, pointer.read_bytes() if pointer.exists() else None

    def refused_unchanged(self, reason):
        before = self.dump()
        with self.assertRaisesRegex(ValueError, reason):
            workspace.bind(self.root)
        self.assertEqual(self.dump(), before)
        self.assertFalse(workspace.pointer_path(self.root).exists())
        print('refusal preserved full ledger and absent pointer:', reason, flush=True)

    def test_directory_bound_refuses_incomplete_inventory(self):
        with patch.object(campaign, 'MAX_DIRECTORIES', 0):
            self.refused_unchanged('directory/depth bound')

    def test_entry_bound_refuses_incomplete_inventory(self):
        with patch.object(campaign, 'MAX_ENTRIES', 0):
            self.refused_unchanged('entry bound')

    def test_depth_bound_refuses_unvisited_subtree(self):
        (self.root / 'one' / 'two').mkdir(parents=True)
        with patch.object(campaign, 'MAX_DEPTH', 0):
            self.refused_unchanged('directory/depth bound')

    def test_expired_inventory_budget_refuses_before_binding(self):
        with patch.object(workspace, 'MAX_BIND_PREFLIGHT_SECONDS', 0, create=True):
            self.refused_unchanged('time budget')

    def test_oversize_ledger_refuses_before_binding(self):
        with patch.object(workspace, 'MAX_BIND_LEDGER_BYTES', 1, create=True):
            self.refused_unchanged('ledger size bound')

    def test_small_inventory_preserves_original_file_sha(self):
        (self.root / 'small' / 'nested').mkdir(parents=True)
        (self.root / 'small' / 'note.txt').write_text('small', encoding='ascii')
        original = file_sha(self.store.path)
        result = workspace.bind(self.root)
        self.assertEqual(result['status'], 'BOUND')
        self.assertEqual(result['pointer']['ledger_sha256'], original)
        self.assertEqual(workspace.bind(self.root)['status'], 'ALREADY_BOUND')

    def test_deadline_after_scandir_closes_inventory_and_refuses(self):
        now, closed = [0.0], []
        scan = workspace.os.scandir

        @contextmanager
        def slow_scan(path):
            with scan(path) as entries:
                yield entries
            now[0] = 6.0
            closed.append(path)

        with patch.object(workspace, 'monotonic', side_effect=lambda: now[0], create=True), \
                patch.object(workspace, 'MAX_BIND_PREFLIGHT_SECONDS', 5, create=True), \
                patch.object(workspace.os, 'scandir', side_effect=slow_scan):
            self.refused_unchanged('time budget')
        self.assertTrue(closed)

    def test_hash_read_uses_remaining_inventory_deadline(self):
        now, reads = [0.0], []
        scan, opened = workspace.os.scandir, Path.open

        @contextmanager
        def timed_scan(path):
            with scan(path) as entries:
                yield entries
            now[0] = 4.0

        class Reader:
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                reads.append(size)
                now[0] = 6.0
                return self.stream.read(size)

        @contextmanager
        def timed_open(path, *args, **kwargs):
            with opened(path, *args, **kwargs) as stream:
                yield Reader(stream) if path == self.store.path and args == ('rb',) else stream

        with patch.object(workspace, 'monotonic', side_effect=lambda: now[0], create=True), \
                patch.object(workspace, 'MAX_BIND_PREFLIGHT_SECONDS', 5, create=True), \
                patch.object(workspace.os, 'scandir', side_effect=timed_scan), \
                patch.object(Path, 'open', new=timed_open):
            self.refused_unchanged('time budget')
        self.assertEqual(len(reads), 1)
        self.assertLessEqual(reads[0], workspace.BIND_HASH_CHUNK_BYTES)

    def test_chunked_hash_matches_original_at_exact_size_limit(self):
        original = file_sha(self.store.path)
        with patch.object(workspace, 'MAX_BIND_LEDGER_BYTES', self.store.path.stat().st_size), \
                patch.object(workspace, 'BIND_HASH_CHUNK_BYTES', 127):
            result = workspace.bind(self.root)
        self.assertEqual(result['pointer']['ledger_sha256'], original)

    def test_hash_growth_after_initial_size_check_is_refused(self):
        opened = Path.open
        sizes = []

        class GrowingReader:
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                sizes.append(size)
                return self.stream.read(size) or b'x'

        @contextmanager
        def growing_open(path, *args, **kwargs):
            with opened(path, *args, **kwargs) as stream:
                yield GrowingReader(stream) if path == self.store.path and args == ('rb',) else stream

        with patch.object(workspace, 'MAX_BIND_LEDGER_BYTES', self.store.path.stat().st_size), \
                patch.object(workspace, 'BIND_HASH_CHUNK_BYTES', 4096), \
                patch.object(Path, 'open', new=growing_open):
            self.refused_unchanged('ledger size bound')
        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), 4096)
        self.assertEqual(sizes[-1], 1)

    def test_unreadable_directory_refuses_without_publication(self):
        before = self.dump()
        with patch.object(workspace.os, 'scandir', side_effect=PermissionError('unreadable subtree')):
            with self.assertRaisesRegex(PermissionError, 'unreadable subtree'):
                workspace.bind(self.root)
        self.assertEqual(self.dump(), before)
        self.assertFalse(workspace.pointer_path(self.root).exists())


if __name__ == '__main__':
    unittest.main()
