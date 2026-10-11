"""Durable workspace-to-ledger identity binding (#282).

One selected workspace binds, once, to one canonical project ledger. The
pointer lives outside `.rds` so deleting or replacing the ledger cannot reset
accounting silently: the pointer's recorded ledger digest stops matching. The
ledger keeps an append-only WORKSPACE_BOUND event so a fresh ledger built with
identical contract bytes is distinguishable from the original. Binding records
no authority of its own: it dispatches no work, grants no budget, and changes
no attempt, receipt or evidence. A host must protect the pointer file for
adversarial enforcement (#120); this module enforces supported RDS APIs only.
"""
import json
import os
import sqlite3
from pathlib import Path

from rds_project import ProjectStore, canonical, digest, file_sha, load_json, require
from rds_mutation import mutation

SCHEMA = 1
POINTER_NAME = ".rds-workspace.json"
EVENT_KIND = "WORKSPACE_BOUND"
ASSURANCE = "SUPPORTED_API_IDENTITY_CHECK_NOT_OS_SANDBOX"
WINDOWS = os.name == 'nt'
MAX_POINTER_BYTES = 64 * 1024


def pointer_path(root):
    return Path(root) / POINTER_NAME


def _write_pointer(root, pointer):
    """Publish complete sibling bytes with the campaign's OS durability barrier.

    Refuse success when file sync, replacement or directory sync fails. If the
    replacement is already visible, retain the advanced pointer: rolling it
    back could admit older accounting after an uncertain publication.
    """
    import uuid
    require(WINDOWS or hasattr(os, 'O_DIRECTORY'),
            'Durable workspace publication is unsupported on this platform')
    target = pointer_path(root)
    tmp = target.with_name(f"{POINTER_NAME}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(canonical(pointer).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if WINDOWS:
            _replace_write_through(tmp, target)
        else:
            os.replace(tmp, target)
            _sync_directory(target.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _sync_directory(path):
    """Persist POSIX pointer replacement, or refuse success."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_write_through(source, destination):
    """Replace on Windows with write-through and no cross-volume fallback."""
    import ctypes
    from ctypes import wintypes
    move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
    move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    move.restype = wintypes.BOOL
    # MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH, as progress advances.
    if not move(str(source), str(destination), 0x1 | 0x8):
        raise ctypes.WinError(ctypes.get_last_error())


_OVERWRITE = ("Workspace identity pointer is frozen; delete the workspace, not the ledger, "
              "to start a different project")


def _store(root):
    return ProjectStore(root)


def _read_pointer_at(root):
    path = pointer_path(root)
    if not path.is_file():
        return None
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_POINTER_BYTES + 1)
        require(len(raw) <= MAX_POINTER_BYTES, 'Workspace pointer exceeds its size limit')
        pointer = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"Workspace identity pointer is unreadable: {path}: {exc}") from exc
    require(isinstance(pointer, dict) and pointer.get("schema") == SCHEMA
            and isinstance(pointer.get("ledger_sha256"), str) and pointer.get("ledger_sha256"),
            "Unrecognized workspace identity pointer")
    return pointer


def _initialized(root):
    store = _store(root)
    if not store.path.is_file():
        return False
    with store._db(True) as db:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
                    and db.execute("SELECT 1 FROM contract WHERE id=1").fetchone())


def _charged_quick_scope(child, owner, owner_pointer):
    """Only a frozen, parent-funded QUICK ledger may shadow its enclosing scope."""
    if not child.is_relative_to(owner / '.rds' / 'exec'):
        return False
    request_path = child / 'rds-exec-request.json'
    if not request_path.is_file() or not _initialized(child):
        return False
    request = load_json(request_path)
    parent, child_store = _store(owner), _store(child)
    with parent._db(True) as db:
        db.execute('BEGIN')
        identity = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id DESC LIMIT 1",
            (EVENT_KIND,)).fetchone()
        event = json.loads(identity['body']) if identity else None
        max_id = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
        anchor = owner_pointer.get('event_id')
        require(event is not None and event.get('event_digest') == owner_pointer.get('event_digest')
                and event.get('root') == str(owner), 'Owning QUICK workspace identity does not match')
        require(anchor is None or type(anchor) is int and 0 < anchor <= max_id,
                'Owning QUICK workspace ledger was rolled back; accounting resets are refused')
        rows = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
            "AND json_extract(body,'$.job_root')=?", (str(child),)).fetchall()
        if len(rows) != 1:
            return False
        allowance = json.loads(rows[0]['body'])
        charged = db.execute("SELECT charged FROM budget WHERE resource='wall_seconds'").fetchone()
        with child_store._db(True) as child_db:
            budget = child_store._contract(child_db).get('budget')
        amount = allowance.get('amount')
        return (isinstance(request, dict) and allowance.get('request_sha256') == digest(request)
                and allowance.get('contract_sha256') == digest(parent._contract(db))
                and allowance.get('materialization_state') == 'PENDING'
                and allowance.get('resource') == 'wall_seconds'
                and allowance.get('accounting') == 'CONSERVATIVE_ALLOWANCE'
                and type(amount) in (int, float) and amount >= 0
                and charged is not None and charged['charged'] >= amount
                and budget == {'wall_seconds': amount})


def read_pointer(root):
    """Find the effective binding; initialized nested ledgers cannot hide ancestors.

    Paid QUICK scopes retain their own pointer (or bind their new ledger while
    materialization is pending). Every enclosing binding is examined, including
    chains of QUICK children; ordinary nested ledgers inherit the outer identity.
    Malformed or unreadable pointers are damage, not absence.
    """
    start = Path(root).resolve()
    scope, selected = None, None
    for candidate in (start, *start.parents):
        pointer = _read_pointer_at(candidate)
        if pointer is not None:
            if scope is None or not _charged_quick_scope(scope, candidate, pointer):
                selected = pointer
            scope = candidate
        elif scope is None and _initialized(candidate):
            scope = candidate
    return selected


@mutation()
def bind(root):
    """Bind this workspace, once, to the canonical ledger it already contains.

    Idempotent recovery: an existing pointer with matching bytes is returned
    unchanged; a conflicting pointer is refused. Binding writes one append-only
    event into the ledger and changes nothing else: no attempt, receipt or
    budget row is touched. Concurrent binders race on the event insert; the
    loser sees the recorded identity and must match it.
    """
    store = _store(root)
    require(store.path.is_file(), f"No project ledger to bind at {store.root}")
    pointer = read_pointer(root)
    if pointer is not None:
        # Idempotence requires the same root, identity and committed progress;
        # never repair an older snapshot by lowering the existing pointer.
        try:
            check_admission(root)
        except ValueError as exc:
            raise ValueError(f'This workspace is already bound; {exc}') from exc
        return {"status": "ALREADY_BOUND", "pointer": pointer, "ledger_root": str(store.root)}
    ledger_sha = file_sha(store.path)
    with store._db() as db:
        contract = store._contract(db)
        identity = {"schema": SCHEMA, "ledger_sha256": ledger_sha, "contract_sha256": digest(contract),
                    "root": str(store.root)}
    require(pointer is None, _OVERWRITE)
    event_digest = digest(identity)
    recovered = False
    with store._db() as db:
        # BEGIN IMMEDIATE serializes concurrent binders; the re-read under the
        # write lock means exactly one WORKSPACE_BOUND event can ever exist.
        db.execute("BEGIN IMMEDIATE")
        recorded = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY rowid DESC LIMIT 1",
            (EVENT_KIND,)).fetchone()
        if recorded is None:
            db.execute("INSERT INTO events(body) VALUES (?)", (canonical(
                {"kind": EVENT_KIND, "assurance": ASSURANCE, "event_digest": event_digest, **identity}),))
        else:
            # Two identical sources: a crash between the committed event and
            # the pointer write, or a concurrent binder that won the race. Both
            # recover the pointer from the recorded identity — this root is the
            # recorded root — while a different contract or root stays refused.
            # The recorded ledger digest rides along as auxiliary evidence;
            # byte comparison is not identity.
            recovered = True
            recorded_body = json.loads(recorded["body"])
            require(recorded_body.get("root") == identity["root"]
                    and recorded_body.get("contract_sha256") == identity["contract_sha256"],
                    "The ledger already records a different workspace identity; "
                    "each bound ledger serves one workspace")
            event_digest = recorded_body["event_digest"]
            ledger_sha = recorded_body["ledger_sha256"]
        max_id = db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        # Re-read and publish under the same writer lock; a delayed binder must
        # not replace progress committed after its initial discovery.
        current = read_pointer(root)
        if current is not None:
            require(current.get('event_digest') == event_digest,
                    'Workspace ledger identity does not match; accounting resets are refused')
            anchor = current.get('event_id')
            require(anchor is None or type(anchor) is int and 0 < anchor <= max_id,
                    'A ledger snapshot was restored after binding; accounting resets are refused')
            pointer = {**current, 'event_id': max_id}
        else:
            pointer = {"schema": SCHEMA, "assurance": ASSURANCE, "ledger_sha256": ledger_sha,
                       "contract_sha256": identity["contract_sha256"], "event_digest": event_digest,
                       "event_id": max_id}
        if pointer != current:
            _write_pointer(root, pointer)
        # The binding event already anchors these exact pending rows. Normal
        # writes still create a progress event when no event represents them.
        db.committed_changes = db.total_changes
    return {"status": "ALREADY_BOUND" if recovered else "BOUND", "pointer": pointer,
            "ledger_root": str(store.root)}


def check_admission(root, supersedes=None, separate_project=False):
    """Refuse a bound workspace before any admission that could reset accounting.

    Returns the canonical store when the workspace is unbound or bound to this
    exact ledger. Raises ValueError naming the next supported command otherwise.
    Refusals happen before any write, so attempts, receipts and budget are
    unchanged.

    Identity rides the ledger's own WORKSPACE_BOUND event, not the database file
    bytes: SQLite checkpoints and same-engine writes legitimately change file
    bytes, while the append-only event cannot be rewritten by supported APIs. A
    replaced ledger carries no recorded identity, so the pointer's event digest
    stops matching.
    """
    store = _store(root)
    pointer = read_pointer(root)
    if pointer is None:
        return store
    require(supersedes is None,
            "This workspace is bound to one canonical project ledger; --supersedes would open "
            "a second accounting identity. Continue in the bound ledger or use an unbound root")
    require(not separate_project,
            "This workspace is bound to one canonical project ledger; --separate-project would "
            "open a second accounting identity in the same workspace")
    require(store.path.is_file(),
            "This workspace is bound to a project ledger that is now missing; restore the ledger "
            f"at {store.root} or record the loss explicitly with a new unbound root")
    event = _recorded_event(store)
    require(event is not None and event.get("event_digest") == pointer.get("event_digest"),
            "This workspace is bound to a canonical ledger whose recorded identity does not match "
            "the workspace pointer; the ledger was replaced or its identity event is damaged. "
            "Accounting resets are refused; restore the bound ledger")
    # A copied workspace carries both the pointer and the ledger event with it,
    # so digest equality alone admits a clone. The event also records the bound
    # root; the original ledger serves only its own directory tree.
    require(str(store.root) == event.get("root"),
            "This workspace is a copy of a bound workspace; the recorded identity names "
            f"{event.get('root')}. A bound ledger serves exactly one workspace")
    # Restoring a ledger snapshot taken after the bind rolls budget and events
    # back invisibly: the identity event still matches. The pointer therefore
    # anchors committed progress, not the last observation by a supported API. Events
    # are append-only, so a rolled-back ledger's event log ends before that
    # anchor and admission is refused until the ledger catches up. The pointer
    # only ever moves forward; a refusal here never rewrites accounting.
    with store._db(True) as db:
        max_id = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
    anchor = pointer.get("event_id")
    if anchor is not None:
        require(type(anchor) is int and anchor > 0, "Invalid workspace progress anchor")
        require(max_id >= anchor,
                "This workspace's ledger holds fewer events than the workspace pointer recorded; "
                "a ledger snapshot was restored after binding. Accounting resets are refused; "
                "restore the ledger state that includes the recorded progress")
    return store


def _anchor_transaction(root, db):
    """Publish progress under the writer lock, before its SQLite commit.

    A failed pointer write rolls back the transaction. A failed commit after
    publication leaves the pointer ahead and refuses further admission; it
    never silently refunds uncertain accounting. Only this ledger's own
    pointer is advanced, not an enclosing QUICK owner's pointer.
    """
    if not db.in_transaction:
        return
    if not pointer_path(root).is_file():
        # Generic writers inherit ancestor admission. Only the separately
        # validated initial QUICK connection may materialize without a pointer.
        if not db.workspace_materializing:
            check_admission(root)
        return
    # Validate the effective pointer against this transaction's view: the
    # initial binding event is not yet visible to a separate read connection.
    pointer = read_pointer(root)
    row = db.execute(
        "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id DESC LIMIT 1",
        (EVENT_KIND,)).fetchone()
    event = json.loads(row['body']) if row is not None else None
    require(event is not None and event.get('event_digest') == pointer.get('event_digest'),
            'Workspace ledger identity does not match; accounting resets are refused')
    require(event.get('root') == str(Path(root).resolve()), 'Workspace ledger belongs to another root')
    max_id = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
    anchor = pointer.get('event_id')
    require(anchor is None or type(anchor) is int and 0 < anchor <= max_id,
            'A ledger snapshot was restored after binding; accounting resets are refused')
    # Some reservations change budget/run rows without appending an event.
    # Give those committed writes an append-only progress identity as well.
    if db.total_changes > db.committed_changes and anchor == max_id:
        db.execute('INSERT INTO events(body) VALUES (?)',
                   (canonical({'kind': 'WORKSPACE_PROGRESS',
                               'workspace_event_digest': pointer['event_digest']}),))
        max_id = db.execute('SELECT MAX(id) FROM events').fetchone()[0]
    if anchor is None or max_id > anchor:
        _write_pointer(root, {**pointer, 'event_id': max_id})


class _AnchoredConnection(sqlite3.Connection):
    def _before_commit(self):
        try:
            _anchor_transaction(self.workspace_root, self)
        except BaseException:
            self.rollback()
            raise

    def commit(self):
        self._before_commit()
        result = super().commit()
        self.committed_changes = self.total_changes
        self.workspace_materializing = False
        return result

    def rollback(self):
        result = super().rollback()
        self.committed_changes = self.total_changes
        self.workspace_materializing = False
        return result

    def __exit__(self, exc_type, exc_value, traceback):
        # SQLite's context manager does not call the Python commit override.
        if exc_type is None:
            self._before_commit()
        result = super().__exit__(exc_type, exc_value, traceback)
        self.committed_changes = self.total_changes
        self.workspace_materializing = False
        return result


def connect(root, database, *, _materialization=None, **kwargs):
    """Writable project-ledger connection with durable workspace anchoring."""
    if _materialization is None:
        check_admission(root)
    else:
        require(isinstance(_materialization, tuple) and len(_materialization) == 3,
                'Fresh QUICK materialization requires its charged admission')
        _store(root)._check_fresh_workspace(*_materialization)
    db = sqlite3.connect(database, factory=_AnchoredConnection, **kwargs)
    db.workspace_root = Path(root).resolve()
    db.committed_changes = db.total_changes
    db.workspace_materializing = _materialization is not None
    return db


def _recorded_event(store):
    with store._db(True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return None
        row = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY rowid DESC LIMIT 1",
            (EVENT_KIND,)).fetchone()
    return json.loads(row["body"]) if row is not None else None


def coverage(root):
    """Read-only binding coverage for discovery/status; claims no enforcement beyond supported APIs."""
    store = _store(root)
    pointer = None
    try:
        pointer = read_pointer(root)
        if pointer is None:
            return {"schema": SCHEMA, "status": "UNBOUND", "assurance": ASSURANCE,
                    "pointer": POINTER_NAME, "ledger_root": str(store.root),
                    "next_command": "python -B scripts/rds_cli.py --root "
                                    f"{_arg(str(store.root))} workspace bind"}
        check = check_admission(root)
        status = "BOUND"
        mismatch = None
    except (ValueError, sqlite3.Error, OSError) as exc:
        status, mismatch, check = "MISMATCH", str(exc), None
    return {"schema": SCHEMA, "status": status, "assurance": ASSURANCE,
            "pointer": POINTER_NAME, "ledger_root": str(store.root),
            **({"ledger_sha256": pointer["ledger_sha256"],
                "contract_sha256": pointer.get("contract_sha256")} if pointer is not None else {}),
            **({"mismatch": mismatch} if mismatch else {}),
            "next_command": ("python -B scripts/rds_cli.py --root "
                             f"{_arg(str(store.root))} project status" if status == "BOUND" else None)}


def _arg(value):
    from rds_project import _shell_argument
    return _shell_argument(value)


def check_canonical_continuation(root):
    """Bound workspaces continue through the canonical ledger's existing methods.

    A bound pointer whose ledger matches admits normal create/execute/advance;
    this is an assertion helper for regression coverage, not a new authority.
    """
    check_admission(root)
    return True
