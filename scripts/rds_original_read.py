"""Bounded original-artifact reads shared by kernel and decision consumers."""
import hashlib
from pathlib import Path

from rds_project_values import require

MAX_JSON_BYTES = 2 * 1024 * 1024


def _original(store, relative):
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
            and not Path(relative).drive and ':' not in relative, 'Invalid receipt artifact path')
    path = (store.root / relative).resolve()
    require(path.is_relative_to(store.root) and path != store.root, 'Receipt artifact escapes project root')
    return path

def _read_original(store, item, *, keep=False, max_bytes=MAX_JSON_BYTES):
    path = _original(store, item['path'])
    size = path.stat().st_size
    require(type(item.get('size')) is int and size == item['size'], 'Artifact size changed: ' + item['path'])
    hasher, read_size = hashlib.sha256(), 0
    chunks = [] if keep and size <= max_bytes else None
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            read_size += len(chunk)
            require(read_size <= size, 'Artifact grew during read')
            hasher.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
    require(read_size == size, 'Artifact changed during read')
    require(hasher.hexdigest() == item['sha256'], 'Artifact hash mismatch: ' + item['path'])
    return b''.join(chunks) if chunks is not None else None
