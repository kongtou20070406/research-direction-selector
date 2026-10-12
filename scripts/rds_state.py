"""L1 reference SQLite state with the original mutation guards."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

from rds_reference_values import (
    READABLE_STATE_VERSIONS, RESOURCES, canonical, digest, require, strict_json,
)


class RDSState:
    def __init__(self, root_dir):
        self.root = Path(root_dir).resolve()
        self.directory = self.root / ".rds"
        self.db_path = self.directory / "state.sqlite3"

    def connect(self, create=False, readonly=False):
        """Return a reader; native writers must use the complete transaction scope."""
        require(readonly, 'Writable reference connections require RDSState.transaction()')
        require(not create, 'Readonly reference connections cannot create state; use RDSState.transaction()')
        return self._connect(readonly=True)

    def _connect(self, create=False, readonly=False):
        if not readonly:
            from rds_campaign import enforce
            enforce(self.root, kind='reference')
            from rds_workspace import check_reference_admission
            check_reference_admission(self.root)
        if create:
            require(not (self.directory / "contract.json").exists(),
                    "Legacy v5 JSON state found; preserve it and initialize a new root")
            self.directory.mkdir(parents=True, exist_ok=True)
        require(create or self.db_path.exists(), "RDS is not initialized")
        target = self.db_path.as_uri() + "?mode=ro" if readonly else self.db_path
        db = sqlite3.connect(target, uri=readonly, timeout=15, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            db.execute("PRAGMA synchronous=FULL")
        if create:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts (sha TEXT PRIMARY KEY, raw BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (run_id TEXT PRIMARY KEY, sha TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS receipt_no_update BEFORE UPDATE ON receipts
                BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS receipt_no_delete BEFORE DELETE ON receipts
                BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS event_no_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS event_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS artifact_no_update BEFORE UPDATE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'artifacts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS artifact_no_delete BEFORE DELETE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'artifacts are append-only'); END;
            """)
        return db

    @staticmethod
    def read_state(db):
        row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
        state = strict_json(row[0]) if row else {}
        if state:
            require(state["version"] in READABLE_STATE_VERSIONS,
                    "Incompatible state version")
            require(digest(state["contract"]) == state["contract_sha256"], "Contract integrity failure")
            if "branches" not in state:
                state["branches"] = {
                    "main": {
                        "id": "main", "parent_id": None, "orthogonal_dimension": "baseline",
                        "rationale": "Initial primary exploration branch", "status": "ACTIVE",
                        "stagnation_count": 0, "created_ns": 0,
                        "hypotheses": list(state.get("hypotheses", {}).keys())
                    }
                }
            state.setdefault("active_branch", "main")
            state.setdefault("baseline_cache", {})
        return state

    @contextmanager
    def snapshot(self):
        """Read a consistent state without taking a writer lock or rewriting it."""
        db = self.connect(readonly=True)
        try:
            db.execute("BEGIN")
            yield db, self.read_state(db)
        finally:
            db.close()

    @contextmanager
    def transaction(self, create=False):
        from rds_mutation import mutation
        with mutation(), self._transaction(create) as pair:
            yield pair

    @contextmanager
    def _transaction(self, create=False):
        db = self._connect(create)
        try:
            db.execute("BEGIN IMMEDIATE")
            state = self.read_state(db)
            yield db, state
            from rds_mutation import mutation
            from rds_workspace import check_reference_admission
            with mutation():
                check_reference_admission(self.root)
                if state:
                    self.invariants(state)
                    db.execute("INSERT INTO state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                               (canonical(state),))
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def invariants(state):
        budget = state["budget"]
        for key in RESOURCES:
            require(budget["spent"][key] >= 0 and budget["reserved"][key] >= 0, "Negative ledger balance")
            require(budget["spent"][key] + budget["reserved"][key] <= budget["limits"][key],
                    "Budget invariant violated")
            live = sum(p["spec"]["resources"][key] for p in state["plans"].values()
                       if p["run_status"] == "RESERVED")
            require(live == budget["reserved"][key], "Reservation ledger does not match plans")

    @staticmethod
    def event(db, kind, **fields):
        body = {"kind": kind, "time_ns": time.time_ns(), **fields}
        cur = db.execute("INSERT INTO events(body) VALUES (?)", (canonical(body),))
        return cur.lastrowid

    @staticmethod
    def artifact(db, raw):
        sha = digest(raw)
        db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (sha, raw))
        return sha
