"""Pure leaf runtime fingerprint, protocol identity and display-command helpers.

Stdlib/rds_project_values-only leaf module so neither rds_project (L1) nor
rds_feasibility (L3) needs an upward or cyclic import for runtime identity or
shell display formatting.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shlex

from rds_project_values import canonical, digest, number, require

IDENTITY = ("code_sha256", "config_sha256", "data_sha256", "data_split",
            "init", "seed", "checkpoint", "schedule", "sample_work", "numeric_protocol")

__all__ = ["IDENTITY", "_shell_argument", "canonical", "digest", "file_sha",
           "load_json", "number", "require", "runtime_fingerprint"]


def runtime_fingerprint():
    return digest({'system': platform.system(), 'release': platform.release(),
                   'machine': platform.machine(), 'processor': platform.processor(),
                   'python': platform.python_version()})


def _shell_argument(value):
    """Render one display-command argument for PowerShell on Windows, POSIX elsewhere."""
    text = str(value)
    if os.name != 'nt':
        return shlex.quote(text)
    if text and all(c.isascii() and (c.isalnum() or c in '-_./:') for c in text):
        return text
    # PowerShell also recognizes typographic single quotes as delimiters.
    return "'" + text.replace("'", "''").replace('‘', '‘‘').replace('’', '’’') + "'"


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path, *, max_bytes=None, expected_sha256=None):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    if max_bytes is None and expected_sha256 is None:
        source = Path(path).read_text(encoding='utf-8')
    else:
        with Path(path).open('rb') as stream:
            raw = stream.read(max_bytes + 1) if max_bytes is not None else stream.read()
        require(max_bytes is None or len(raw) <= max_bytes, 'Maintenance context exceeds byte limit')
        require(expected_sha256 is None or hashlib.sha256(raw).hexdigest() == expected_sha256,
                'Maintenance context binding changed')
        source = raw.decode('utf-8')
    return json.loads(source, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
