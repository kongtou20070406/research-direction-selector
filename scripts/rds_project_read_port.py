"""Small readonly projections borrowing the caller's SQLite transaction.

The caller supplies an open connection with an active explicit transaction and
must keep that SAME transaction alive until close(). SQLite pins a deferred
transaction's snapshot at its first read, not at construction of this view.
Each operation checks in_transaction; a commit followed immediately by BEGIN
cannot be detected, so replacing the transaction violates the caller contract.
close() invalidates only this view. It never closes or settles the connection.

Only fixed SELECTs are issued, through promptly closed, tuple-row cursors. No
connection settings/authorizer/row_factory are changed. This is an API boundary,
not OS security: trusted callers retain the original connection and its own
functions/converters/authorizer. No SQL or planning callback is accepted here.

Caps count selected SQLite scalar payload bytes (length(CAST(value AS BLOB))),
not Python object overhead. Oversized values are gated in SQL before transfer;
row and aggregate cap violations reject the whole read. Receipt pages include
one lookahead row in those caps, declare has_more, and never imply a complete
integrity audit. Summaries retain row identities without decoding/hash-checking
bodies. receipt() checks one bounded full body through the existing pure codec.
JSON arrays become tuples and objects readonly mappings, without changing leaf
values or UNKNOWN. Contract/run/policy validation is outside this small port.
Raw genesis/run projections retain their exact SQLite fields without decoding
or validation. GENESIS is the original contract table id=1, never the effective
active contract after method revisions. Raw runs cannot substitute for the rich
original run validator. Their size-only SELECT precedes body transfer, so both
row and aggregate caps (including lookahead) reject before any body is returned.
"""

from dataclasses import dataclass
import sqlite3
from types import MappingProxyType

from rds_project_records import decode_receipt


class ReadPortLimitError(ValueError):
    """A requested projection exceeds an explicit caller read cap."""


@dataclass(frozen=True, slots=True)
class BudgetRow:
    resource: str
    cap: float
    spent: float
    charged: float
    reserved: float


@dataclass(frozen=True, slots=True)
class ReceiptSummary:
    run_id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ReceiptPage:
    rows: tuple
    has_more: bool
    next_after: str | None


@dataclass(frozen=True, slots=True)
class RawGenesisContractRecord:
    """Retained GENESIS fields only; no active-policy or integrity decision."""

    id: int
    sha256: str
    body: str

    @property
    def kind(self):
        return "GENESIS"


@dataclass(frozen=True, slots=True)
class RawRunRecord:
    """Exact retained fields, not a validated run or admission result."""

    id: str
    status: str
    body: str


@dataclass(frozen=True, slots=True)
class RawRunPage:
    """A raw-row page with explicit continuation, never a complete audit."""

    rows: tuple
    has_more: bool
    next_after: str | None


def _projection(table, columns, where, order):
    size = " + ".join(f"coalesce(length(CAST({name} AS BLOB)), 0)" for name in columns)
    fields = ", ".join(f"CASE WHEN nbytes <= ? THEN {name} END" for name in columns)
    return (f"SELECT nbytes, {fields} FROM (SELECT {', '.join(columns)}, {size} AS nbytes "
            f"FROM {table} {where}) AS retained {order}")


_BUDGET = _projection("budget", ("resource", "cap", "spent", "charged", "reserved"),
                      "", "ORDER BY retained.resource LIMIT ?")
_PAGE = _projection("receipts", ("run_id", "sha256"), "", "ORDER BY retained.run_id LIMIT ?")
_PAGE_AFTER = _projection("receipts", ("run_id", "sha256"), "WHERE run_id > ?",
                         "ORDER BY retained.run_id LIMIT ?")
_RECEIPT = _projection("receipts", ("run_id", "sha256", "body"), "WHERE run_id = ?", "LIMIT 1")
_QUERIES = (_BUDGET, _PAGE, _PAGE_AFTER, _RECEIPT)


def _raw_queries(table, columns, where, order):
    size = " + ".join(f"coalesce(length(CAST({name} AS BLOB)), 0)" for name in columns)
    sizes = f"SELECT {size} FROM {table} {where} {order}"
    return sizes, _projection(table, columns, where, order)


_GENESIS_RAW = _raw_queries("contract", ("id", "sha256", "body"), "WHERE id = 1", "LIMIT 1")
_RUN_RAW = _raw_queries("runs", ("id", "status", "body"), "WHERE id = ?", "LIMIT 1")
_RUN_PAGE_RAW = _raw_queries("runs", ("id", "status", "body"), "", "ORDER BY id LIMIT ?")
_RUN_PAGE_AFTER_RAW = _raw_queries("runs", ("id", "status", "body"), "WHERE id > ?", "ORDER BY id LIMIT ?")
_QUERIES += _GENESIS_RAW + _RUN_RAW + _RUN_PAGE_RAW + _RUN_PAGE_AFTER_RAW


def _immutable(value):
    # Decoded JSON is acyclic and already byte-capped. An explicit postorder
    # stack avoids imposing a tighter depth limit than the original decoder.
    pending = [(value, False)]
    converted = []
    while pending:
        node, ready = pending.pop()
        mapping = isinstance(node, dict)
        if not mapping and not isinstance(node, list):
            converted.append(node)
        elif not ready:
            pending.append((node, True))
            children = tuple(node.values()) if mapping else node
            pending.extend((child, False) for child in reversed(children))
        else:
            count = len(node)
            children = converted[-count:] if count else []
            if count:
                del converted[-count:]
            converted.append(MappingProxyType(dict(zip(node, children))) if mapping else tuple(children))
    return converted[0]


