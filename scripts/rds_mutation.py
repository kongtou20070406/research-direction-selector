"""Serialize short RDS mutations across processes; never hold during execution.

This is a coordination primitive, not a research ledger or a host sandbox.
The lock identity does not depend on the checkout, cwd, or temporary directory.
Acquisition failures are reported; there is no process-local fallback.
"""
from contextlib import contextmanager
import math
import os
import stat
import threading
import time

_gate = threading.RLock()
_local = threading.local()
_pid = os.getpid()
_posix_fd = None


def _after_fork():
    global _gate, _local, _pid, _posix_fd
    # flock belongs to the shared open-file description. Close the child's
    # copy without LOCK_UN, which would also unlock the parent's description.
    if _posix_fd is not None:
        os.close(_posix_fd)
    _posix_fd = None
    _gate = threading.RLock()
    _local = threading.local()
    _pid = os.getpid()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


def _windows_api():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return kernel


def _windows_name(kernel):
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    api.OpenProcessToken.restype = wintypes.BOOL
    api.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetTokenInformation.restype = wintypes.BOOL
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [('sid', ctypes.c_void_p), ('attributes', wintypes.DWORD)]

    token = wintypes.HANDLE()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        if api.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed)) \
                or ctypes.get_last_error() != 122 or needed.value == 0:
            raise OSError('Cannot determine the Windows user token size')
        raw = ctypes.create_string_buffer(needed.value)
        if not api.GetTokenInformation(token, 1, raw, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(raw, ctypes.POINTER(SidAndAttributes)).contents.sid
        text = wintypes.LPWSTR()
        if not api.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return 'Global\\RDS-Mutation-v1-' + text.value
        finally:
            if kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p)):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not kernel.CloseHandle(token):
            raise ctypes.WinError(ctypes.get_last_error())


def _acquire_windows(deadline):
    import ctypes
    kernel = _windows_api()
    handle = kernel.CreateMutexW(None, False, _windows_name(kernel))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        remaining = max(0.0, deadline - time.monotonic())
        milliseconds = min(0xfffffffe, math.ceil(remaining * 1000))
        result = kernel.WaitForSingleObject(handle, milliseconds)
        if result in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED: both own it.
            return kernel, handle
        if result == 0x102:
            raise TimeoutError('Timed out waiting for the RDS mutation mutex')
        if result == 0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        raise OSError('Unexpected Windows mutation wait result: ' + str(result))
    except BaseException:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        raise


def _release_windows(owned):
    import ctypes
    kernel, handle = owned
    try:
        if not kernel.ReleaseMutex(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _validate_posix(fd, path):
    opened, named = os.fstat(fd), os.lstat(path)
    if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid() \
            or opened.st_nlink != 1 or opened.st_mode & 0o022 \
            or not stat.S_ISREG(named.st_mode) \
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise OSError('Unsafe RDS mutation lock file')


def _acquire_posix(deadline):
    import errno
    import fcntl
    global _posix_fd
    if not hasattr(os, 'O_NOFOLLOW'):
        raise OSError('This platform cannot safely open the RDS mutation lock')
    path = '/tmp/rds-mutation-v1-' + str(os.getuid()) + '.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    _posix_fd = fd
    try:
        _validate_posix(fd, path)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EINTR):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Timed out waiting for the RDS mutation flock') from exc
                time.sleep(min(0.05, remaining))
        _validate_posix(fd, path)
        return fd
    except BaseException:
        os.close(fd)
        _posix_fd = None
        raise


def _release_posix(fd):
    import fcntl
    global _posix_fd
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        _posix_fd = None


@contextmanager
def mutation(timeout=60.0):
    """Hold a short publication/mutation gate; nest only on the same thread.

    The timeout covers waiting for both thread and OS locks. No child launch,
    experiment execution, or external/model work should occur in this scope.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
            or not math.isfinite(timeout) or timeout < 0:
        raise ValueError('Mutation timeout must be a finite nonnegative number')
    if os.getpid() != _pid:
        _after_fork()
    deadline = time.monotonic() + timeout
    if not _gate.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError('Timed out waiting for the RDS mutation thread lock')
    owner_pid = os.getpid()
    owned = None
    depth = getattr(_local, 'depth', 0)
    try:
        if depth == 0:
            owned = _acquire_windows(deadline) if os.name == 'nt' else _acquire_posix(deadline)
        _local.depth = depth + 1
        try:
            yield
        finally:
            if os.getpid() == owner_pid:
                _local.depth = depth
    finally:
        if os.getpid() == owner_pid:
            try:
                if owned is not None:
                    _release_windows(owned) if os.name == 'nt' else _release_posix(owned)
            finally:
                _gate.release()
