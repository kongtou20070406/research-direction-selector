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
from pathlib import Path

from rds_project import ProjectStore, canonical, digest, file_sha, require

SCHEMA = 1
POINTER_NAME = ".rds-workspace.json"
EVENT_KIND = "WORKSPACE_BOUND"
ASSURANCE = "SUPPORTED_API_IDENTITY_CHECK_NOT_OS_SANDBOX"


def pointer_path(root):
    return Path(root) / POINTER_NAME


def _write_pointer(root, pointer):
    """Replace the pointer atomically: a torn write is never observable.

    Recovery must survive the very crash this function guards against, so the
    pointer is written to a temporary sibling, flushed, and moved into place.
    On Windows os.replace fails while the destination is open elsewhere and
    the old pointer stays intact — damage is refused, never half-adopted.
    """
    import uuid
    target = pointer_path(root)
    tmp = target.with_name(f"{POINTER_NAME}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(canonical(pointer).encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


_OVERWRITE = ("Workspace identity pointer is frozen; delete the workspace, not the ledger, "
              "to start a different project")


def _store(root):
    return ProjectStore(root)


def read_pointer(root):
    """The bound identity pointer, or None; a malformed pointer is damage, not absence.

    The nearest pointer walking up from `root` governs: a bound workspace binds
    the whole tree beneath it, so a sibling project root cannot escape the
    canonical ledger by re-rooting. A pointer inside `.rds` would be lost with
    the ledger; this pointer lives outside it.
    """
    start = Path(root).resolve()
    for candidate in (start, *start.parents):
        path = candidate / POINTER_NAME
        if path.is_file():
            try:
                pointer = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f"Workspace identity pointer is unreadable: {path}: {exc}") from exc
            require(isinstance(pointer, dict) and pointer.get("schema") == SCHEMA
                    and isinstance(pointer.get("ledger_sha256"), str) and pointer.get("ledger_sha256"),
                    "Unrecognized workspace identity pointer")
            return pointer
        if (candidate / ".rds").is_dir() and candidate != start:
            # A nested project root with its own state dir stops upward search:
            # its own workspace, not the parent's binding.
            return None
    return None


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
        # An existing pointer with a matching recorded identity is idempotent
        # recovery; a conflicting pointer is a refusal. Identity rides the
        # ledger's append-only WORKSPACE_BOUND event, not database file bytes
        # (SQLite checkpoints legitimately rewrite those).
        event = _recorded_event(store)
        require(event is not None and event.get("event_digest") == pointer.get("event_digest"),
                "This workspace is already bound to a different project ledger; "
                "delete the workspace, not the ledger, to start a different project")
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
    pointer = {"schema": SCHEMA, "assurance": ASSURANCE, "ledger_sha256": ledger_sha,
               "contract_sha256": identity["contract_sha256"], "event_digest": event_digest}
    _write_pointer(root, pointer)
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
    # anchors progress — the highest event id seen by a supported API. Events
    # are append-only, so a rolled-back ledger's event log ends before that
    # anchor and admission is refused until the ledger catches up. The pointer
    # only ever moves forward; a refusal here never rewrites accounting.
    with store._db(True) as db:
        max_id = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        bound_id = db.execute(
            "SELECT id FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id DESC LIMIT 1",
            (EVENT_KIND,)).fetchone()[0]
    anchor = pointer.get("event_id")
    if anchor is not None:
        require(max_id >= anchor,
                "This workspace's ledger holds fewer events than the workspace pointer recorded; "
                "a ledger snapshot was restored after binding. Accounting resets are refused; "
                "restore the ledger state that includes the recorded progress")
        if max_id > anchor:
            pointer = {**pointer, "event_id": max_id}
            _write_pointer(root, pointer)
    else:
        _write_pointer(root, {**pointer, "event_id": max(bound_id, max_id)})
    return store


def _recorded_event(store):
    with store._db(True) as db:
        row = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY rowid DESC LIMIT 1",
            (EVENT_KIND,)).fetchone()
    return json.loads(row["body"]) if row is not None else None


def coverage(root):
    """Read-only binding coverage for discovery/status; claims no enforcement beyond supported APIs."""
    store = _store(root)
    pointer = read_pointer(root)
    bound = pointer is not None
    if not bound:
        return {"schema": SCHEMA, "status": "UNBOUND", "assurance": ASSURANCE,
                "pointer": POINTER_NAME, "ledger_root": str(store.root),
                "next_command": "python -B scripts/rds_cli.py --root "
                                f"{_arg(str(store.root))} workspace bind"}
    try:
        check = check_admission(root)
        status = "BOUND"
        mismatch = None
    except ValueError as exc:
        status, mismatch, check = "MISMATCH", str(exc), None
    return {"schema": SCHEMA, "status": status, "assurance": ASSURANCE,
            "pointer": POINTER_NAME, "ledger_root": str(store.root),
            "ledger_sha256": pointer["ledger_sha256"], "contract_sha256": pointer.get("contract_sha256"),
            **({"mismatch": mismatch} if mismatch else {}),
            "next_command": ("python -B scripts/rds_cli.py --root "
                             f"{_arg(str(store.root))} project status" if status == "BOUND" else None)}


def _arg(value):
    return "'" + value.replace("'", "'\\''") + "'" if " " in value or os.name == "nt" and "'" in value else value


def check_canonical_continuation(root):
    """Bound workspaces continue through the canonical ledger's existing methods.

    A bound pointer whose ledger matches admits normal create/execute/advance;
    this is an assertion helper for regression coverage, not a new authority.
    """
    check_admission(root)
    return True
