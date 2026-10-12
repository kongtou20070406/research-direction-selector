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
from time import monotonic

from rds_project import ProjectStore, canonical, digest, file_sha, require
from rds_mutation import mutation

SCHEMA = 1
POINTER_NAME = ".rds-workspace.json"
EVENT_KIND = "WORKSPACE_BOUND"
ASSURANCE = "SUPPORTED_API_IDENTITY_CHECK_NOT_OS_SANDBOX"
WINDOWS = os.name == 'nt'
MAX_POINTER_BYTES = 64 * 1024
MAX_QUICK_REQUEST_BYTES = 2 * 1024 * 1024
MAX_BIND_WHEEL_STATE_BYTES = 2 * 1024 * 1024
MAX_BIND_PREFLIGHT_SECONDS = 5.0
MAX_BIND_LEDGER_BYTES = 64 * 1024 * 1024
BIND_HASH_CHUNK_BYTES = 1024 * 1024


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
    import stat
    from rds_bounded_io import read_regular_bytes
    path = pointer_path(root)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    require(stat.S_ISREG(metadata.st_mode), 'Workspace identity pointer is not a regular file')
    try:
        raw = read_regular_bytes(path, MAX_POINTER_BYTES, label='Workspace pointer',
                                 limit_message='Workspace pointer exceeds its size limit')
        pointer = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"Workspace identity pointer is unreadable: {path}: {exc}") from exc
    require(isinstance(pointer, dict) and pointer.get("schema") == SCHEMA
            and isinstance(pointer.get("ledger_sha256"), str) and pointer.get("ledger_sha256"),
            "Unrecognized workspace identity pointer")
    require(type(pointer.get('event_id')) is int and pointer['event_id'] > 0,
            'Invalid workspace progress anchor')
    return pointer


def _initialized(root):
    store = _store(root)
    if not store.path.is_file():
        return False
    with store._db(True) as db:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
                    and db.execute("SELECT 1 FROM contract WHERE id=1").fetchone())


def _recorded_quick_contract(db, allowance):
    """Keep paid scope identity across verified owner contract transitions."""
    from rds_method_revision import contract_history
    return next((entry['contract'] for entry in contract_history(db)
                 if entry['sha256'] == allowance.get('contract_sha256')), None)


def _matching_identity(pointer, event):
    if not isinstance(event, dict):
        return False
    identity = {key: event.get(key) for key in
                ('schema', 'ledger_sha256', 'contract_sha256', 'root')}
    return (event.get('event_digest') == pointer.get('event_digest') == digest(identity)
            and all(pointer.get(key) == identity[key]
                    for key in ('schema', 'ledger_sha256', 'contract_sha256')))


def _quick_request(path):
    from rds_bounded_io import read_regular_bytes
    from rds_cli import strict_json
    raw = read_regular_bytes(path, MAX_QUICK_REQUEST_BYTES, label='QUICK identity request')
    request = strict_json(raw.decode('utf-8'))
    require(isinstance(request, dict), 'QUICK identity request must be an object')
    pending = [(request, 0)]
    while pending:
        value, depth = pending.pop()
        require(depth <= 64, 'QUICK identity request exceeds its nesting bound')
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return request


def _charged_quick_scope(child, owner, owner_pointer):
    """Validate funding; discovery also requires the child's own pointer."""
    if not child.is_relative_to(owner / '.rds' / 'exec'):
        return False
    request_path = child / 'rds-exec-request.json'
    if not request_path.is_file() or not _initialized(child):
        return False
    request = _quick_request(request_path)
    parent, child_store = _store(owner), _store(child)
    with parent._db(True) as db:
        db.execute('BEGIN')
        identity = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id DESC LIMIT 1",
            (EVENT_KIND,)).fetchone()
        event = json.loads(identity['body']) if identity else None
        max_id = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
        anchor = owner_pointer.get('event_id')
        require(_matching_identity(owner_pointer, event)
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
                and _recorded_quick_contract(db, allowance) is not None
                and allowance.get('materialization_state') == 'PENDING'
                and allowance.get('resource') == 'wall_seconds'
                and allowance.get('accounting') == 'CONSERVATIVE_ALLOWANCE'
                and type(amount) in (int, float) and amount >= 0
                and charged is not None and charged['charged'] >= amount
                and budget == {'wall_seconds': amount})


