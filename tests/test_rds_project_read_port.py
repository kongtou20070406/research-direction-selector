"""Real temporary ProjectStore ledgers; borrowed-transaction projections only."""

import ast
import builtins
from contextlib import closing
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sqlite3
import sys
from types import MappingProxyType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "scripts")]

import test_rds_project as fixtures
import rds_project as project
import rds_project_read_port as port


def thaw(value):
    if hasattr(value, "items"):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def ledger_rows(db):
    return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("contract", "budget", "runs", "receipts", "events", "exposures", "output_claims")}


def signature(exc):
    if exc is None:
        return None
    return (type(exc), str(exc).encode("utf-8"), exc.args, exc.__suppress_context__,
            signature(exc.__cause__), signature(exc.__context__))


class ProjectReadPortTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.ProjectTests("test_execution_policy_is_opt_in_and_frozen")
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.store = self.fx.store
        self.retained = self.fx.run_spec(self.fx.spec("r1", timeout=8))
        self.assertEqual(self.retained["run_status"], "SUCCEEDED", self.retained)
        self.assertFalse(self.retained["timeout"], self.retained)
        self.store.register(self.fx.spec("r2", timeout=8))
        self.db = sqlite3.connect(self.store.path)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute("BEGIN")

    def view(self, **kwargs):
        return port.ProjectReadPort(self.db, integrity_error=project.ReceiptIntegrityError,
                                    digest=project.digest, **kwargs)

    def assert_immutable_tree(self, original, actual):
        pending = [(original, actual)]
        while pending:
            old, new = pending.pop()
            if isinstance(old, dict):
                self.assertIsInstance(new, MappingProxyType)
                self.assertEqual(set(old), set(new))
                with self.assertRaises(TypeError):
                    new["mutation"] = None
                pending.extend((value, new[key]) for key, value in old.items())
            elif isinstance(old, list):
                self.assertIsInstance(new, tuple)
                self.assertEqual(len(old), len(new))
                with self.assertRaises(TypeError):
                    new[0] = None
                pending.extend(zip(old, new))
            else:
                self.assertIs(type(new), type(old))
                self.assertEqual(new, old)

    def check_deep_receipt(self, depth, mixed):
        # Add adversarial data to a real API-produced temporary receipt and reseal
        # it with the original digest. No recursive builder/comparator is used.
        self.db.rollback()
        nested = {"leaves": [None, True, False, 0, -0.0, 1.25, "UNKNOWN", "\u4e2d\u6587"]}
        for index in range(depth):
            nested = {"child": nested} if mixed and index % 2 else [nested]
        body = {key: value for key, value in self.retained.items() if key != "sha256"}
        body["depth_vector"] = nested
        sha = project.digest(body)
        text = project.canonical({**body, "sha256": sha})
        self.assertLess(len(text.encode("utf-8")), 65536)
        with self.store._db() as writer:
            writer.execute("DROP TRIGGER IF EXISTS receipts_no_update")
            writer.execute("UPDATE receipts SET sha256=?,body=? WHERE run_id='r1'", (sha, text))
        self.db.execute("BEGIN")
        baseline = ledger_rows(self.db)
        raw = self.db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id='r1'").fetchone()
        limit = sys.getrecursionlimit()
        old = project.ProjectStore._receipt(raw)
        actual = self.view().receipt("r1")
        self.assert_immutable_tree(old, actual)
        self.assertEqual(actual["sha256"], sha)
        self.assertEqual(actual["assessment"]["task_gain"], "UNKNOWN")
        self.assertEqual(sys.getrecursionlimit(), limit)
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(ledger_rows(self.db), baseline)

    def test_deep_list_receipts_match_original_without_recursive_test_helpers(self):
        for depth in (512, 768):
            with self.subTest(depth=depth):
                self.check_deep_receipt(depth, mixed=False)

    def test_deep_mixed_receipts_match_original_without_recursive_test_helpers(self):
        for depth in (512, 768):
            with self.subTest(depth=depth):
                self.check_deep_receipt(depth, mixed=True)


    def test_raw_records_exact_immutable_missing_and_literal_ids(self):
        view = self.view()
        raw = tuple(self.db.execute("SELECT id,sha256,body FROM contract WHERE id=1").fetchone())
        genesis = view.genesis_contract_record()
        self.assertEqual((genesis.id, genesis.sha256, genesis.body), raw)
        self.assertEqual(genesis.kind, "GENESIS")
        with self.assertRaises(FrozenInstanceError):
            genesis.body = "replacement"
        run = view.run_record("r2")
        expected = tuple(self.db.execute("SELECT id,status,body FROM runs WHERE id='r2'").fetchone())
        self.assertEqual((run.id, run.status, run.body), expected)
        with self.assertRaises(FrozenInstanceError):
            run.status = "PASS"
        for key in ("missing", "r1' OR 1=1 --", "D:/Myfile/work space/RDS"):
            self.assertIsNone(view.run_record(key))
        view.close()
        self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("DROP TRIGGER contract_no_delete")
        self.db.execute("DELETE FROM contract WHERE id=1")
        self.assertIsNone(self.view().genesis_contract_record())

    def test_raw_run_pages_original_order_lookahead_and_parameter_binding(self):
        self.db.rollback()
        self.store.register(self.fx.spec("r3"))
        self.db.execute("BEGIN")
        view = self.view(max_rows=2)
        first = view.run_records(limit=2)
        self.assertEqual([r.id for r in first.rows], ["r1", "r2"])
        self.assertTrue(first.has_more)
        self.assertEqual(first.next_after, "r2")
        second = view.run_records(limit=2, after=first.next_after)
        self.assertEqual([r.id for r in second.rows], ["r3"])
        self.assertFalse(second.has_more)
        self.assertIsNone(second.next_after)
        self.assertEqual(view.run_records(limit=1, after="r9").rows, ())
        key = "r1' OR 1=1 --"
        expected = [tuple(row) for row in self.db.execute("SELECT id,status,body FROM runs WHERE id>? ORDER BY id LIMIT 2", (key,))]
        actual = view.run_records(limit=2, after=key)
        self.assertEqual([(r.id, r.status, r.body) for r in actual.rows], expected)
        with self.assertRaises(FrozenInstanceError):
            first.has_more = False
        with self.assertRaises(FrozenInstanceError):
            first.rows[0].body = "changed"

    def test_raw_corruption_fields_are_retained_not_validated(self):
        self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE runs SET status=?,body=? WHERE id='r2'", ("BROKEN UNKNOWN \u4e2d", b"\xff\xfe{not-json"))
        self.db.execute("DROP TRIGGER contract_no_update")
        self.db.execute("UPDATE contract SET sha256=?,body=? WHERE id=1", ("unverified-row-hash", "{\u4e2d not JSON"))
        view = self.view()
        before = ledger_rows(self.db)
        with patch.object(project.ProjectStore, "_run_row", side_effect=AssertionError("not a run validator")), \
             patch.object(project.ProjectStore, "_contract", side_effect=AssertionError("not effective contract")), \
             patch.object(port, "decode_receipt", side_effect=AssertionError("not receipt decoding")):
            run = view.run_record("r2")
            genesis = view.genesis_contract_record()
        self.assertEqual(run.status, "BROKEN UNKNOWN \u4e2d")
        self.assertEqual(run.body, b"\xff\xfe{not-json")
        self.assertEqual(genesis.sha256, "unverified-row-hash")
        self.assertEqual(genesis.body, "{\u4e2d not JSON")
        self.assertEqual(ledger_rows(self.db), before)

    def test_raw_genesis_stays_genesis_with_labelled_revision_row_fixture(self):
        # This is a labelled retained-row fixture, NOT a valid method revision.
        # It deliberately does not exercise or claim effective_contract adoption.
        before = tuple(self.db.execute("SELECT id,sha256,body FROM contract WHERE id=1").fetchone())
        self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        revised = {**self.fx.contract, "description": "labelled alternate retained contract"}
        fixture = {"kind": "METHOD_REVISION_ADOPTED", "contract": revised,
                   "sha256": project.digest(revised), "fixture_label": "RETAINED_ROW_ONLY_NOT_VALID_ADOPTION"}
        self.db.execute("INSERT INTO events(body) VALUES (?)", (project.canonical(fixture),))
        view = self.view()
        with patch.object(project.ProjectStore, "_contract", side_effect=AssertionError("must not derive active policy")):
            genesis = view.genesis_contract_record()
        self.assertEqual((genesis.id, genesis.sha256, genesis.body), before)
        self.assertEqual(genesis.kind, "GENESIS")
        self.assertNotEqual(genesis.body, project.canonical(revised))
        self.assertFalse(hasattr(view, "effective_contract"))

    def test_raw_records_keep_wal_snapshot(self):
        view = self.view()
        genesis = view.genesis_contract_record()
        run = view.run_record("r2")
        page = view.run_records(limit=3)
        self.store.execute("r2")
        self.assertEqual(view.genesis_contract_record(), genesis)
        self.assertEqual(view.run_record("r2"), run)
        self.assertEqual(view.run_records(limit=3), page)
        self.assertEqual(run.status, "RESERVED")
        self.assertTrue(self.db.in_transaction)
        view.close()
        self.db.rollback()
        self.db.execute("BEGIN")
        self.assertEqual(self.view().run_record("r2").status, "COMPLETED")

    def test_raw_reads_only_select_no_state_settings_or_seam_callbacks(self):
        before = ledger_rows(self.db)
        changes, factory, isolation = self.db.total_changes, self.db.row_factory, self.db.isolation_level
        statements = []
        self.db.set_trace_callback(statements.append)
        def authorize(action, *args):
            return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY
        self.db.set_authorizer(authorize)
        with patch.object(port, "decode_receipt", side_effect=AssertionError("raw only")), \
             patch.object(port.sqlite3, "connect", side_effect=AssertionError("borrow only")):
            view = self.view()
            view.genesis_contract_record()
            view.run_record("r1")
            view.run_records(limit=2)
            view.run_records(limit=1, after="r1")
            view.close()
        self.assertEqual(len(statements), 8)
        self.assertTrue(all(s.lstrip().startswith("SELECT ") for s in statements))
        self.db.set_authorizer(None)
        self.db.set_trace_callback(None)
        self.assertEqual(ledger_rows(self.db), before)
        self.assertEqual(self.db.total_changes, changes)
        self.assertIs(self.db.row_factory, factory)
        self.assertEqual(self.db.isolation_level, isolation)
        self.assertTrue(self.db.in_transaction)

    def test_raw_byte_boundaries_row_and_aggregate_caps(self):
        queries = [("SELECT coalesce(length(CAST(id AS BLOB)),0)+length(CAST(sha256 AS BLOB))+length(CAST(body AS BLOB)) FROM contract WHERE id=1", "genesis_contract_record", ()),
                   ("SELECT length(CAST(id AS BLOB))+length(CAST(status AS BLOB))+length(CAST(body AS BLOB)) FROM runs WHERE id='r1'", "run_record", ("r1",))]
        for sql, name, args in queries:
            size = self.db.execute(sql).fetchone()[0]
            self.assertIsNotNone(getattr(self.view(max_row_bytes=size, max_result_bytes=size), name)(*args))
            for caps, reason in [({"max_row_bytes": size-1}, "max_row_bytes"), ({"max_result_bytes": size-1}, "max_result_bytes")]:
                with self.subTest(name=name, caps=caps):
                    self.db.text_factory = lambda value: self.fail("raw fields transferred before cap rejection")
                    try:
                        with self.assertRaisesRegex(port.ReadPortLimitError, reason):
                            getattr(self.view(**caps), name)(*args)
                    finally:
                        self.db.text_factory = str

    def test_raw_page_aggregate_and_lookahead_before_body_transfer(self):
        size = self.db.execute("SELECT length(CAST(id AS BLOB))+length(CAST(status AS BLOB))+length(CAST(body AS BLOB)) FROM runs WHERE id='r1'").fetchone()[0]
        self.db.text_factory = lambda value: self.fail("page transferred body before complete size preflight")
        try:
            with self.assertRaisesRegex(port.ReadPortLimitError, "max_result_bytes"):
                self.view(max_result_bytes=size).run_records(limit=1)
        finally:
            self.db.text_factory = str
        page = self.view().run_records(limit=1)
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_after, "r1")

    def test_raw_large_invalid_utf8_text_rejects_before_transfer(self):
        self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        huge = "\u4e2d" * 100000 + "{ invalid JSON"
        self.db.execute("UPDATE runs SET status=?,body=? WHERE id='r2'", ("\u00e9 UNKNOWN", huge))
        self.db.execute("DROP TRIGGER contract_no_update")
        self.db.execute("UPDATE contract SET body=? WHERE id=1", (huge,))
        self.db.text_factory = lambda value: self.fail("oversized raw value transferred")
        try:
            for operation in (lambda: self.view().run_record("r2"), lambda: self.view().run_records(limit=1), lambda: self.view().genesis_contract_record()):
                with self.assertRaisesRegex(port.ReadPortLimitError, "max_row_bytes"):
                    operation()
        finally:
            self.db.text_factory = str
        self.assertTrue(self.db.in_transaction)

    def test_raw_missing_tables_preserve_sqlite_errors(self):
        self.db.rollback()
        self.db.execute("DROP TABLE runs")
        self.db.execute("DROP TABLE contract")
        self.db.execute("BEGIN")
        view = self.view()
        for operation, table in ((lambda: view.run_record("r1"), "runs"), (lambda: view.run_records(limit=1), "runs"), (view.genesis_contract_record, "contract")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "no such table: " + table):
                operation()
        self.assertTrue(self.db.in_transaction)

    def test_raw_query_argument_and_view_lifetime_negatives(self):
        view = self.view(max_rows=2)
        statements = []
        self.db.set_trace_callback(statements.append)
        for limit in (0, -1, True, 3, None):
            with self.assertRaises(port.ReadPortLimitError):
                view.run_records(limit=limit)
        with self.assertRaises(TypeError):
            view.run_records(limit=1, after=7)
        with self.assertRaises(TypeError):
            view.run_record(7)
        self.assertEqual(statements, [])
        view.close()
        for operation in (view.genesis_contract_record, lambda: view.run_record("r1"), lambda: view.run_records(limit=1)):
            with self.assertRaisesRegex(ValueError, "closed"):
                operation()
        self.assertTrue(self.db.in_transaction)

    def test_original_budget_rows_reservations_and_unknown_receipt_values(self):
        view = self.view()
        expected = [tuple(row) for row in self.db.execute("SELECT * FROM budget ORDER BY resource")]
        self.assertEqual([(r.resource, r.cap, r.spent, r.charged, r.reserved) for r in view.budget_rows()], expected)
        self.assertGreater(sum(r.reserved for r in view.budget_rows()), 0)
        raw = self.db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id='r1'").fetchone()
        original = project.ProjectStore._receipt(raw)
        self.assertEqual(thaw(view.receipt("r1")), original)
        self.assertEqual(original["assessment"]["task_gain"], "UNKNOWN")
        self.assertEqual(original["sha256"], self.retained["sha256"])
        self.assertEqual(original["resources"]["cpu_seconds"]["unknown"], True)
        # Original protocol/file bindings stay in the retained receipt, never recomputed by the port.
        self.assertEqual(thaw(view.receipt("r1"))["protocol"], original["protocol"])

    def test_results_are_deeply_immutable(self):
        view = self.view()
        budget = view.budget_rows()
        with self.assertRaises(FrozenInstanceError):
            budget[0].spent = 100
        page = view.receipt_summaries(limit=1)
        with self.assertRaises(FrozenInstanceError):
            page.has_more = True
        with self.assertRaises(FrozenInstanceError):
            page.rows[0].run_id = "another"
        receipt = view.receipt("r1")
        with self.assertRaises(TypeError):
            receipt["run_id"] = "another"
        with self.assertRaises(TypeError):
            receipt["assessment"]["task_gain"] = "PASS"
        self.assertIsInstance(receipt["errors"], tuple)
        view.close()
        self.assertEqual(receipt["sha256"], self.retained["sha256"])

    def test_pagination_uses_original_ids_and_explicit_continuation(self):
        self.db.rollback()
        self.store.execute("r2")
        self.fx.run_spec(self.fx.spec("r3"))
        self.db.execute("BEGIN")
        view = self.view(max_rows=2)
        first = view.receipt_summaries(limit=2)
        self.assertTrue(first.has_more)
        self.assertEqual(first.next_after, "r2")
        self.assertEqual([(r.run_id, r.sha256) for r in first.rows],
                         [tuple(r) for r in self.db.execute("SELECT run_id,sha256 FROM receipts ORDER BY run_id LIMIT 2")])
        second = view.receipt_summaries(limit=2, after=first.next_after)
        self.assertEqual([r.run_id for r in second.rows], ["r3"])
        self.assertFalse(second.has_more)
        self.assertIsNone(second.next_after)
        self.assertEqual(view.receipt_summaries(limit=1, after="r9").rows, ())
        self.assertIsNone(view.receipt("missing"))
        # Run identifiers are bound parameters, including hostile-looking strings.
        self.assertIsNone(view.receipt("r1' OR 1=1 --"))
        self.assertEqual(view.receipt_summaries(limit=1, after="r1' OR 1=1 --").rows[0].run_id, "r2")

    def test_summaries_never_read_or_decode_full_bodies(self):
        view = self.view()
        reads = []
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                reads.append((table, column))
                if table == "receipts" and column == "body":
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.db.set_authorizer(authorize)
        with patch.object(port, "decode_receipt", side_effect=AssertionError("no body decode")):
            self.assertEqual(view.receipt_summaries(limit=1).rows[0].sha256, self.retained["sha256"])
        self.assertNotIn(("receipts", "body"), reads)
        self.assertNotIn(("events", "body"), reads)

    def test_wal_snapshot_consistency_across_committed_writer_connection(self):
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        view = self.view()
        before = view.budget_rows()  # Pins the original reader transaction's snapshot.
        receipt_before = view.receipt_summaries(limit=3)
        run_before = tuple(self.db.execute("SELECT id,status,body FROM runs WHERE id='r2'").fetchone())
        # The API opens distinct writer connections while this read transaction remains open.
        self.store.execute("r2")
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(view.budget_rows(), before)
        self.assertEqual(view.receipt_summaries(limit=3), receipt_before)
        self.assertIsNone(view.receipt("r2"))
        self.assertEqual(tuple(self.db.execute("SELECT id,status,body FROM runs WHERE id='r2'").fetchone()), run_before)
        with closing(sqlite3.connect(self.store.path)) as writer_view:
            self.assertEqual(writer_view.execute("SELECT count(*) FROM receipts").fetchone()[0], 2)
            self.assertEqual(writer_view.execute("SELECT status FROM runs WHERE id='r2'").fetchone()[0], "COMPLETED")
        view.close()
        self.db.rollback()
        self.db.execute("BEGIN")
        fresh = self.view()
        self.assertIsNotNone(fresh.receipt("r2"))
        self.assertNotEqual(fresh.budget_rows(), before)

    def test_deliberate_timeout_wal_preserves_failed_receipt_cost_and_no_duplicate(self):
        self.db.rollback()
        spec = self.fx.spec("r3", mode="timeout", timeout=0.2)
        registered = self.store.register(spec)
        self.db.execute("BEGIN")
        view = self.view()
        budget_before = {r.resource: r for r in view.budget_rows()}
        run_before = view.run_record("r3")
        page_before = view.receipt_summaries(limit=3)
        rows_before = ledger_rows(self.db)

        receipt = self.store.execute("r3")
        self.assertEqual(receipt["run_status"], "FAILED", receipt)
        self.assertTrue(receipt["timeout"], receipt)
        self.assertTrue(receipt["process_started"], receipt)
        self.assertIn("Process timeout", receipt["errors"])
        self.assertEqual(receipt["run_id"], "r3")
        self.assertEqual(receipt["assessment"]["task_gain"], "UNKNOWN")
        self.assertEqual(view.run_record("r3"), run_before)
        self.assertEqual(view.receipt_summaries(limit=3), page_before)
        self.assertIsNone(view.receipt("r3"))
        self.assertEqual({r.resource: r for r in view.budget_rows()}, budget_before)
        self.assertEqual(ledger_rows(self.db), rows_before)
        self.assertTrue(self.db.in_transaction)

        view.close()
        self.db.rollback()
        self.db.execute("BEGIN")
        fresh = self.view()
        self.assertEqual(fresh.run_record("r3").status, "FAILED")
        self.assertEqual(thaw(fresh.receipt("r3")), receipt)
        budget_after = {r.resource: r for r in fresh.budget_rows()}
        self.assertEqual(budget_after["wall_seconds"].cap, 20)
        for resource, before in budget_before.items():
            self.assertEqual(budget_after[resource].cap, before.cap)
            self.assertAlmostEqual(budget_after[resource].reserved,
                                   before.reserved - registered["resource_estimates"][resource])
        self.assertGreater(budget_after["wall_seconds"].spent, budget_before["wall_seconds"].spent)
        self.assertGreater(budget_after["cpu_seconds"].charged, budget_before["cpu_seconds"].charged)
        self.assertEqual(budget_after["cpu_seconds"].reserved, 1)
        rows_settled = ledger_rows(self.db)
        self.assertEqual(self.store.recover("r3"), receipt)
        with self.assertRaisesRegex(ValueError, "already dispatched"):
            self.store.execute("r3")
        with closing(sqlite3.connect(self.store.path)) as observer:
            self.assertEqual(ledger_rows(observer), rows_settled)
            raw = observer.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id='r3'").fetchone()
            self.assertEqual(project.ProjectStore._receipt(dict(zip(("run_id", "sha256", "body"), raw))), receipt)
            self.assertEqual(observer.execute("SELECT count(*) FROM runs WHERE id='r3'").fetchone()[0], 1)
            self.assertEqual(observer.execute("SELECT count(*) FROM receipts WHERE run_id='r3'").fetchone()[0], 1)

    def test_only_select_no_events_cost_or_connection_settings_change(self):
        before = ledger_rows(self.db)
        changes = self.db.total_changes
        row_factory, isolation = self.db.row_factory, self.db.isolation_level
        statements, denied = [], []
        self.db.set_trace_callback(statements.append)
        def authorize(action, *args):
            if action not in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
                denied.append((action, args))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.db.set_authorizer(authorize)
        view = self.view()
        with patch.object(port.sqlite3, "connect", side_effect=AssertionError("must borrow original connection")):
            view.budget_rows()
            view.receipt_summaries(limit=2)
            view.receipt("r1")
            view.close()
        self.assertTrue(statements)
        self.assertTrue(all(s.lstrip().startswith("SELECT ") for s in statements), statements)
        self.assertFalse(denied)
        self.db.set_authorizer(None)
        self.db.set_trace_callback(None)
        self.assertEqual(ledger_rows(self.db), before)
        self.assertEqual(self.db.total_changes, changes)
        self.assertIs(self.db.row_factory, row_factory)
        self.assertEqual(self.db.isolation_level, isolation)
        self.assertTrue(self.db.in_transaction)
        self.assertFalse(any(isinstance(getattr(view, name), sqlite3.Connection)
                             for name in dir(view) if not name.startswith("_")))

    def test_close_inside_writer_transaction_leaves_original_write_pending(self):
        self.db.rollback()
        old = self.db.execute("SELECT reserved FROM budget WHERE resource='cpu_seconds'").fetchone()[0]
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE budget SET reserved=reserved+0.5 WHERE resource='cpu_seconds'")
        view = self.view()
        self.assertEqual(next(r.reserved for r in view.budget_rows() if r.resource == "cpu_seconds"), old + 0.5)
        view.close()
        view.close()
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute("SELECT reserved FROM budget WHERE resource='cpu_seconds'").fetchone()[0], old + 0.5)
        with closing(sqlite3.connect(self.store.path)) as observer:
            self.assertEqual(observer.execute("SELECT reserved FROM budget WHERE resource='cpu_seconds'").fetchone()[0], old)
        self.db.rollback()  # Only the caller owns settlement.
        self.assertEqual(self.db.execute("SELECT reserved FROM budget WHERE resource='cpu_seconds'").fetchone()[0], old)
        with self.assertRaisesRegex(ValueError, "closed"):
            view.budget_rows()

    def test_constructor_and_ended_transaction_lifetime(self):
        with self.assertRaises(TypeError):
            port.ProjectReadPort(object(), integrity_error=ValueError, digest=project.digest)
        self.db.rollback()
        with self.assertRaisesRegex(ValueError, "active caller transaction"):
            self.view()
        self.db.execute("BEGIN")
        view = self.view()
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "caller transaction has ended"):
            view.budget_rows()
        self.db.execute("BEGIN")
        with self.assertRaisesRegex(ValueError, "closed"):
            view.receipt("r1")

    def test_invalid_caps_and_page_requests_reject_without_querying(self):
        statements = []
        self.db.set_trace_callback(statements.append)
        for name in ("max_rows", "max_row_bytes", "max_result_bytes"):
            for value in (0, -1, True, 1.5, "1", None):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    self.view(**{name: value})
        view = self.view(max_rows=2)
        for value in (0, -1, True, 1.5, 3, None):
            with self.subTest(limit=value), self.assertRaises(port.ReadPortLimitError):
                view.receipt_summaries(limit=value)
        with self.assertRaises(TypeError):
            view.receipt_summaries(limit=1, after=5)
        with self.assertRaises(TypeError):
            view.receipt(5)
        self.assertEqual(statements, [])

    def test_budget_row_count_cap_rejects_instead_of_truncating(self):
        with self.assertRaisesRegex(port.ReadPortLimitError, "max_rows exceeded"):
            self.view(max_rows=2).budget_rows()
        self.assertEqual(len(self.view(max_rows=3).budget_rows()), 3)
        self.assertTrue(self.db.in_transaction)

    def test_byte_caps_and_exact_boundary(self):
        row = self.db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id='r1'").fetchone()
        size = sum(len(v.encode("utf-8")) for v in row)
        self.assertEqual(thaw(self.view(max_row_bytes=size, max_result_bytes=size).receipt("r1")), self.retained)
        with self.assertRaisesRegex(port.ReadPortLimitError, "max_row_bytes exceeded"):
            self.view(max_row_bytes=size - 1).receipt("r1")
        with self.assertRaisesRegex(port.ReadPortLimitError, "max_result_bytes exceeded"):
            self.view(max_result_bytes=size - 1).receipt("r1")
        with self.assertRaisesRegex(port.ReadPortLimitError, "max_result_bytes exceeded"):
            self.view(max_result_bytes=1).budget_rows()
        with self.assertRaisesRegex(port.ReadPortLimitError, "max_row_bytes exceeded"):
            self.view(max_row_bytes=1).receipt_summaries(limit=1)

    def test_page_lookahead_counts_toward_aggregate_cap(self):
        self.db.rollback()
        self.store.execute("r2")
        self.db.execute("BEGIN")
        size = self.db.execute("SELECT length(CAST(run_id AS BLOB))+length(CAST(sha256 AS BLOB)) FROM receipts WHERE run_id='r1'").fetchone()[0]
        with self.assertRaisesRegex(port.ReadPortLimitError, "max_result_bytes exceeded"):
            self.view(max_result_bytes=size).receipt_summaries(limit=1)
        self.assertTrue(self.view(max_result_bytes=size * 2).receipt_summaries(limit=1).has_more)

    def test_original_integrity_error_bytes_and_injected_digest(self):
        self.db.rollback()
        original_body = project.canonical(self.retained)
        with self.store._db() as db:
            db.execute("DROP TRIGGER receipts_no_update")
        bodies = ["{", "[]", '{}', '{"run_id":"r1","run_id":"r1"}',
                  original_body[:-1] + ',"extra":NaN}', original_body[:-1] + ',"extra":"\\ud800"}',
                  project.canonical({**self.retained, "run_id": "another"}),
                  project.canonical({**self.retained, "sha256": "0" * 64})]
        for body in bodies:
            with self.store._db() as writer:
                writer.execute("UPDATE receipts SET body=? WHERE run_id='r1'", (body,))
            self.db.execute("BEGIN")
            raw = self.db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id='r1'").fetchone()
            with self.assertRaises(project.ReceiptIntegrityError) as old:
                project.ProjectStore._receipt(raw)
            with self.assertRaises(project.ReceiptIntegrityError) as new:
                self.view().receipt("r1")
            self.assertEqual(signature(new.exception), signature(old.exception))
            self.db.rollback()
        with self.store._db() as writer:
            writer.execute("UPDATE receipts SET body=? WHERE run_id='r1'", (original_body,))
        self.db.execute("BEGIN")
        sentinel = TypeError("original digest seam")
        def fail(value):
            raise sentinel
        view = port.ProjectReadPort(self.db, integrity_error=project.ReceiptIntegrityError, digest=fail)
        with self.assertRaises(TypeError) as caught:
            view.receipt("r1")
        self.assertIs(caught.exception, sentinel)

    def test_no_upward_imports_in_isolated_module_namespace(self):
        source = Path(port.__file__).read_text(encoding="utf-8")
        allowed = {"dataclasses", "sqlite3", "types", "rds_project_records"}
        nodes = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.Import, ast.ImportFrom))]
        actual = {n.module if isinstance(n, ast.ImportFrom) else n.names[0].name for n in nodes}
        self.assertEqual(actual, allowed)
        seen = []
        def guarded_import(name, *args, **kwargs):
            self.assertIn(name, allowed)
            seen.append(name)
            return builtins.__import__(name, *args, **kwargs)
        isolated = {"__name__": "_read_port_isolation", "__builtins__": {**vars(builtins), "__import__": guarded_import}}
        # dataclasses resolves its own defining module; provide that isolated module temporarily.
        import types
        module = types.ModuleType(isolated["__name__"])
        module.__dict__.update(isolated)
        with patch.dict(sys.modules, {module.__name__: module}):
            exec(compile(source, "<isolated-read-port>", "exec"), module.__dict__)
            view = module.ProjectReadPort(self.db, integrity_error=project.ReceiptIntegrityError, digest=project.digest)
            self.assertEqual(thaw(view.receipt("r1")), self.retained)
        self.assertEqual(set(seen), allowed)


if __name__ == "__main__":
    unittest.main()
