"""Guarded CAS publication, shared by kernel services and entry points."""
import hashlib
from pathlib import Path
import re

from rds_project import file_sha
from rds_project_values import canonical, require
from rds_mutation import mutation


def cas_json(root, value, *, max_bytes=None):
    require(max_bytes is None or type(max_bytes) is int and max_bytes > 0,
            'Invalid CAS JSON publication byte limit')
    raw = canonical(value).encode('utf-8')
    require(max_bytes is None or len(raw) <= max_bytes, 'CAS JSON exceeds its publication byte limit')
    return cas_bytes(root, raw, 'json')



@mutation()
def cas_bytes(root, raw, suffix='bin'):
    from rds_campaign import enforce
    enforce(root)
    from rds_workspace import check_admission
    check_admission(root)
    require(isinstance(raw, bytes) and re.fullmatch(r'[a-z0-9]{1,12}', suffix), 'Invalid CAS bytes or suffix')
    sha = hashlib.sha256(raw).hexdigest()
    directory = Path(root).resolve() / '.rds' / 'cas'
    require(directory.resolve().is_relative_to(Path(root).resolve()), 'CAS escapes project root')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (sha + '.' + suffix)
    try:
        with path.open('xb') as handle:
            handle.write(raw)
    except FileExistsError:
        require(file_sha(path) == sha, 'CAS integrity failure')
    return {'sha256': sha, 'path': str(path), 'bytes': len(raw)}
