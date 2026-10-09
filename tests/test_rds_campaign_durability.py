"""Publication ordering and OS error controls; no simulated power-loss claim."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_campaign as campaign
import test_rds_campaign_binding as fixtures


class CampaignDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CampaignBindingTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.marker = self.fixture.marker

    def test_native_publication_and_same_identity_retry_never_overwrite(self):
        before = self.fixture.store.snapshot()
        first = campaign.bind(self.fixture.store, self.fixture.workspace)
        original = self.marker.read_bytes()
        self.assertEqual(campaign.bind(self.fixture.store, self.fixture.workspace), first)
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertEqual(len(self.fixture.events()), 1)
        for key in ('runs', 'receipts', 'budget', 'contract_sha256'):
            self.assertEqual(self.fixture.store.snapshot()[key], before[key])
        incompatible = {k: v for k, v in first.items() if k != 'binding_path'}
        incompatible['binding_id'] = 'another-identity'
        with self.assertRaises(ValueError):
            campaign._publish(self.marker, incompatible)
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertFalse(list(self.fixture.workspace.glob('.rds-campaign-*')))

    def test_file_flush_precedes_publication_and_cleanup_precedes_directory_sync(self):
        events = []
        original_fsync = campaign.os.fsync
        original_link = campaign.os.link
        original_unlink = Path.unlink
        def flush(fd):
            events.append('file_flush')
            return original_fsync(fd)
        def link(source, destination):
            self.assertEqual(events, ['file_flush'])
            events.append('publish')
            return original_link(source, destination)
        def unlink(path, *args, **kwargs):
            if path.name.startswith('.rds-campaign-'):
                events.append('remove_temporary')
            return original_unlink(path, *args, **kwargs)
        def sync(directory):
            self.assertEqual(directory, self.fixture.workspace)
            self.assertTrue(self.marker.is_file())
            self.assertFalse(list(directory.glob('.rds-campaign-*')))
            events.append('directory_flush')
        # Exercise POSIX ordering on every host without pretending a mocked
        # Windows directory descriptor is an actual POSIX filesystem flush.
        with patch.object(campaign, 'WINDOWS', False), \
                patch.object(campaign.os, 'O_DIRECTORY', 0, create=True), \
                patch.object(campaign.os, 'fsync', flush), \
                patch.object(campaign.os, 'link', link), \
                patch.object(Path, 'unlink', unlink), \
                patch.object(campaign, '_sync_directory', sync):
            campaign.bind(self.fixture.store, self.fixture.workspace)
        self.assertEqual(events, ['file_flush', 'publish', 'remove_temporary', 'directory_flush'])

    def test_directory_flush_failure_retains_committed_identity_for_retry(self):
        with patch.object(campaign, 'WINDOWS', False), \
                patch.object(campaign.os, 'O_DIRECTORY', 0, create=True), \
                patch.object(campaign, '_sync_directory', side_effect=OSError('directory flush refused')):
            with self.assertRaisesRegex(OSError, 'directory flush refused'):
                campaign.bind(self.fixture.store, self.fixture.workspace)
        original = self.marker.read_bytes()
        self.assertEqual(len(self.fixture.events()), 1)
        campaign.bind(self.fixture.store, self.fixture.workspace)
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertEqual(len(self.fixture.events()), 1)
        self.assertFalse(list(self.fixture.workspace.glob('.rds-campaign-*')))

    def test_directory_descriptor_is_closed_even_when_sync_fails(self):
        with patch.object(campaign.os, 'O_DIRECTORY', 0, create=True), \
                patch.object(campaign.os, 'open', return_value=123) as opened, \
                patch.object(campaign.os, 'fsync', side_effect=OSError('flush refused')), \
                patch.object(campaign.os, 'close') as closed:
            with self.assertRaisesRegex(OSError, 'flush refused'):
                campaign._sync_directory(self.fixture.workspace)
        opened.assert_called_once_with(self.fixture.workspace, campaign.os.O_RDONLY)
        closed.assert_called_once_with(123)

    def test_unsupported_platform_refuses_before_publication(self):
        with patch.object(campaign, 'WINDOWS', False), \
                patch.object(campaign.os, 'O_DIRECTORY', create=True):
            del campaign.os.O_DIRECTORY
            with self.assertRaisesRegex(ValueError, 'unsupported on this platform'):
                campaign._publish(self.marker, {})
        self.assertFalse(self.marker.exists())

    def test_windows_write_through_uses_only_no_replace_flag_and_preserves_error(self):
        import ctypes
        class Move:
            def __call__(self, source, destination, flags):
                self.arguments = (source, destination, flags)
                return False
        move = Move()
        class Library:
            MoveFileExW = move
        with patch.object(ctypes, 'WinDLL', return_value=Library(), create=True) as library, \
                patch.object(ctypes, 'get_last_error', return_value=183, create=True):
            with self.assertRaises(FileExistsError):
                campaign._move_write_through(self.marker.with_suffix('.tmp'), self.marker)
        library.assert_called_once_with('kernel32', use_last_error=True)
        self.assertEqual(move.arguments[2], 8)
        self.assertEqual(len(move.argtypes), 3)
        error = OSError('Windows access denied')
        error.winerror = 5
        with patch.object(ctypes, 'WinDLL', return_value=Library(), create=True), \
                patch.object(ctypes, 'get_last_error', return_value=5, create=True), \
                patch.object(ctypes, 'WinError', return_value=error, create=True):
            with self.assertRaises(OSError) as refused:
                campaign._move_write_through(self.marker.with_suffix('.tmp'), self.marker)
        self.assertEqual(refused.exception.winerror, 5)


if __name__ == '__main__':
    unittest.main()