class ProjectReadPort:
    """Borrow a connection; max_rows caps a page or a complete budget result.

    max_row_bytes/max_result_bytes apply per operation, including page lookahead.
    A receipt body may therefore be rejected before integrity decoding; cap
    errors are explicit ReadPortLimitError, not fabricated integrity outcomes.
    No cursor, raw connection or unbounded list is returned. close is idempotent.
    """

    __slots__ = ("__connection", "__error", "__digest", "__max_rows", "__row_bytes", "__result_bytes")

    def __init__(self, connection, *, integrity_error, digest, max_rows=64,
                 max_row_bytes=65536, max_result_bytes=262144):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("Read port requires an existing SQLite connection")
        if not connection.in_transaction:
            raise ValueError("Read port requires an active caller transaction")
        for name, value in (("max_rows", max_rows), ("max_row_bytes", max_row_bytes),
                            ("max_result_bytes", max_result_bytes)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"Read port {name} must be a positive integer")
        if not isinstance(integrity_error, type) or not issubclass(integrity_error, Exception):
            raise TypeError("Read port integrity_error must be an exception type")
        if not callable(digest):
            raise TypeError("Read port digest must be callable")
        self.__connection = connection
        self.__error, self.__digest = integrity_error, digest
        self.__max_rows = max_rows
        self.__row_bytes, self.__result_bytes = max_row_bytes, max_result_bytes

    def close(self):
        self.__connection = None

    def _read(self, sql, parameters):
        db = self.__connection
        if db is None:
            raise ValueError("Read port is closed")
        if not db.in_transaction:
            self.close()
            raise ValueError("Read port caller transaction has ended")
        if sql not in _QUERIES:
            raise ValueError("Read port supports only its fixed SELECT queries")
        cursor = db.cursor()
        cursor.row_factory = None
        total, rows = 0, []
        try:
            for row in cursor.execute(sql, parameters):
                size = row[0]
                if size > self.__row_bytes:
                    raise ReadPortLimitError("Read port max_row_bytes exceeded")
                total += size
                if total > self.__result_bytes:
                    raise ReadPortLimitError("Read port max_result_bytes exceeded")
                rows.append(row[1:])
        finally:
            cursor.close()
        return tuple(rows)

    def budget_rows(self):
        """Return the complete original resource/cap/spent/charged/reserved rows."""
        rows = self._read(_BUDGET, (self.__row_bytes,) * 5 + (self.__max_rows + 1,))
        if len(rows) > self.__max_rows:
            raise ReadPortLimitError("Read port max_rows exceeded")
        return tuple(BudgetRow(*row) for row in rows)

    def receipt_summaries(self, *, limit, after=None):
        """Keyset page ordered by the original run_id; no body or audit claim."""
        if type(limit) is not int or not 1 <= limit <= self.__max_rows:
            raise ReadPortLimitError("Read port page limit must be in 1..max_rows")
        if after is not None and not isinstance(after, str):
            raise TypeError("Read port after must be a run_id string or None")
        query = _PAGE if after is None else _PAGE_AFTER
        parameters = (self.__row_bytes,) * 2 + (() if after is None else (after,)) + (limit + 1,)
        rows = self._read(query, parameters)
        more = len(rows) > limit
        summaries = tuple(ReceiptSummary(*row) for row in rows[:limit])
        return ReceiptPage(summaries, more, summaries[-1].run_id if more else None)

    def receipt(self, run_id):
        """Decode one exact retained receipt; return None when no row exists."""
        if not isinstance(run_id, str):
            raise TypeError("Read port run_id must be a string")
        rows = self._read(_RECEIPT, (self.__row_bytes,) * 3 + (run_id,))
        if not rows:
            return None
        row = dict(zip(("run_id", "sha256", "body"), rows[0]))
        return _immutable(decode_receipt(row, self.__error, self.__digest))

    def _raw_rows(self, queries, parameters):
        # Both reads share the caller's original transaction/snapshot. Size-only
        # preflight bounds the complete page before transferring any raw bodies.
        self._read(queries[0], parameters)
        return self._read(queries[1], (self.__row_bytes,) * 3 + parameters)

    def genesis_contract_record(self):
        """Raw GENESIS id=1 sha256/body; None if absent, never effective_contract."""
        rows = self._raw_rows(_GENESIS_RAW, ())
        return RawGenesisContractRecord(*rows[0]) if rows else None

    def run_record(self, run_id):
        """Raw retained id/status/body, without original _run_row validation."""
        if not isinstance(run_id, str):
            raise TypeError("Read port run_id must be a string")
        rows = self._raw_rows(_RUN_RAW, (run_id,))
        return RawRunRecord(*rows[0]) if rows else None

    def run_records(self, *, limit, after=None):
        """Keyset page of RAW runs; no JSON, integrity or admission decision."""
        if type(limit) is not int or not 1 <= limit <= self.__max_rows:
            raise ReadPortLimitError("Read port page limit must be in 1..max_rows")
        if after is not None and not isinstance(after, str):
            raise TypeError("Read port after must be a run_id string or None")
        queries = _RUN_PAGE_RAW if after is None else _RUN_PAGE_AFTER_RAW
        parameters = (() if after is None else (after,)) + (limit + 1,)
        rows = self._raw_rows(queries, parameters)
        more = len(rows) > limit
        records = tuple(RawRunRecord(*row) for row in rows[:limit])
        return RawRunPage(records, more, records[-1].id if more else None)