def read_pointer(root):
    """Find the effective binding; initialized nested ledgers cannot hide ancestors.

    Paid QUICK scopes retain their own published pointer. Incomplete children
    inherit their owner until binding. Every enclosing binding is examined, including
    chains of QUICK children; ordinary nested ledgers inherit the outer identity.
    Malformed or unreadable pointers are damage, not absence.
    """
    start = Path(root).resolve()
    scope, selected = None, None
    for candidate in (start, *start.parents):
        pointer = _read_pointer_at(candidate)
        if pointer is not None:
            if (scope is None or _read_pointer_at(scope) is None
                    or not _charged_quick_scope(scope, candidate, pointer)):
                selected = pointer
            scope = candidate
        elif scope is None and _initialized(candidate):
            scope = candidate
    return selected


def _check_bind_deadline(deadline):
    require(monotonic() < deadline,
            'Workspace binding preflight time budget exceeded; inventory/hash is incomplete')


def _bounded_bind_ledger_sha(path, deadline):
    """Preserve file_sha's SHA-256 bytes with finite size and shared time bounds.

    Checks are cooperative: a blocking OS stat/open/read cannot be preempted.
    """
    import hashlib
    import stat

    _check_bind_deadline(deadline)
    metadata = path.stat()
    require(stat.S_ISREG(metadata.st_mode), 'Workspace ledger must be a regular file')
    require(metadata.st_nlink == 1, 'Cannot bind a workspace ledger with multiple hard links')
    require(metadata.st_size <= MAX_BIND_LEDGER_BYTES, 'Workspace ledger size bound exceeded')
    _check_bind_deadline(deadline)
    hashed, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        require(opened.st_nlink == 1, 'Cannot bind a workspace ledger with multiple hard links')
        require(opened.st_size <= MAX_BIND_LEDGER_BYTES,
                'Workspace ledger size bound exceeded')
        while True:
            _check_bind_deadline(deadline)
            chunk = stream.read(min(BIND_HASH_CHUNK_BYTES, MAX_BIND_LEDGER_BYTES - size + 1))
            _check_bind_deadline(deadline)
            if not chunk:
                break
            size += len(chunk)
            require(size <= MAX_BIND_LEDGER_BYTES, 'Workspace ledger size bound exceeded')
            hashed.update(chunk)
    _check_bind_deadline(deadline)
    return hashed.hexdigest()


