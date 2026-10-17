"""Native prepared admission data and locked ledger/CAS recheck.

This is a conservative read fence, not execution authorization. It does not
open connections, commit, run policy, or deserialize tokens from user JSON.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from rds_project_values import canonical, digest, require

MAX_ROW_BYTES = 8 * 1024 * 1024
MAX_ROWS = 262144
MAX_FENCE_BYTES = 256 * 1024 * 1024


class StaleAdmission(ValueError):
    status = 'STALE'


@dataclass(frozen=True, slots=True)
class FileIdentity:
    relative: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class PreparedAdmission:
    schema_version: int
    ledger_path: str
    manifest_sha256: str
    ledger_sha256: str
    files: tuple[FileIdentity, ...]


def ledger_fence(db):
    """Stream a bounded exact data/schema fingerprint on the borrowed snapshot.

    All application tables are conservatively fenced. Even an unrelated write
    yields STALE rather than silently weakening a prevalidated policy result.
    """
    require(db.in_transaction, 'Admission fence needs an active transaction')
    hasher = hashlib.sha256()
    total = count = 0
    cursor = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' "
                        "AND name NOT GLOB 'sqlite_*' ORDER BY name")
    try:
        tables = cursor.fetchall()
    finally:
        cursor.close()
    for name, schema in tables:
        header = canonical([name, schema]).encode('utf-8')
        hasher.update(len(header).to_bytes(8, 'big'))
        hasher.update(header)
        # Identifiers are SQLite schema data, not caller-built SQL fragments.
        quoted = '"' + name.replace('"', '""') + '"'
        metadata = db.execute('PRAGMA table_info(' + quoted + ')')
        try:
            columns = tuple(row[1] for row in metadata)
        finally:
            metadata.close()
        sizes = ' + '.join('coalesce(length(CAST("' + column.replace('"', '""') +
                           '" AS BLOB)),0)' for column in columns)
        stats = db.execute('SELECT count(*),coalesce(max(' + sizes +
                           '),0),coalesce(sum(' + sizes + '),0) FROM ' + quoted)
        try:
            n, maximum, size = stats.fetchone()
        finally:
            stats.close()
        require(count + n <= MAX_ROWS and maximum <= MAX_ROW_BYTES
                and total + size <= MAX_FENCE_BYTES, 'Admission fence coverage exceeds its bounds')
        rows = db.execute('SELECT * FROM ' + quoted + ' ORDER BY rowid')
        try:
            for row in rows:
                values = [[type(v).__name__, v.hex() if isinstance(v, bytes) else v] for v in row]
                raw = canonical(values).encode('utf-8')
                count += 1
                total += len(raw)
                require(len(raw) <= MAX_ROW_BYTES and count <= MAX_ROWS
                        and total <= MAX_FENCE_BYTES, 'Admission fence coverage exceeds its bounds')
                hasher.update(len(raw).to_bytes(8, 'big'))
                hasher.update(raw)
        finally:
            rows.close()
    return hasher.hexdigest()


def _path(root, relative):
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
            and not Path(relative).drive and ':' not in relative,
            'Invalid receipt artifact path')
    base = Path(root).resolve()
    path = (base / relative).resolve()
    require(path.is_relative_to(base) and path != base, 'Receipt artifact escapes project root')
    return path


def check_files(root, files):
    """Verify original bytes in chunks; no nested ledger or upper-layer hook."""
    for item in files:
        require(type(item) is FileIdentity and type(item.size) is int and item.size >= 0
                and re.fullmatch('[0-9a-f]{64}', item.sha256), 'Invalid native file identity')
        path = _path(root, item.relative)
        require(path.stat().st_size == item.size, 'Artifact size changed: ' + item.relative)
        hasher, read_size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                read_size += len(chunk)
                require(read_size <= item.size, 'Artifact grew during read')
                hasher.update(chunk)
        require(read_size == item.size, 'Artifact changed during read')
        require(hasher.hexdigest() == item.sha256, 'Artifact hash mismatch: ' + item.relative)


def prepare(db, ledger_path, spec, evidence_files, snapshot):
    """Freeze data only after the original upper policy has checked this snapshot."""
    files = tuple(FileIdentity(item['path'], item['sha256'], item['size'])
                  for item in evidence_files)
    if snapshot is not None:
        ref = snapshot['map']
        base = Path(ledger_path).resolve().parent.parent
        path = _path(base, ref['path'])
        require(path.parent == (base / '.rds/cas').resolve()
                and path.name == ref['sha256'] + '.bin', 'Research blob is outside its CAS')
        files += (FileIdentity(ref['path'], ref['sha256'], ref['bytes']),)
    return PreparedAdmission(1, str(Path(ledger_path).resolve()), digest(spec), ledger_fence(db), files)


def check_locked(db, ledger_path, root, spec, packet):
    """Reject stale prevalidation, then verify CAS. Caller retains original budget checks."""
    require(type(packet) is PreparedAdmission and packet.schema_version == 1,
            'Native prepared admission is missing; user JSON is not admission')
    require(db.in_transaction, 'Admission recheck needs the caller transaction')
    path = str(Path(ledger_path).resolve())
    main = next((row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main'), '')
    require(main and str(Path(main).resolve()) == path == packet.ledger_path,
            'Admission is bound to another ledger')
    require(digest(spec) == packet.manifest_sha256, 'Owned admission manifest binding mismatch')
    if ledger_fence(db) != packet.ledger_sha256:
        raise StaleAdmission('Owned state changed after Advisor selection; STALE; retry admission')
    check_files(root, packet.files)
    return True
