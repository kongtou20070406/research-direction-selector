"""Native QUICK preparation data and ledger/evidence recheck; no policy calls."""
from dataclasses import dataclass
import hashlib
from pathlib import Path

from rds_admission_read import PreparedAdmission, StaleAdmission, check_locked
from rds_project_values import digest, require


@dataclass(frozen=True, slots=True)
class PreparedQuickChoice:
    admission: PreparedAdmission
    decision_json: str
    context_sha256: str
    candidate_id: str
    dependency_sha256: str | None
    file_requests: tuple[str, ...]
    receipt_requests: tuple[tuple[str, str], ...]
    source_bytes: int
    evidence_sha256: str


def evidence_fence(db, root, files, receipts, source_bytes):
    """Fingerprint the originals policy read, including unavailable evidence."""
    import sqlite3
    from rds_sqlite import readonly_connection
    rows, remaining = [], source_bytes
    for text in files:
        path = Path(text)
        try:
            resolved, size = str(path.resolve()), path.stat().st_size
            if size > remaining:
                rows.append((text, resolved, size, 'BYTE_LIMIT_EXCEEDED'))
                continue
            hasher, count = hashlib.sha256(), 0
            with path.open('rb') as stream:
                while chunk := stream.read(min(1024 * 1024, remaining - count + 1)):
                    count += len(chunk)
                    require(count <= remaining, 'QUICK evidence exceeds its frozen read bound')
                    hasher.update(chunk)
            rows.append((text, resolved, count, hasher.hexdigest()))
            remaining -= count
        except OSError as exc:
            rows.append((text, type(exc).__name__))
    for owner, sha in receipts:
        owner_root = Path(owner).resolve()
        require(owner_root.is_dir(), 'Project root must exist')
        require((owner_root / '.rds').resolve().is_relative_to(owner_root),
                'State directory escapes project root')
        ledger = owner_root / '.rds/project.sqlite3'
        def read(connection):
            cursor = connection.execute('SELECT run_id,sha256,body FROM receipts WHERE sha256=? LIMIT 2', (sha,))
            try:
                found = cursor.fetchall()
            finally:
                cursor.close()
            return digest([[[type(v).__name__, v.hex() if isinstance(v, bytes) else v]
                            for v in row] for row in found])
        try:
            if owner_root == Path(root).resolve():
                identity = read(db)
            else:
                if not ledger.is_file():
                    raise FileNotFoundError('Project contract has not been initialized')
                require(ledger.resolve().is_relative_to(owner_root), 'Project database escapes root')
                with readonly_connection(ledger) as other:
                    identity = read(other)
            rows.append((owner, sha, str(ledger.resolve()), identity))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            rows.append((owner, sha, type(exc).__name__))
    return digest(rows)


def check_choice(db, root, request, packet):
    require(type(packet) is PreparedQuickChoice, 'Native QUICK choice preparation is missing')
    check_locked(db, Path(root).resolve() / '.rds/project.sqlite3', root, request, packet.admission)
    if evidence_fence(db, root, packet.file_requests, packet.receipt_requests,
                      packet.source_bytes) != packet.evidence_sha256:
        raise StaleAdmission('QUICK evidence changed after selection; STALE; review original evidence')
