"""Real cross-process/thread coordination; no research execution under the gate."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_mutation as locks

CHILD = '''import os,sys
sys.path.insert(0,sys.argv[1])
from rds_mutation import mutation
print('waiting',flush=True)
with mutation(timeout=float(sys.argv[2])):
    print('acquired',flush=True)
    if sys.argv[3]=='abandon': os._exit(0)
    if sys.argv[3]=='release': input()
'''


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-mutation-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def child(self, name, timeout=5, mode='finish'):
        checkout = self.root / name
        checkout.mkdir()
        shutil.copyfile(ROOT / 'scripts/rds_mutation.py', checkout / 'rds_mutation.py')
        scratch = checkout / 'different-temp'
        scratch.mkdir()
        process = subprocess.Popen([sys.executable, '-B', '-c', CHILD, str(checkout), str(timeout), mode],
            cwd=checkout, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env={**os.environ, 'TEMP': str(scratch), 'TMP': str(scratch)})
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        self.assertEqual(process.stdout.readline().strip(), 'waiting')
        return process

    def test_real_subprocess_different_checkout_and_temp_waits_for_outermost_release(self):
        entered = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with locks.mutation():
                with locks.mutation(timeout=0):
                    process = self.child('other-checkout')
                    def read_acquired():
                        line = process.stdout.readline().strip()
                        entered.set()
                        return line
                    future = pool.submit(read_acquired)
                    self.assertFalse(entered.wait(0.2))
                self.assertFalse(entered.wait(0.2))
            self.assertEqual(future.result(timeout=5), 'acquired')
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stdout + stderr)

    def test_exception_releases_real_process_lock(self):
        with self.assertRaisesRegex(RuntimeError, 'original fixture failure'):
            with locks.mutation():
                raise RuntimeError('original fixture failure')
        process = self.child('after-exception')
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(stdout.strip(), 'acquired')

    def test_threads_exclude_while_same_thread_and_decorator_can_nest(self):
        entered, waiting = threading.Event(), threading.Event()
        @locks.mutation()
        def nested():
            with locks.mutation(timeout=0):
                return 'nested'
        def other():
            waiting.set()
            with locks.mutation(timeout=3):
                entered.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with locks.mutation():
                self.assertEqual(nested(), 'nested')
                future = pool.submit(other)
                self.assertTrue(waiting.wait(1))
                self.assertFalse(entered.wait(0.2))
            future.result(timeout=3)
        self.assertTrue(entered.is_set())

    def test_real_other_process_timeout_does_not_enter_and_gate_remains_usable(self):
        with locks.mutation():
            process = self.child('timeout', timeout=0.15)
            stdout, stderr = process.communicate(timeout=5)
            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn('acquired', stdout)
            self.assertIn('TimeoutError', stderr)
        with locks.mutation(timeout=1):
            pass

    def test_acquisition_error_has_no_fallback_and_releases_thread_gate(self):
        backend = '_acquire_windows' if os.name == 'nt' else '_acquire_posix'
        entered = False
        with mock.patch.object(locks, backend, side_effect=OSError('original lock failure')):
            with self.assertRaisesRegex(OSError, 'original lock failure'):
                with locks.mutation():
                    entered = True
        self.assertFalse(entered)
        with locks.mutation(timeout=1):
            pass

    def test_invalid_timeout_rejected(self):
        for value in (True, -1, float('inf'), float('nan'), '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                with locks.mutation(timeout=value):
                    self.fail('Invalid timeout acquired a gate')

    @unittest.skipUnless(os.name == 'nt', 'Windows abandoned named mutex')
    def test_real_abandoned_windows_mutex_is_owned_and_released(self):
        kernel = locks._windows_api()
        keeper = kernel.CreateMutexW(None, False, locks._windows_name(kernel))
        self.assertTrue(keeper)
        try:
            process = self.child('abandoned', mode='abandon')
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(stdout.strip(), 'acquired')
            with locks.mutation(timeout=1):
                pass
        finally:
            self.assertTrue(kernel.CloseHandle(keeper))

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX fork state')
    def test_fork_child_does_not_inherit_python_reentrancy_or_unlock_parent(self):
        read_fd, write_fd = os.pipe()
        with locks.mutation():
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                try:
                    with locks.mutation(timeout=0.15):
                        os.write(write_fd, b'WRONG_INHERITED_LOCK')
                except TimeoutError:
                    os.write(write_fd, b'TIMEOUT')
                finally:
                    os.close(write_fd)
                os._exit(0)
            os.close(write_fd)
            self.assertEqual(os.read(read_fd, 100), b'TIMEOUT')
            os.close(read_fd)
            self.assertEqual(os.waitpid(pid, 0)[1], 0)
        with locks.mutation(timeout=1):
            pass


if __name__ == '__main__':
    unittest.main()
