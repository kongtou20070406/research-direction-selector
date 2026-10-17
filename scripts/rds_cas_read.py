"""Bounded original CAS reads, independent of project/controller construction."""
import hashlib
from pathlib import Path
import re

from rds_project_values import require

MAX_BYTES = 8 * 1024 * 1024


def read_bytes(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, 'Research asset exceeds 8 MiB')
    return raw


def blob(root, ref, *, reader=read_bytes):
    require(isinstance(ref, dict) and isinstance(ref.get('sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', ref['sha256']), 'Invalid research blob binding')
    base = Path(root).resolve()
    path = (base / ref['path']).resolve()
    require(path.parent == (base / '.rds' / 'cas').resolve()
            and path.name == ref['sha256'] + '.bin', 'Research blob is outside its CAS')
    raw = reader(path)
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'] and len(raw) == ref['bytes'],
            'Research blob integrity failure')
    return raw
