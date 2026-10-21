"""Staged durable notifications on the caller's ProjectStore transaction.

No commit, connection, dispatcher or authorization is created here. External
delivery is at-least-once; a same-ledger consumer can commit its effect and ACK
atomically. Payloads record evidence, never permissions or scientific truth.
"""
import json
from pathlib import Path
import re
import uuid

from rds_project_values import canonical, require

KINDS = frozenset({"JOB_STARTED", "CHECKPOINT_COMMITTED", "RESULT_READY",
                   "JOB_FAILED", "RESOURCE_LIMIT", "RESEARCH_JUDGMENT"})
MAX_SEQUENCE = 2**31 - 1
MAX_INTEGER = 2**63 - 1
MAX_BYTES = 65536
FIELDS = {"schema_version", "job_id", "attempt_id", "sequence", "kind", "payload"}


def _publish_lifecycle(store, db, run, kind, payload):
    """Emit only within the transaction that records the source transition."""
    # Older accepted run records can carry non-UUID attempt identities. Settle
    # them unchanged; do not rekey history or weaken the v1 envelope contract.
    if not isinstance(run["attempt_id"], str) or re.fullmatch(r"[0-9a-f]{32}", run["attempt_id"]) is None:
        return None
    outbox = Outbox(store, db)
    outbox.initialize()
    sequence = db.execute(
        "SELECT COALESCE(MAX(sequence),0)+1 FROM outbox_events WHERE job_id=? AND attempt_id=?",
        (run["id"], run["attempt_id"])).fetchone()[0]
    return outbox.publish({"schema_version": 1, "job_id": run["id"],
                           "attempt_id": run["attempt_id"], "sequence": sequence,
                           "kind": kind, "payload": payload})


def publish_started(store, db, run):
    """JOB_STARTED means the attempt entered RUNNING, before child launch."""
    require(run["status"] == "RUNNING", "Started event requires a running attempt")
    return _publish_lifecycle(store, db, run, "JOB_STARTED", {
        "phase": "ATTEMPT_RUNNING", "run_status": run["run_status"],
        "started_at": run["started_at"], "worker_pid": run["worker_pid"]})


def publish_finished(store, db, run, receipt):
    """One terminal notification, bound to the original operational receipt.

    Success is result availability, not scientific acceptance. Recovery with
    unknown exit/costs is JOB_FAILED; a progress stall is not a resource limit.
    Existing receipts bypass this writer, including pre-outbox ledgers. Legacy
    non-UUID attempts settle normally without receiving a v1 notification.
    """
    require(run["id"] == receipt["run_id"] and run["attempt_id"] == receipt["attempt_id"]
            and run["status"] == receipt["process_status"], "Terminal event identity mismatch")
    payload = {"receipt_sha256": receipt["sha256"], "run_status": receipt["run_status"],
               "exit_code": receipt["exit_code"], "timeout": receipt["timeout"],
               "process_started": receipt["process_started"], "assessment": receipt["assessment"]}
    if "stop_reason" in receipt:
        payload["stop_reason"] = receipt["stop_reason"]
    kind = "RESULT_READY" if receipt["run_status"] == "SUCCEEDED" else "JOB_FAILED"
    if receipt["timeout"] is True:
        kind = "RESOURCE_LIMIT"
        payload["limit"] = {"cause": "PROCESS_TIMEOUT", "resource": "wall_seconds",
                            "seconds": run["manifest"]["timeout_seconds"]}
    elif receipt.get("stop_reason") == "CAMPAIGN_DEADLINE":
        kind = "RESOURCE_LIMIT"
        payload["limit"] = {"cause": "CAMPAIGN_DEADLINE", "resource": "wall_seconds"}
    return _publish_lifecycle(store, db, run, kind, payload)


def _integer(value, name, minimum=0, maximum=MAX_INTEGER):
    require(type(value) is int and minimum <= value <= maximum, "Invalid " + name)


def _identifier(value, name):
    # Match ProjectStore's existing run-ID character contract (including Unicode).
    require(isinstance(value, str) and 1 <= len(value) <= 80
            and all(c.isalnum() or c in "-_" for c in value), "Invalid " + name)


