import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_project import _Job, _WindowsPopen


@unittest.skipUnless(os.name == 'nt', 'Windows owned process handles')
class WindowsStdioGuardTests(unittest.TestCase):
    def launch(self):
        return _WindowsPopen(
            [sys.executable, '-B', '-c', 'print("stdio-ok")'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=0x4 | subprocess.CREATE_NO_WINDOW,
        )

    def test_invalid_stream_is_rejected_before_filter_or_child_creation(self):
        for slot in (0, 3, 5):
            for invalid in (-1, 0):
                with self.subTest(slot=slot, invalid=invalid):
                    handles = [4, -1, -1, 8, -1, 12]
                    handles[slot] = invalid
                    with patch.object(_WindowsPopen, '_get_handles', return_value=tuple(handles)), \
                            patch.object(_WindowsPopen, '_close_pipe_fds'), \
                            patch.object(_WindowsPopen, '_filter_handle_list') as filtered:
                        with self.assertRaisesRegex(ValueError, 'requires all standard streams'):
                            self.launch()
                    filtered.assert_not_called()

    def test_filter_oserror_is_reported_as_owned_launch_validation_error(self):
        with patch.object(_WindowsPopen, '_get_handles', return_value=(4, -1, -1, 8, -1, 12)), \
                patch.object(_WindowsPopen, '_close_pipe_fds'), \
                patch.object(_WindowsPopen, '_filter_handle_list', side_effect=OSError('invalid handle')):
            with self.assertRaisesRegex(ValueError, 'standard stream handles') as raised:
                self.launch()
        self.assertIsInstance(raised.exception.__cause__, OSError)

    def test_valid_owned_streams_still_launch_and_capture(self):
        process = self.launch()
        job = _Job(process)
        try:
            job.resume()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), b'stdio-ok')
            self.assertEqual(stderr, b'')
        finally:
            job.stop()
            job.close()


if __name__ == '__main__':
    unittest.main()
