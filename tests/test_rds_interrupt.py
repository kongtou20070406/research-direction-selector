"""An outer stop request must surface as OuterInterrupt and leave the signal table as it was."""
from pathlib import Path
import signal
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_interrupt as interrupt


class InterruptTests(unittest.TestCase):
    def test_outer_interrupt_is_not_an_ordinary_exception(self):
        # `except Exception` in a drive step must not swallow the stop request.
        self.assertTrue(issubclass(interrupt.OuterInterrupt, BaseException))
        self.assertFalse(issubclass(interrupt.OuterInterrupt, Exception))

    def test_installed_handler_raises_and_restore_puts_back_the_previous_one(self):
        before = signal.getsignal(signal.SIGTERM)
        previous = interrupt.install()
        try:
            self.assertIn(signal.SIGTERM, previous)
            self.assertEqual(previous[signal.SIGTERM], before)
            handler = signal.getsignal(signal.SIGTERM)
            self.assertNotEqual(handler, before)
            with self.assertRaises(interrupt.OuterInterrupt) as caught:
                handler(signal.SIGTERM, None)
            self.assertEqual(caught.exception.args, ('SIGTERM',))
        finally:
            interrupt.restore(previous)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    @unittest.skipUnless(hasattr(signal, 'SIGBREAK'), 'SIGBREAK exists only on Windows')
    def test_windows_settled_stop_arrives_as_sigbreak(self):
        previous = interrupt.install()
        try:
            with self.assertRaises(interrupt.OuterInterrupt) as caught:
                signal.getsignal(signal.SIGBREAK)(signal.SIGBREAK, None)
            self.assertEqual(caught.exception.args, ('SIGBREAK',))
        finally:
            interrupt.restore(previous)

    @unittest.skipIf(sys.platform == 'win32', 'Windows SIGTERM is TerminateProcess, not a signal')
    def test_a_delivered_sigterm_reaches_the_caller(self):
        previous = interrupt.install(('SIGTERM',))
        try:
            with self.assertRaises(interrupt.OuterInterrupt):
                signal.raise_signal(signal.SIGTERM)
        finally:
            interrupt.restore(previous)

    def test_off_the_main_thread_the_host_keeps_its_signals(self):
        before = signal.getsignal(signal.SIGTERM)
        result = []
        thread = threading.Thread(target=lambda: result.append(interrupt.install()))
        thread.start()
        thread.join(10)
        self.assertEqual(result, [{}])
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_unknown_signal_names_are_skipped(self):
        self.assertEqual(interrupt.install(('NOT_A_SIGNAL',)), {})


if __name__ == '__main__':
    unittest.main()