def _json_value(value, depth=0):
    require(depth <= 32, "Payload nesting exceeds 32")
    if type(value) is dict:
        require(all(type(k) is str for k in value), "Payload keys must be strings")
        for item in value.values():
            _json_value(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
    else:
        require(type(value) in (str, int, float, bool, type(None)), "Invalid JSON value")


def validate_event(event):
    """Validate the v1 envelope; return immutable canonical JSON bytes as text."""
    require(type(event) is dict and set(event) == FIELDS, "Invalid event fields")
    require(type(event["schema_version"]) is int and event["schema_version"] == 1,
            "Unsupported event schema_version")
    _identifier(event["job_id"], "job_id")
    require(isinstance(event["attempt_id"], str)
            and re.fullmatch(r"[0-9a-f]{32}", event["attempt_id"]), "Invalid attempt_id")
    _integer(event["sequence"], "sequence", 1, MAX_SEQUENCE)
    require(isinstance(event["kind"], str) and event["kind"] in KINDS, "Invalid event kind")
    require(type(event["payload"]) is dict, "Payload must be an object")
    _json_value(event["payload"])
    try:
        raw = canonical(event)
        require(len(raw.encode("utf-8")) <= MAX_BYTES, "Event exceeds 65536 bytes")
    except (TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("Event has no bounded canonical JSON encoding") from exc
    return raw


class Outbox:
    """Bind a borrowed connection to its store; every operation needs BEGIN.

    Writers should use BEGIN IMMEDIATE in store._db(), including consumers.
    Reads may use BEGIN in store.readonly(). initialize() is explicit and adds
    only outbox-owned tables, an index and local outbox integrity triggers
    without mutating connection PRAGMAs.
    """

    def __init__(self, store, db):
        self.store, self.db = store, db
        path = next((row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"), "")
        require(path and Path(path).resolve() == store.path.resolve(), "Outbox needs the existing project ledger")
        self._active()
        store._contract(db)

    def _active(self):
        require(self.db.in_transaction, "Outbox requires the caller's active transaction")

    def initialize(self):
        self._active()
        # executescript implicitly commits in legacy SQLite mode: never use it.
        statements = [
            """CREATE TABLE IF NOT EXISTS outbox_events(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 2147483647),
                body TEXT NOT NULL, UNIQUE(job_id,attempt_id,sequence),
                FOREIGN KEY(job_id) REFERENCES runs(id))""",
            """CREATE TABLE IF NOT EXISTS outbox_subscriptions(
                consumer_id TEXT PRIMARY KEY, watermark INTEGER NOT NULL DEFAULT 0 CHECK(watermark>=0))""",
            """CREATE TABLE IF NOT EXISTS outbox_deliveries(
                consumer_id TEXT NOT NULL, event_id INTEGER NOT NULL,
                attempts INTEGER NOT NULL CHECK(attempts BETWEEN 1 AND 2147483647),
                token TEXT, retry_at INTEGER NOT NULL CHECK(retry_at>=0),
                acked INTEGER NOT NULL DEFAULT 0 CHECK(acked IN (0,1)),
                PRIMARY KEY(consumer_id,event_id),
                FOREIGN KEY(consumer_id) REFERENCES outbox_subscriptions(consumer_id),
                FOREIGN KEY(event_id) REFERENCES outbox_events(event_id))""",
            "CREATE INDEX IF NOT EXISTS outbox_retry ON outbox_deliveries(consumer_id,acked,retry_at)",
            """CREATE TRIGGER IF NOT EXISTS outbox_events_valid_insert
                BEFORE INSERT ON outbox_events WHEN
                NOT EXISTS(SELECT 1 FROM runs WHERE id=NEW.job_id) OR
                EXISTS(SELECT 1 FROM outbox_events WHERE event_id=NEW.event_id
                    OR (job_id=NEW.job_id AND attempt_id=NEW.attempt_id AND sequence=NEW.sequence))
                BEGIN SELECT RAISE(ABORT,'invalid or duplicate outbox event'); END""",
            """CREATE TRIGGER IF NOT EXISTS outbox_subscriptions_valid_insert
                BEFORE INSERT ON outbox_subscriptions WHEN
                NEW.consumer_id='' OR NEW.watermark!=0 OR
                EXISTS(SELECT 1 FROM outbox_subscriptions WHERE consumer_id=NEW.consumer_id)
                BEGIN SELECT RAISE(ABORT,'invalid outbox subscription insert'); END""",
            """CREATE TRIGGER IF NOT EXISTS outbox_subscriptions_valid_update
                BEFORE UPDATE ON outbox_subscriptions WHEN
                NEW.consumer_id!=OLD.consumer_id OR NEW.watermark<OLD.watermark OR
                (NEW.watermark>0 AND NOT EXISTS(SELECT 1 FROM outbox_events WHERE event_id=NEW.watermark)) OR
                EXISTS(SELECT 1 FROM outbox_events e LEFT JOIN outbox_deliveries d
                    ON d.event_id=e.event_id AND d.consumer_id=NEW.consumer_id
                    WHERE e.event_id<=NEW.watermark AND COALESCE(d.acked,0)=0)
                BEGIN SELECT RAISE(ABORT,'invalid outbox watermark update'); END""",
            """CREATE TRIGGER IF NOT EXISTS outbox_subscriptions_no_delete
                BEFORE DELETE ON outbox_subscriptions
                BEGIN SELECT RAISE(ABORT,'outbox subscriptions cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS outbox_deliveries_valid_insert
                BEFORE INSERT ON outbox_deliveries WHEN
                NOT EXISTS(SELECT 1 FROM outbox_subscriptions WHERE consumer_id=NEW.consumer_id) OR
                NOT EXISTS(SELECT 1 FROM outbox_events WHERE event_id=NEW.event_id) OR
                EXISTS(SELECT 1 FROM outbox_deliveries WHERE consumer_id=NEW.consumer_id AND event_id=NEW.event_id) OR
                NEW.attempts!=1 OR NEW.acked!=0 OR NEW.token IS NULL OR length(NEW.token)!=32
                BEGIN SELECT RAISE(ABORT,'invalid outbox delivery insert'); END""",
            """CREATE TRIGGER IF NOT EXISTS outbox_deliveries_valid_update
                BEFORE UPDATE ON outbox_deliveries WHEN
                OLD.acked!=0 OR NEW.consumer_id!=OLD.consumer_id OR NEW.event_id!=OLD.event_id OR
                NOT ((NEW.acked=0 AND NEW.attempts=OLD.attempts+1 AND NEW.token IS NOT NULL AND length(NEW.token)=32) OR
                     (NEW.acked=0 AND NEW.attempts=OLD.attempts AND NEW.token IS NULL) OR
                     (NEW.acked=1 AND NEW.attempts=OLD.attempts AND OLD.token IS NOT NULL
                      AND NEW.token=OLD.token AND NEW.retry_at=OLD.retry_at))
                BEGIN SELECT RAISE(ABORT,'invalid outbox delivery update'); END""",
            """CREATE TRIGGER IF NOT EXISTS outbox_deliveries_no_delete
                BEFORE DELETE ON outbox_deliveries
                BEGIN SELECT RAISE(ABORT,'outbox deliveries cannot be deleted'); END""",
        ]
        for action in ("UPDATE", "DELETE"):
            statements.append(f"CREATE TRIGGER IF NOT EXISTS outbox_events_no_{action.lower()} "
                              f"BEFORE {action} ON outbox_events BEGIN "
                              "SELECT RAISE(ABORT,'outbox events are immutable'); END")
        for statement in statements:
            self.db.execute(statement)

    def publish(self, event):
        self._active()
        raw = validate_event(event)
        run = self.store._run(self.db, event["job_id"])
        require(run["attempt_id"] == event["attempt_id"], "Event does not name the ledger attempt")
        identity = (event["job_id"], event["attempt_id"], event["sequence"])
        old = self.db.execute("SELECT event_id,body FROM outbox_events WHERE job_id=? AND attempt_id=? AND sequence=?",
                              identity).fetchone()
        if old is not None:
            require(old[1] == raw, "Conflicting event identity")
            return old[0]
        last = self.db.execute("SELECT COALESCE(MAX(sequence),0) FROM outbox_events WHERE job_id=? AND attempt_id=?",
                               identity[:2]).fetchone()[0]
        require(event["sequence"] == last + 1, "Event sequence must be contiguous")
        return self.db.execute("INSERT INTO outbox_events(job_id,attempt_id,sequence,body) VALUES (?,?,?,?)",
                               (*identity, raw)).lastrowid

    def subscribe(self, consumer_id):
        self._active()
        _identifier(consumer_id, "consumer_id")
        self.db.execute("INSERT INTO outbox_subscriptions(consumer_id) SELECT ? "
                        "WHERE NOT EXISTS(SELECT 1 FROM outbox_subscriptions WHERE consumer_id=?)",
                        (consumer_id, consumer_id))
        return self.watermark(consumer_id)

    def watermark(self, consumer_id):
        self._active()
        _identifier(consumer_id, "consumer_id")
        row = self.db.execute("SELECT watermark FROM outbox_subscriptions WHERE consumer_id=?", (consumer_id,)).fetchone()
        require(row is not None, "Unknown subscription")
        return row[0]

    def pending(self, consumer_id, *, now, limit=100, include_delayed=False):
        watermark = self.watermark(consumer_id)
        _integer(now, "now")
        _integer(limit, "limit", 1, 1000)
        require(type(include_delayed) is bool, "Invalid include_delayed")
        rows = self.db.execute("""SELECT e.event_id,e.body,COALESCE(d.attempts,0),COALESCE(d.retry_at,0)
            FROM outbox_events e LEFT JOIN outbox_deliveries d
            ON d.event_id=e.event_id AND d.consumer_id=?
            WHERE e.event_id>? AND COALESCE(d.acked,0)=0 AND (? OR COALESCE(d.retry_at,0)<=?)
            ORDER BY e.event_id LIMIT ?""", (consumer_id, watermark, include_delayed, now, limit))
        return [{"event_id": r[0], "event": json.loads(r[1]), "attempts": r[2], "retry_at": r[3]} for r in rows]

    def claim(self, consumer_id, event_id, *, now, lease_seconds=60):
        """Lease one due notification. None means acknowledged or still leased."""
        watermark = self.watermark(consumer_id)
        _integer(event_id, "event_id", 1)
        _integer(now, "now")
        _integer(lease_seconds, "lease_seconds", 1, 86400)
        _integer(now + lease_seconds, "lease expiry")
        event = self.db.execute("SELECT body FROM outbox_events WHERE event_id=?", (event_id,)).fetchone()
        require(event is not None, "Unknown event")
        old = self.db.execute("SELECT attempts,retry_at,acked FROM outbox_deliveries WHERE consumer_id=? AND event_id=?",
                              (consumer_id, event_id)).fetchone()
        if event_id <= watermark or (old is not None and (old[2] or old[1] > now)):
            return None
        attempts = old[0] + 1 if old else 1
        _integer(attempts, "delivery attempts", 1, MAX_SEQUENCE)
        token = uuid.uuid4().hex
        if old is None:
            self.db.execute("INSERT INTO outbox_deliveries(consumer_id,event_id,attempts,token,retry_at) "
                            "VALUES (?,?,1,?,?)", (consumer_id, event_id, token, now + lease_seconds))
        else:
            self.db.execute("UPDATE outbox_deliveries SET attempts=?,token=?,retry_at=? "
                            "WHERE consumer_id=? AND event_id=?",
                            (attempts, token, now + lease_seconds, consumer_id, event_id))
        return {"event_id": event_id, "event": json.loads(event[0]), "delivery_token": token,
                "attempts": attempts, "retry_at": now + lease_seconds}

    def _delivery(self, consumer_id, event_id, token, now):
        self.watermark(consumer_id)
        _integer(event_id, "event_id", 1)
        _integer(now, "now")
        require(isinstance(token, str) and re.fullmatch(r"[0-9a-f]{32}", token), "Invalid delivery token")
        row = self.db.execute("SELECT token,retry_at,acked FROM outbox_deliveries WHERE consumer_id=? AND event_id=?",
                              (consumer_id, event_id)).fetchone()
        require(row is not None and row[0] == token, "Foreign or superseded delivery")
        require(row[2] or now < row[1], "Delivery lease expired")
        return row

    def retry(self, consumer_id, event_id, token, *, now, delay_seconds):
        _integer(delay_seconds, "delay_seconds", 0, 86400)
        _integer(now, "now")
        _integer(now + delay_seconds, "retry expiry")
        row = self._delivery(consumer_id, event_id, token, now)
        require(not row[2], "Acknowledged event cannot retry")
        self.db.execute("UPDATE outbox_deliveries SET token=NULL,retry_at=? WHERE consumer_id=? AND event_id=?",
                        (now + delay_seconds, consumer_id, event_id))

    def ack(self, consumer_id, event_id, token, *, now):
        """ACK one exact lease; repeat ACK is a no-op. No payload grants authority."""
        row = self._delivery(consumer_id, event_id, token, now)
        if row[2]:
            return False
        self.db.execute("UPDATE outbox_deliveries SET acked=1 WHERE consumer_id=? AND event_id=?", (consumer_id, event_id))
        first_pending = self.db.execute("""SELECT MIN(e.event_id) FROM outbox_events e
            LEFT JOIN outbox_deliveries d ON d.event_id=e.event_id AND d.consumer_id=?
            WHERE COALESCE(d.acked,0)=0""", (consumer_id,)).fetchone()[0]
        high = (self.db.execute("SELECT COALESCE(MAX(event_id),0) FROM outbox_events WHERE event_id<?",
                                (first_pending,)).fetchone()[0] if first_pending is not None else
                self.db.execute("SELECT COALESCE(MAX(event_id),0) FROM outbox_events").fetchone()[0])
        self.db.execute("UPDATE outbox_subscriptions SET watermark=? WHERE consumer_id=?", (high, consumer_id))
        return True
