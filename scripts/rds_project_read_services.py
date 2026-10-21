"""Readonly selectors over the original caller connection.

Legacy complete-state reads retain their original coverage and validators.
The service never opens a database, commits, repairs, or calls a planner.
Bounded public projections remain on ProjectReadPort; compatibility budget
reads set byte caps from scalar sizes in the same caller transaction.
"""
from dataclasses import dataclass, field
import sqlite3
from types import MappingProxyType

from rds_contract_read import effective_contract, contract_history
from rds_project_read_port import ProjectReadPort
from rds_project_values import require


def has_table(db, name):
    """Is a table in this ledger yet? A missing table is a normal state, not an error."""
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


@dataclass(frozen=True, slots=True)
class ProjectReadServices:
    """Borrow a connection without changing transaction or database settings.

    Single legacy selectors support implicit SQLite read transactions too.
    Composite reads use the caller's existing explicit snapshot. Results carry
    no cursor or connection. Original rich validators stay in their callers.
    """

    _connection: sqlite3.Connection = field(repr=False, compare=False)

    def _select(self, sql, parameters=()):
        cursor = self._connection.cursor()
        cursor.row_factory = None
        try:
            cursor.execute(sql, parameters)
            names = tuple(column[0] for column in cursor.description)
            return tuple(MappingProxyType(dict(zip(names, row))) for row in cursor)
        finally:
            cursor.close()

    def has_contract(self):
        return has_table(self._connection, 'contract')

    def contract(self):
        require(self.has_contract(), "Project contract has not been initialized")
        return effective_contract(self._connection)

    def history(self):
        return contract_history(self._connection)

    def owned_coverage(self, maximum):
        for table, message in (('runs', 'Owned run coverage limit exceeded'),
                               ('receipts', 'Owned receipt coverage limit exceeded')):
            require(self._select(f'SELECT count(*) AS n FROM {table}')[0]['n'] <= maximum, message)

    def runs(self):
        return self._select('SELECT id,status,body FROM runs ORDER BY id')

    def receipts(self):
        return self._select('SELECT run_id,sha256,body FROM receipts ORDER BY run_id')

    def bounded_port(self, *, integrity_error, digest, **limits):
        return ProjectReadPort(self._connection, integrity_error=integrity_error,
                               digest=digest, **limits)

    def budget(self, *, integrity_error, digest):
        # Preserve the original number of frozen budget dimensions. A new fixed
        # 64-row limit would reject old, valid contracts during this extraction.
        require(self._connection.in_transaction, 'Read budget requires an active caller transaction')
        size = ' + '.join(f'coalesce(length(CAST({name} AS BLOB)), 0)'
                          for name in ('resource', 'cap', 'spent', 'charged', 'reserved'))
        stats = self._select(f'SELECT count(*) AS n, max({size}) AS row_bytes, '
                             f'sum({size}) AS result_bytes FROM budget')[0]
        port = self.bounded_port(integrity_error=integrity_error, digest=digest,
                                 max_rows=max(1, stats['n']),
                                 max_row_bytes=max(1, stats['row_bytes'] or 0),
                                 max_result_bytes=max(1, stats['result_bytes'] or 0))
        try:
            return tuple(MappingProxyType({'resource': row.resource, 'cap': row.cap,
                                          'spent': row.spent, 'charged': row.charged,
                                          'reserved': row.reserved})
                         for row in port.budget_rows())
        finally:
            port.close()

    def exposure_bodies(self):
        return tuple(row['body'] for row in self._select('SELECT body FROM exposures ORDER BY id'))

    def campaign_body(self):
        rows = self._select("SELECT body FROM events WHERE json_extract(body,'$.kind')="
                            "'CAMPAIGN_STARTED' ORDER BY id LIMIT 1")
        return rows[0]['body'] if rows else None

    def has_completion(self, run_id, sha256):
        return bool(self._select("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                                 "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=? LIMIT 1",
                                 (run_id, sha256)))

    def checkpoint_ids(self, *, maximum):
        if not has_table(self._connection, 'checkpoints'):
            return None
        count = self._select('SELECT count(*) AS n FROM checkpoints')[0]['n']
        require(count <= maximum, 'Owned decision history exceeds its bounded coverage')
        return tuple(row['id'] for row in self._select('SELECT id FROM checkpoints ORDER BY rowid'))

    def latest_event_record(self, kind=None):
        if kind is None:
            rows = self._select('SELECT id,body FROM events ORDER BY id DESC LIMIT 1')
        else:
            rows = self._select("SELECT id,body FROM events WHERE json_extract(body,'$.kind')=? "
                                "ORDER BY id DESC LIMIT 1", (kind,))
        return rows[0] if rows else None

    def latest_event(self, kind):
        row = self.latest_event_record(kind)
        return row['body'] if row else None

    def receipt_row(self, run_id):
        rows = self._select('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run_id,))
        return rows[0] if rows else None

    def revision_adoption(self, revision_id):
        rows = self._select("SELECT body FROM events WHERE json_extract(body,'$.kind')='METHOD_REVISION_ADOPTED' "
                            "AND json_extract(body,'$.id')=?", (revision_id,))
        return rows[0] if rows else None

    def completion_ids(self, run_id, sha256):
        return tuple(row['id'] for row in self._select("SELECT id FROM events WHERE "
                     "json_extract(body,'$.kind')='ATTEMPT_FINISHED' AND json_extract(body,'$.run_id')=? "
                     "AND json_extract(body,'$.sha256')=?", (run_id, sha256)))

    def checkpoints(self):
        if not has_table(self._connection, 'checkpoints'):
            return ()
        return self._select('SELECT * FROM checkpoints ORDER BY id')

    def dependency_snapshot(self):
        if not has_table(self._connection, 'dependency_snapshots'):
            return None
        rows = self._select('SELECT sha256,body FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1')
        return rows[0] if rows else None

    def events(self, kinds, *, maximum, limit_message):
        require(type(maximum) is int and maximum >= 0, 'Read event limit must be nonnegative')
        require(isinstance(kinds, tuple) and all(isinstance(kind, str) for kind in kinds),
                'Read events needs literal kind values')
        placeholders = ','.join('?' for _ in kinds)
        rows = self._select("SELECT id,body FROM events WHERE json_extract(body,'$.kind') IN (" +
                            placeholders + ') ORDER BY id LIMIT ?', (*kinds, maximum + 1))
        require(len(rows) <= maximum, limit_message)
        return rows
