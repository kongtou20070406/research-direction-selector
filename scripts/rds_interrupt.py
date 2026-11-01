"""Turn an outer harness's SIGTERM into a settled controller exit (#215).

A drive loop that is terminated mid-pass must still run its ``finally`` block:
release the controller reservation, charge measured overhead and record which
work was in flight. SIGKILL cannot be intercepted; every drive step is an
append-only ledger write, so the next pass recovers the original attempt.

Windows has no deliverable SIGTERM: ``terminate()`` and ``send_signal(SIGTERM)``
are ``TerminateProcess``, as final as SIGKILL. A Windows harness asks for a
settled stop with ``CTRL_BREAK_EVENT``, which arrives as SIGBREAK.
"""
import signal


class OuterInterrupt(BaseException):
    """An outer termination request; BaseException so ordinary handlers do not swallow it."""


def install(names=('SIGTERM', 'SIGBREAK')):
    previous = {}

    def handler(signum, frame):
        raise OuterInterrupt(signal.Signals(signum).name)
    for name in names:
        if hasattr(signal, name):
            sig = getattr(signal, name)
            try:
                previous[sig] = signal.signal(sig, handler)
            except ValueError:
                pass  # Not the main thread: the embedding host owns signals.
    return previous


def restore(previous):
    for sig, old in previous.items():
        signal.signal(sig, old)
