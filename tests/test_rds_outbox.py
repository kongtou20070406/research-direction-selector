"""Deterministic staged outbox tests on real ProjectStore SQLite ledgers."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_outbox import KINDS, MAX_BYTES, MAX_INTEGER, Outbox, validate_event
from rds_project import ProjectStore, RunIntegrityError, canonical, file_sha

ATTEMPT = "a" * 32


class OutboxTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".autorun").mkdir(exist_ok=True)
        tmp = tempfile.TemporaryDirectory(prefix="b6-test-", dir=ROOT / ".autorun")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, body in {"code.py": "pass\n", "config.json": "{}", "data.json": "[]",
                           "evaluator.json": "{}"}.items():
            (self.root / name).write_text(body, encoding="utf-8")
        protocol = {role + "_sha256": file_sha(self.root / name) for role, name in
                    [("code", "code.py"), ("config", "config.json"), ("data", "data.json")]}
        protocol.update(data_split="synthetic", init="none", seed=0, checkpoint="none",
                        schedule="none", sample_work={"rows": 0}, numeric_protocol="Python")
        (self.root / "protocol.json").write_text(canonical(protocol), encoding="utf-8")
        contract = {"schema": 1, "bindings": [{"role": r, "path": n, "sha256": file_sha(self.root / n)}
                    for r, n in [("code", "code.py"), ("config", "config.json"), ("data", "data.json"),
                                 ("evaluator", "evaluator.json"), ("protocol", "protocol.json")]],
                    "allowed_commands": [[sys.executable, "-B", "code.py"]], "output_roots": ["outputs"],
                    "budget": {"wall_seconds": 10, "cpu_seconds": 10, "gpu_seconds": 0}}
        self.store = ProjectStore(self.root)
        self.store.initialize(contract)
        for rid in ("r1", "r2"):
            self.store.register({"schema": 1, "id": rid, "arm": "control", "control_id": None,
                "protocol": {"path": "protocol.json", "sha256": file_sha(self.root / "protocol.json")},
                "argv": [sys.executable, "-B", "code.py"], "outpaths": [f"outputs/{rid}.json"],
                "resource_estimates": {"wall_seconds": 1, "cpu_seconds": 1, "gpu_seconds": 0},
                "timeout_seconds": 0.5})
        with self.tx() as out:
            # Synthetic attempt admission only; no child commands run in these fixtures.
            run = self.store._run(out.db, "r1")
            run["attempt_id"] = ATTEMPT
            self.store._save(out.db, run)
            out.initialize()
            out.subscribe("alpha")

    @contextmanager
    def tx(self):
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            yield Outbox(self.store, db)

    def event(self, sequence=1, kind="JOB_STARTED", **changes):
        return {"schema_version": 1, "job_id": "r1", "attempt_id": ATTEMPT,
                "sequence": sequence, "kind": kind, "payload": {"fact": "synthetic"}, **changes}

    def publish(self, count=1):
        with self.tx() as out:
            return [out.publish(self.event(i)) for i in range(1, count + 1)]

    def test_atomic_ledger_publish_rollback_visibility_and_reopen(self):
        before = self.store.snapshot()
        with self.assertRaisesRegex(RuntimeError, "crash"):
            with self.tx() as out:
                out.db.execute("UPDATE budget SET spent=spent+1 WHERE resource='wall_seconds'")
                out.publish(self.event())
                with self.store._db(True) as reader:
                    self.assertEqual(reader.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0], 0)
                raise RuntimeError("crash before commit")
        self.assertEqual(self.store.snapshot(), before)
        with self.tx() as out:
            out.db.execute("UPDATE budget SET spent=spent+1 WHERE resource='wall_seconds'")
            eid = out.publish(self.event())
            self.assertTrue(out.db.in_transaction)
        self.store = ProjectStore(self.root)
        with self.tx() as out:
            self.assertEqual(out.pending("alpha", now=0)[0]["event_id"], eid)
        with self.store._db(True) as db:
            self.assertEqual(db.execute("SELECT spent FROM budget WHERE resource='wall_seconds'").fetchone()[0], 1)

    def test_schema_initialization_borrows_transaction_and_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.tx() as out:
                out.db.execute("DROP TABLE outbox_deliveries")
                out.db.execute("DROP TABLE outbox_subscriptions")
                out.db.execute("DROP TABLE outbox_events")
                out.initialize()
                out.publish(self.event())
                raise RuntimeError("rollback DDL and event")
        with self.tx() as out:
            self.assertEqual(out.pending("alpha", now=0), [])
            out.initialize()
            self.assertEqual(out.subscribe("alpha"), 0)

    def test_uncommitted_connection_close_and_committed_ack_recovery(self):
        db = sqlite3.connect(self.store.path)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            out = Outbox(self.store, db)
            out.publish(self.event())
            db.execute("UPDATE budget SET charged=charged+2 WHERE resource='cpu_seconds'")
        finally:
            db.close()  # SQLite rolls back an abandoned transaction on this real ledger.
        self.store = ProjectStore(self.root)
        with self.tx() as out:
            self.assertEqual(out.pending("alpha", now=0), [])
            self.assertEqual(out.db.execute("SELECT charged FROM budget WHERE resource='cpu_seconds'").fetchone()[0], 0)
            eid = out.publish(self.event())
            delivery = out.claim("alpha", eid, now=0)
            out.ack("alpha", eid, delivery["delivery_token"], now=1)
        self.store = ProjectStore(self.root)
        with self.tx() as out:
            self.assertEqual(out.watermark("alpha"), eid)
            self.assertEqual(out.pending("alpha", now=100), [])
            self.assertFalse(out.ack("alpha", eid, delivery["delivery_token"], now=100))

    def test_new_attempt_identity_keeps_historical_delivery(self):
        eid = self.publish()[0]
        with self.tx() as out:
            run = self.store._run(out.db, "r1")
            run["attempt_id"] = "b" * 32
            self.store._save(out.db, run)
            with self.assertRaises(ValueError):
                out.publish(self.event(2))
            newer = out.publish(self.event(attempt_id="b" * 32))
            self.assertNotEqual(newer, eid)
            old = out.claim("alpha", eid, now=0)
            self.assertEqual(old["event"]["attempt_id"], ATTEMPT)
            out.ack("alpha", eid, old["delivery_token"], now=1)
            self.assertEqual(out.pending("alpha", now=1)[0]["event_id"], newer)

    def test_duplicate_envelope_conflict_and_immutable_payload(self):
        event = self.event(payload={"z": [1], "a": 2})
        with self.tx() as out:
            eid = out.publish(event)
            self.assertEqual(out.publish(self.event(payload={"a": 2, "z": [1]})), eid)
            for change in ({"payload": {"different": True}}, {"kind": "RESULT_READY"}):
                with self.assertRaisesRegex(ValueError, "Conflicting"):
                    out.publish({**event, **change})
            event["payload"]["z"].append(9)
            self.assertEqual(out.pending("alpha", now=0)[0]["event"]["payload"]["z"], [1])
            for sql in ("UPDATE outbox_events SET body='{}'", "DELETE FROM outbox_events"):
                with self.assertRaises(sqlite3.IntegrityError):
                    out.db.execute(sql)

    def test_attempts_sequences_and_original_validator(self):
        with self.tx() as out:
            for event in (self.event(job_id="missing"), self.event(job_id="r2"),
                          self.event(attempt_id="b" * 32), self.event(2)):
                with self.assertRaises(ValueError):
                    out.publish(event)
            out.publish(self.event())
            with self.assertRaises(ValueError):
                out.publish(self.event(3))
            run = self.store._run(out.db, "r2")
            run["attempt_id"] = "b" * 32
            self.store._save(out.db, run)
            out.publish(self.event(job_id="r2", attempt_id="b" * 32))
            out.db.execute("UPDATE runs SET body='{}' WHERE id='r1'")
            with self.assertRaises(RunIntegrityError):
                out.publish(self.event(2))

    def test_all_event_kinds_are_data_and_accounting_is_unchanged(self):
        before = self.store.snapshot()
        with self.tx() as out:
            for seq, kind in enumerate(sorted(KINDS), 1):
                out.publish(self.event(seq, kind, payload={"authorized": True, "status": "PASS"}))
            self.assertEqual(len(out.pending("alpha", now=0)), 6)
        self.assertEqual(self.store.snapshot(), before)

    def test_out_of_order_ack_watermark_and_consumer_isolation(self):
        ids = self.publish(3)
        with self.tx() as out:
            out.subscribe("beta")
            claims = [out.claim("alpha", eid, now=10) for eid in ids]
            beta = out.claim("beta", ids[1], now=10)
            with self.assertRaisesRegex(ValueError, "Foreign"):
                out.ack("beta", ids[1], claims[1]["delivery_token"], now=11)
            for i, expected in ((1, 0), (0, ids[1]), (2, ids[2])):
                self.assertTrue(out.ack("alpha", ids[i], claims[i]["delivery_token"], now=11))
                self.assertEqual(out.watermark("alpha"), expected)
            self.assertFalse(out.ack("alpha", ids[2], claims[2]["delivery_token"], now=999))
            self.assertIsNone(out.claim("alpha", ids[2], now=999))
            self.assertEqual(out.watermark("beta"), 0)
            self.assertTrue(out.ack("beta", ids[1], beta["delivery_token"], now=11))
            self.assertEqual([r["event_id"] for r in out.pending("beta", now=100)], [ids[0], ids[2]])

    def test_retry_lease_crash_recovery_and_late_foreign_ack(self):
        eid = self.publish()[0]
        with self.tx() as out:
            first = out.claim("alpha", eid, now=0, lease_seconds=5)
            self.assertIsNone(out.claim("alpha", eid, now=1))
            self.assertEqual(out.pending("alpha", now=1), [])
            self.assertEqual(len(out.pending("alpha", now=1, include_delayed=True)), 1)
        self.store = ProjectStore(self.root)
        with self.tx() as out:
            with self.assertRaises(ValueError):
                out.ack("alpha", eid, first["delivery_token"], now=5)
            second = out.claim("alpha", eid, now=5)
            self.assertEqual(second["attempts"], 2)
            with self.assertRaises(ValueError):
                out.ack("alpha", eid, first["delivery_token"], now=6)
            out.retry("alpha", eid, second["delivery_token"], now=6, delay_seconds=10)
            with self.assertRaises(ValueError):
                out.ack("alpha", eid, second["delivery_token"], now=7)
            self.assertEqual(out.pending("alpha", now=15), [])
            self.assertEqual(out.pending("alpha", now=16)[0]["attempts"], 2)
            third = out.claim("alpha", eid, now=16)
            with self.assertRaises(ValueError):
                out.ack("alpha", eid + 1, third["delivery_token"], now=17)
            out.ack("alpha", eid, third["delivery_token"], now=17)

    def test_consumer_effect_ack_and_claim_rollback(self):
        eid = self.publish()[0]
        with self.assertRaises(RuntimeError):
            with self.tx() as out:
                out.claim("alpha", eid, now=0)
                raise RuntimeError("claim crash")
        with self.tx() as out:
            delivery = out.claim("alpha", eid, now=0)
            self.assertEqual(delivery["attempts"], 1)
        token = delivery["delivery_token"]
        with self.assertRaises(RuntimeError):
            with self.tx() as out:
                out.db.execute("INSERT INTO events(body) VALUES (?)", ('{"consumer_effect":true}',))
                out.ack("alpha", eid, token, now=1)
                raise RuntimeError("effect crash")
        with self.tx() as out:
            self.assertEqual(out.watermark("alpha"), 0)
            self.assertEqual(out.db.execute("SELECT COUNT(*) FROM events WHERE body=?",
                                           ('{"consumer_effect":true}',)).fetchone()[0], 0)
            for _ in range(2):
                if out.ack("alpha", eid, token, now=2):
                    out.db.execute("INSERT INTO events(body) VALUES (?)", ('{"consumer_effect":true}',))
            self.assertEqual(out.db.execute("SELECT COUNT(*) FROM events WHERE body=?",
                                           ('{"consumer_effect":true}',)).fetchone()[0], 1)

    def test_validation_bounds_and_connection_scope(self):
        invalid = [{"schema_version": True}, {"schema_version": 2}, {"job_id": "../r1"},
                   {"job_id": "r" * 81}, {"attempt_id": "bad"}, {"sequence": True},
                   {"sequence": 0}, {"sequence": 2**31}, {"kind": "OTHER"}, {"kind": []},
                   {"payload": []}, {"payload": {1: "key"}}, {"payload": {"x": (1,)}},
                   {"payload": {"x": float("nan")}}, {"payload": {"x": "\ud800"}},
                   {"payload": {"x": "x" * MAX_BYTES}}, {"extra": 1}]
        nested = {}
        for _ in range(33):
            nested = {"x": nested}
        invalid.append({"payload": nested})
        for change in invalid:
            with self.subTest(change=str(change)[:80]), self.assertRaises(ValueError):
                validate_event(self.event(**change))
        with self.store._db(True) as db:
            with self.assertRaises(ValueError):
                Outbox(self.store, db)
            db.execute("BEGIN")
            out = Outbox(self.store, db)
            for changes in ({"now": -1}, {"limit": 0}, {"limit": 1001}, {"include_delayed": 1}):
                with self.assertRaises(ValueError):
                    out.pending("alpha", **{"now": 0, **changes})
            for consumer in ("unknown", "", "../consumer"):
                with self.assertRaises(ValueError):
                    out.pending(consumer, now=0)
        eid = self.publish()[0]
        with self.tx() as out:
            for args in ({"now": MAX_INTEGER}, {"now": 0, "lease_seconds": 0},
                         {"now": 0, "lease_seconds": 86401}):
                with self.assertRaises(ValueError):
                    out.claim("alpha", eid, **args)
        with sqlite3.connect(":memory:") as db:
            db.execute("BEGIN")
            with self.assertRaises(ValueError):
                Outbox(self.store, db)

    def test_f1_ack_and_subscription_records_cannot_be_deleted_or_replaced(self):
        ids = self.publish(2)
        with self.tx() as out:
            for eid in ids:
                delivery = out.claim("alpha", eid, now=0)
                out.ack("alpha", eid, delivery["delivery_token"], now=1)
            for sql, params in [
                ("DELETE FROM outbox_deliveries WHERE acked=1", ()),
                ("DELETE FROM outbox_subscriptions WHERE consumer_id=?", ("alpha",)),
                ("UPDATE outbox_subscriptions SET watermark=0 WHERE consumer_id=?", ("alpha",)),
                ("INSERT OR REPLACE INTO outbox_subscriptions VALUES (?,0)", ("alpha",)),
                ("INSERT OR REPLACE INTO outbox_deliveries VALUES (?,?,1,?,60,0)", ("alpha", ids[0], "b" * 32)),
                ("UPDATE outbox_deliveries SET acked=0 WHERE consumer_id=?", ("alpha",)),
            ]:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    out.db.execute(sql, params)
            self.assertEqual(out.watermark("alpha"), ids[-1])
            self.assertEqual(out.pending("alpha", now=100), [])
            self.assertIsNone(out.claim("alpha", ids[0], now=100))
            self.assertEqual(out.subscribe("alpha"), ids[-1])

    def test_f1_watermarks_start_at_zero_and_cover_only_acknowledged_prefix(self):
        ids = self.publish(3)
        with self.tx() as out:
            for sql, params in [
                ("INSERT INTO outbox_subscriptions VALUES ('forged',5)", ()),
                ("INSERT INTO outbox_subscriptions(consumer_id,watermark) VALUES ('fakeconsumer',5)", ()),
                ("INSERT INTO outbox_deliveries VALUES (?,?,1,?,60,1)", ("alpha", ids[0], "b" * 32)),
                ("UPDATE outbox_subscriptions SET watermark=? WHERE consumer_id='alpha'", (ids[-1],)),
                ("UPDATE outbox_subscriptions SET consumer_id='renamed' WHERE consumer_id='alpha'", ()),
            ]:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    out.db.execute(sql, params)
            delivery = out.claim("alpha", ids[1], now=0)
            out.ack("alpha", ids[1], delivery["delivery_token"], now=1)
            with self.assertRaises(sqlite3.IntegrityError):
                out.db.execute("UPDATE outbox_subscriptions SET watermark=? WHERE consumer_id='alpha'", (ids[1],))
            self.assertEqual(out.watermark("alpha"), 0)
            self.assertEqual([r["event_id"] for r in out.pending("alpha", now=100)], [ids[0], ids[2]])
            for eid in (ids[0], ids[2]):
                delivery = out.claim("alpha", eid, now=100)
                out.ack("alpha", eid, delivery["delivery_token"], now=101)
            with self.assertRaises(sqlite3.IntegrityError):
                out.db.execute("UPDATE outbox_subscriptions SET watermark=? WHERE consumer_id='alpha'", (ids[-1] + 1,))

    def test_f1_orphan_references_are_rejected_without_changing_foreign_keys(self):
        eid = self.publish()[0]
        with self.tx() as out:
            setting = out.db.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(setting, 0)
            out.initialize()
            self.assertEqual(out.db.execute("PRAGMA foreign_keys").fetchone()[0], setting)
            event = self.event(job_id="missing")
            sql = "INSERT INTO outbox_events(job_id,attempt_id,sequence,body) VALUES (?,?,?,?)"
            with self.assertRaises(sqlite3.IntegrityError):
                out.db.execute(sql, ("missing", ATTEMPT, 1, canonical(event)))
            for consumer, event_id in (("missing", eid), ("alpha", eid + 1)):
                with self.subTest(consumer=consumer), self.assertRaises(sqlite3.IntegrityError):
                    out.db.execute("INSERT INTO outbox_deliveries VALUES (?,?,1,?,60,0)",
                                   (consumer, event_id, "b" * 32))
            first = out.claim("alpha", eid, now=0)
            for field, value in (("consumer_id", "missing"), ("event_id", eid + 1), ("attempts", 0)):
                with self.subTest(field=field), self.assertRaises(sqlite3.IntegrityError):
                    out.db.execute(f"UPDATE outbox_deliveries SET {field}=? WHERE event_id=?", (value, eid))
            out.retry("alpha", eid, first["delivery_token"], now=1, delay_seconds=2)
            with self.assertRaises(sqlite3.IntegrityError):
                out.db.execute("UPDATE outbox_deliveries SET acked=1 WHERE consumer_id='alpha' AND event_id=?", (eid,))
            second = out.claim("alpha", eid, now=3)
            self.assertEqual(second["attempts"], 2)
            out.ack("alpha", eid, second["delivery_token"], now=4)
            self.assertEqual(out.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_schema_matches_envelope_contract(self):
        schema = json.loads((ROOT / "schemas/rds-event.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(self.event()))
        self.assertEqual(set(schema["properties"]["kind"]["enum"]), KINDS)
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