def _refuse_active_descendants(root):
    """Inspect reservations under the mutation gate, before taking a writer lock."""
    import stat
    from rds_campaign import _Inventory

    deadline = monotonic() + MAX_BIND_PREFLIGHT_SECONDS
    _check_bind_deadline(deadline)
    root = Path(root).resolve()
    inventory = _Inventory()
    pending = [(root, 0)]
    while pending:
        _check_bind_deadline(deadline)
        directory, depth = pending.pop()
        inventory.directory(depth)
        with os.scandir(directory) as entries:
            for entry in entries:
                _check_bind_deadline(deadline)
                inventory.entry()
                metadata = entry.stat(follow_symlinks=False)
                _check_bind_deadline(deadline)
                # Metadata links can hide supported ledgers. Refuse binding
                # rather than silently omitting them or following link cycles.
                wheel_directory = (os.path.normcase(directory.name) == 'wheel'
                                   and os.path.normcase(directory.parent.name) == '.rds')
                wheel_state = wheel_directory and os.path.normcase(entry.name) == 'state.json'
                if (stat.S_ISLNK(metadata.st_mode)
                        or getattr(metadata, 'st_file_attributes', 0) & 0x400):
                    require(not (os.path.normcase(entry.name) == '.rds'
                                 or (os.path.normcase(directory.name) == '.rds'
                                     and os.path.normcase(entry.name) in {'project.sqlite3', 'state.sqlite3', 'wheel'})
                                 or wheel_state),
                            'Cannot bind workspace with linked workspace metadata; '
                            'settle existing work and use real metadata paths before binding')
                    continue
                path = Path(entry.path)
                if not path.resolve().is_relative_to(root):
                    continue
                _check_bind_deadline(deadline)
                if wheel_state and directory.parent.parent != root:
                    from rds_bounded_io import read_regular_bytes
                    from rds_cli import strict_json
                    require(stat.S_ISREG(metadata.st_mode),
                            'Descendant wheel state must be a regular file')
                    require(path.stat().st_nlink == 1,
                            'Cannot bind workspace containing wheel state with multiple hard links')
                    raw = read_regular_bytes(path, MAX_BIND_WHEEL_STATE_BYTES,
                                             label='Descendant wheel state')
                    _check_bind_deadline(deadline)
                    state = strict_json(raw.decode('utf-8'))
                    require(isinstance(state, dict) and 'pending' in state,
                            'Descendant wheel state must contain its pending journal')
                    # A committed file journal or unregistered dispatch still
                    # needs child admission to recover, even without a live run.
                    require('effects' not in state and state['pending'] is None,
                            f'Cannot bind workspace with a pending descendant wheel: {directory.parent.parent}; '
                            'finish its original wheel recovery first')
                    _check_bind_deadline(deadline)
                elif stat.S_ISDIR(metadata.st_mode):
                    pending.append((path, depth + 1))
                elif (os.path.normcase(entry.name) in {'project.sqlite3', 'state.sqlite3'}
                      and os.path.normcase(directory.name) == '.rds'
                      and (directory.parent != root or os.path.normcase(entry.name) == 'state.sqlite3')
                      and stat.S_ISREG(metadata.st_mode)):
                    # DirEntry.stat does not populate link counts on Windows.
                    require(path.stat().st_nlink == 1,
                            'Cannot bind workspace containing a ledger with multiple hard links')
                    # Raw read-only discovery avoids identity checks and any
                    # child mutation lock while examining already-owned pointers.
                    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                         timeout=min(1.0, max(0.0, deadline - monotonic())))
                    try:
                        db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                        db.execute('PRAGMA query_only=ON')
                        tables = {row[0] for row in db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'")}
                        if os.path.normcase(entry.name) == 'state.sqlite3':
                            if 'state' not in tables:
                                continue
                            from rds_cli import RDSState
                            state = RDSState.read_state(db)
                            active = any(plan['run_status'] in {'RESERVED', 'RUNNING', 'RECOVERY_REQUIRED'}
                                         for plan in state.get('plans', {}).values())
                        else:
                            if 'contract' not in tables or 'runs' not in tables:
                                continue
                            if db.execute('SELECT 1 FROM contract WHERE id=1').fetchone() is None:
                                continue
                            active = db.execute(
                                "SELECT id,status FROM runs WHERE status IN ('RESERVED','RUNNING') LIMIT 1").fetchone()
                            if not active and 'budget' in tables:
                                active = db.execute('SELECT 1 FROM budget WHERE reserved!=0 LIMIT 1').fetchone()
                            if not active:
                                active = db.execute(
                                    "SELECT 1 FROM events a WHERE json_extract(a.body,'$.kind')='THEORY_ALLOWANCE' "
                                    "AND NOT EXISTS (SELECT 1 FROM events o "
                                    "WHERE json_extract(o.body,'$.kind')='THEORY_OUTCOME' "
                                    "AND json_extract(o.body,'$.attempt_id')="
                                    "json_extract(a.body,'$.attempt_id')) LIMIT 1").fetchone()
                            if not active:
                                active = db.execute(
                                    "SELECT 1 FROM events a WHERE json_extract(a.body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                                    "AND json_extract(a.body,'$.materialization_state')='PENDING' "
                                    "AND NOT EXISTS (SELECT 1 FROM events m "
                                    "WHERE json_extract(m.body,'$.kind')='QUICK_JOB_ADMITTED' "
                                    "AND json_extract(m.body,'$.job_root')=json_extract(a.body,'$.job_root') "
                                    "AND json_extract(m.body,'$.request_sha256')=json_extract(a.body,'$.request_sha256')) "
                                    "LIMIT 1").fetchone()
                            if not active:
                                active = db.execute(
                                    "SELECT 1 FROM events p WHERE json_extract(p.body,'$.kind')='METHOD_REVISION_PREPARED' "
                                    "AND NOT EXISTS (SELECT 1 FROM events a "
                                    "WHERE json_extract(a.body,'$.kind')='METHOD_REVISION_ADOPTED' "
                                    "AND json_extract(a.body,'$.id')=json_extract(p.body,'$.id') "
                                    "AND json_extract(a.body,'$.prepared_sha256')=json_extract(p.body,'$.sha256')) "
                                    "LIMIT 1").fetchone()
                            if not active:
                                # A requested model run whose terminal receipt is
                                # already committed but whose processed event is
                                # missing is still unrecovered paid work:
                                # autonomy.drive() resumes exactly this state by
                                # replaying process_result(). Binding would make
                                # the inherited admission reject the writer that
                                # recovery needs, stranding the paid result and
                                # any proposal it carries. This mirrors the
                                # unfinished-run test drive() applies to
                                # state['autonomy_records'].
                                active = db.execute(
                                    "SELECT 1 FROM events r "
                                    "WHERE json_extract(r.body,'$.kind')='AUTONOMY_MODEL_REQUESTED' "
                                    "AND EXISTS (SELECT 1 FROM receipts rc "
                                    "WHERE rc.run_id=json_extract(r.body,'$.run_id')) "
                                    "AND NOT EXISTS (SELECT 1 FROM events p "
                                    "WHERE json_extract(p.body,'$.kind')='AUTONOMY_MODEL_PROCESSED' "
                                    "AND json_extract(p.body,'$.run_id')=json_extract(r.body,'$.run_id')) "
                                    "LIMIT 1").fetchone()
                            if not active and 'events' in tables:
                                # Tool preparation started but validation not yet published
                                # mirrors rds_campaign._preparation_finished requirements:
                                if 'research_records' not in tables:
                                    active = db.execute(
                                        "SELECT 1 FROM events p WHERE json_extract(p.body,'$.kind')='TOOL_PREPARATION_STARTED' LIMIT 1").fetchone()
                                else:
                                    active = db.execute(
                                        "SELECT 1 FROM events p WHERE json_extract(p.body,'$.kind')='TOOL_PREPARATION_STARTED' "
                                        "AND NOT EXISTS (SELECT 1 FROM research_records r "
                                        "WHERE r.id=json_extract(p.body,'$.request.validation_id') "
                                        "AND json_extract(r.body,'$.kind')='tool-validation') LIMIT 1").fetchone()
                            if not active and 'contract' in tables and 'receipts' in tables and 'events' in tables:
                                # An owned-Advisor descendant interrupted before post-receipt review
                                # requires OWNED_ADVISOR_REVIEW postdating ATTEMPT_FINISHED:
                                active = db.execute(
                                    "SELECT 1 FROM contract c "
                                    "WHERE c.id=1 AND json_extract(c.body,'$.advisor_policy') IS NOT NULL "
                                    "AND EXISTS (SELECT 1 FROM receipts) "
                                    "AND (NOT EXISTS (SELECT 1 FROM events f WHERE json_extract(f.body,'$.kind')='ATTEMPT_FINISHED') "
                                    "     OR NOT EXISTS (SELECT 1 FROM events r "
                                    "                    WHERE json_extract(r.body,'$.kind')='OWNED_ADVISOR_REVIEW' "
                                    "                    AND r.id > (SELECT COALESCE(MAX(f.id), 0) FROM events f WHERE json_extract(f.body,'$.kind')='ATTEMPT_FINISHED')))"
                                    "LIMIT 1").fetchone()
                            if not active and 'receipts' in tables:
                                # A prospective QUICK descendant with terminal receipts must settle
                                # its after-checkpoint into the parent ledger before ancestor binding:
                                req_file = directory.parent / 'rds-exec-request.json'
                                if (req_file.exists() or req_file.is_symlink()) and db.execute(
                                        'SELECT 1 FROM receipts LIMIT 1').fetchone():
                                    req_metadata = req_file.lstat()
                                    require(stat.S_ISREG(req_metadata.st_mode)
                                            and not getattr(req_metadata, 'st_file_attributes', 0) & 0x400,
                                            'Descendant QUICK request must be an unlinked regular file')
                                    _check_bind_deadline(deadline)
                                    req_data = _quick_request(req_file)
                                    _check_bind_deadline(deadline)
                                    res_ctx = req_data.get('research_context')
                                    if res_ctx is not None:
                                        require(isinstance(res_ctx, dict) and isinstance(res_ctx.get('ledger'), str),
                                                'Descendant prospective QUICK parent ledger is invalid')
                                        parent_ledger = Path(res_ctx['ledger'])
                                        require(parent_ledger.is_absolute() and parent_ledger.is_file(),
                                                'Descendant prospective QUICK parent ledger is unavailable')
                                        try:
                                            parent_db = sqlite3.connect(
                                                parent_ledger.as_uri() + '?mode=ro', uri=True,
                                                timeout=min(0.5, max(0.0, deadline - monotonic())))
                                            try:
                                                parent_db.set_progress_handler(
                                                    lambda: int(monotonic() >= deadline), 1000)
                                                parent_db.execute('PRAGMA query_only=ON')
                                                chk_name = 'exec-after-' + directory.parent.name
                                                chk_tbl = parent_db.execute(
                                                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone()
                                                has_chk = chk_tbl and parent_db.execute(
                                                    'SELECT 1 FROM checkpoints WHERE id=?', (chk_name,)).fetchone()
                                                active = not has_chk
                                            finally:
                                                parent_db.close()
                                        except (sqlite3.Error, OSError):
                                            active = True
                        _check_bind_deadline(deadline)
                        require(not active,
                                f'Cannot bind workspace with an active descendant: {directory.parent}; '
                                'settle or recover its original reservation first')
                    except sqlite3.OperationalError:
                        _check_bind_deadline(deadline)
                        raise
                    finally:
                        db.close()
    _check_bind_deadline(deadline)
    return deadline


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
    bootstrap_owner = None
    if pointer is not None and _read_pointer_at(store.root) is None:
        for owner in store.root.parents:
            owner_pointer = _read_pointer_at(owner)
            if owner_pointer is None:
                continue
            if owner_pointer == pointer and _charged_quick_scope(store.root, owner, owner_pointer):
                check_admission(owner)
                from rds_quick import _is_unmaterialized_allowance
                with _store(owner)._db(True) as parent_db:
                    allowance = parent_db.execute(
                        "SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                        "AND json_extract(body,'$.job_root')=?", (str(store.root),)).fetchone()
                    _is_unmaterialized_allowance(parent_db, store.root,
                                                _quick_request(store.root / 'rds-exec-request.json'),
                                                json.loads(allowance['body']))
                bootstrap_owner, pointer = owner, None
            break
    if pointer is not None:
        # Idempotence requires the same root, identity and committed progress;
        # never repair an older snapshot by lowering the existing pointer.
        try:
            check_admission(root)
        except ValueError as exc:
            raise ValueError(f'This workspace is already bound; {exc}') from exc
        return {"status": "ALREADY_BOUND", "pointer": pointer, "ledger_root": str(store.root)}
    deadline = _refuse_active_descendants(store.root)
    ledger_sha = _bounded_bind_ledger_sha(store.path, deadline)
    with store._db(True) as db:
        contract = store._contract(db)
        identity = {"schema": SCHEMA, "ledger_sha256": ledger_sha, "contract_sha256": digest(contract),
                    "root": str(store.root)}
    require(pointer is None, _OVERWRITE)
    event_digest = digest(identity)
    recovered = False
    from contextlib import closing, contextmanager

    @contextmanager
    def binding_writer():
        if bootstrap_owner is None:
            with store._db() as db:
                yield db
            return
        # Only this locked bind owns this connection. Ordinary/reentrant
        # writers still inherit the owner and fail their admission checks.
        from rds_campaign import enforce
        enforce(store.root)
        require(store.path.resolve().is_relative_to(store.root), 'Project database escapes root')
        with closing(sqlite3.connect(store.path.as_uri() + '?mode=rw', uri=True,
                                     factory=_AnchoredConnection, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.workspace_root = store.root
            db.committed_changes = 0
            db.workspace_materializing = False
            db.workspace_binding_digest = None
            with db:
                yield db

    with binding_writer() as db:
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
        if bootstrap_owner is not None and _read_pointer_at(store.root) is None:
            require(current == _read_pointer_at(bootstrap_owner), 'QUICK binding owner changed')
            current = None
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
        db.workspace_binding_digest = event_digest
        if pointer != current:
            _write_pointer(root, pointer)
        # The binding event already anchors these exact pending rows. Normal
        # writes still create a progress event when no event represents them.
        db.committed_changes = db.total_changes
    return {"status": "ALREADY_BOUND" if recovered else "BOUND", "pointer": pointer,
            "ledger_root": str(store.root)}


@mutation()
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
    require(_matching_identity(pointer, event),
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
    binding_digest = db.workspace_binding_digest
    own_pointer = _read_pointer_at(root)
    # Only bind's writer-locked publication may validate a pending identity.
    # Other writers must compare the pointer with committed progress, so their
    # new events cannot hide an earlier uncertain commit or restored snapshot.
    if binding_digest is None and (not db.workspace_materializing or own_pointer is not None):
        check_admission(root)
    if own_pointer is None:
        return
    # Validate the effective pointer against this transaction's view: the
    # initial binding event is not yet visible to a separate read connection.
    pointer = read_pointer(root)
    row = db.execute(
        "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id DESC LIMIT 1",
        (EVENT_KIND,)).fetchone()
    event = json.loads(row['body']) if row is not None else None
    require(_matching_identity(pointer, event),
            'Workspace ledger identity does not match; accounting resets are refused')
    require(binding_digest is None or binding_digest == event.get('event_digest'),
            'Workspace binding transaction identity does not match')
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


def check_reference_admission(root):
    """Validate a reference writer; keep mutation() held through its commit."""
    check_admission(root)
    require(read_pointer(root) is None,
            'A bound workspace uses its canonical project ledger; reference accounting is refused')


class _AnchoredConnection(sqlite3.Connection):
    def _before_commit(self):
        try:
            _anchor_transaction(self.workspace_root, self)
        except BaseException:
            self.rollback()
            raise

    def commit(self):
        with mutation():
            self._before_commit()
            result = super().commit()
            self.committed_changes = self.total_changes
            self.workspace_materializing = False
            self.workspace_binding_digest = None
            return result

    def rollback(self):
        result = super().rollback()
        self.committed_changes = self.total_changes
        self.workspace_materializing = False
        self.workspace_binding_digest = None
        return result

    def __exit__(self, exc_type, exc_value, traceback):
        # SQLite's context manager does not call the Python commit override.
        with mutation():
            if exc_type is None:
                self._before_commit()
            result = super().__exit__(exc_type, exc_value, traceback)
            self.committed_changes = self.total_changes
            self.workspace_materializing = False
            self.workspace_binding_digest = None
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
    db.workspace_binding_digest = None
    return db


def _recorded_event(store):
    with store._db(True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return None
        row = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY rowid DESC LIMIT 1",
            (EVENT_KIND,)).fetchone()
    return json.loads(row["body"]) if row is not None else None


@mutation()
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
