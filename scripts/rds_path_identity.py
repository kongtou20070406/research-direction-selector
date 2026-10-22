"""Spelling-independent path identity for root and ledger guards (#207).

On Windows one directory can be spelled several ways: 8.3 short names
(``RUNNER~1``), drive-letter and component case, and ``\\\\?\\`` prefixes.
``Path.resolve()`` canonicalizes existing components but a stored string may
still carry an alias. Guards compare identities, never raw spellings. On POSIX
the identity is exactly ``str(Path(p).resolve())``, so behavior is unchanged.
"""
import os
from pathlib import Path


def _long(text):
    """Expand Windows short-name aliases of an existing path; other hosts unchanged."""
    if os.name != 'nt':
        return text
    import ctypes
    from ctypes import wintypes
    function = ctypes.windll.kernel32.GetLongPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    size = function(text, None, 0)
    if size == 0:
        return text
    buffer = ctypes.create_unicode_buffer(size)
    return buffer.value if function(text, buffer, size) else text


def _strip(text):
    if text.startswith('\\\\?\\UNC\\'):
        return '\\\\' + text[8:]
    if text.startswith('\\\\?\\'):
        return text[4:]
    return text


def _expand(text):
    """Expand aliases on the deepest existing ancestor; keep a not-yet-created tail."""
    probe, tail = Path(text), []
    while not probe.exists() and probe != probe.parent:
        tail.append(probe.name)
        probe = probe.parent
    return str(Path(_long(str(probe)), *reversed(tail)))


def identity(path):
    """Resolved, alias-expanded, case-normalized spelling used for equality only."""
    return os.path.normcase(_expand(_strip(str(Path(path).resolve()))))


def same(left, right):
    if not isinstance(left, (str, os.PathLike)) or not isinstance(right, (str, os.PathLike)):
        return False
    try:
        if os.path.exists(left) and os.path.exists(right):
            return os.path.samefile(left, right)
    except OSError:
        pass
    return identity(left) == identity(right)


def canonical_spelling(value):
    """True when a stored absolute path is canonical up to short-name aliases and case."""
    return identity(value) == os.path.normcase(_expand(_strip(str(value))))
