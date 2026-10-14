"""Fixture receipts exercise the native decoder; no model requests or reruns."""
import copy
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from rds_project import ProjectStore, ReceiptIntegrityError, canonical, digest

spec = importlib.util.spec_from_file_location("observations", Path(__file__).with_name("observations.py"))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rds observations ")
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(self.tmp.name)
        self.store.state_dir.mkdir()
        self.db = sqlite3.connect(self.store.path)
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE receipts(run_id TEXT PRIMARY KEY,sha256 TEXT,body TEXT)")

    def retain(self, run_id, status="SUCCEEDED"):
        artifacts = []
        for kind, raw in (("stdout.bin", b"original stdout"), ("stderr.bin", b"original failure\xff"),
                          ("project_output", b'{"mean":2}')):
            if status == "FAILED" and kind == "project_output":
                continue
            path = self.store.root / (run_id + "-" + kind)
            path.write_bytes(raw)
            artifacts.append({"path": path.name, "kind": kind, "size": len(raw),
                              "sha256": adapter.hashlib.sha256(raw).hexdigest()})
        receipt = {"run_id": run_id, "attempt_id": run_id + "-attempt", "run_status": status,
                   "cwd": str(self.store.root), "process_started": True, "artifacts": artifacts,
                   "errors": ["original failure"] if status == "FAILED" else [],
                   "resources": {"cpu_seconds": {"measured": None, "unknown": True, "charged_estimate": 1}}}
        self.write(receipt)
        return {"run_id": run_id, "receipt_sha256": receipt["sha256"]}

    def write(self, receipt):
        receipt["sha256"] = digest({k: v for k, v in receipt.items() if k != "sha256"})
        self.db.execute("INSERT OR REPLACE INTO receipts VALUES (?,?,?)",
                        (receipt["run_id"], receipt["sha256"], canonical(receipt)))
        self.db.commit()

    def receipt(self, pin):
        self.db.row_factory = sqlite3.Row
        return self.store._receipt(self.db.execute("SELECT * FROM receipts WHERE run_id=?",
                                                  (pin["run_id"],)).fetchone())

    def test_order_negative_bytes_and_unknown_cost_preserved_without_writes(self):
        pins = [self.retain("failed", "FAILED"), self.retain("ok")]
        before = self.store.path.read_bytes()
        with patch.object(self.store, "execute", side_effect=AssertionError("Must never execute")), \
             patch.object(self.store, "_db", wraps=self.store._db) as opened:
            result = adapter.read_observations(self.store, pins)
        opened.assert_called_once_with(readonly=True)
        self.assertEqual(self.store.path.read_bytes(), before)
        rows = result["observations"]
        self.assertEqual([r["receipt"]["run_status"] for r in rows], ["FAILED", "SUCCEEDED"])
        self.assertEqual(rows[0]["receipt"]["errors"], ["original failure"])
        self.assertIsNone(rows[0]["receipt"]["resources"]["cpu_seconds"]["measured"])
        self.assertEqual(rows[0]["artifacts"][1]["bytes"], b"original failure\xff")
        self.assertEqual(result["evidence_status"], "UNKNOWN")
        self.assertEqual(result["domain_projection"], "UNKNOWN")

    def test_missing_wrong_pin_duplicate_and_damaged_native_receipt_reject(self):
        pin = self.retain("ok")
        for pins in ([{**pin, "run_id": "missing"}], [{**pin, "receipt_sha256": "0" * 64}], [pin, pin]):
            with self.subTest(pins=pins), self.assertRaises(ValueError):
                adapter.read_observations(self.store, pins)
        receipt = self.receipt(pin)
        receipt["errors"] = ["changed without updating original digest"]
        self.db.execute("UPDATE receipts SET body=? WHERE run_id=?", (canonical(receipt), pin["run_id"]))
        self.db.commit()
        with self.assertRaises(ReceiptIntegrityError):
            adapter.read_observations(self.store, [pin])

    def test_missing_tampered_escaped_and_duplicate_artifacts_reject(self):
        pin = self.retain("ok")
        original = self.receipt(pin)
        path = self.store.root / original["artifacts"][0]["path"]
        path.write_bytes(b"tampered stdout")
        with self.assertRaises(ValueError):
            adapter.read_observations(self.store, [pin])
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            adapter.read_observations(self.store, [pin])
        path.write_bytes(b"original stdout")
        for relative in ("../outside.bin", original["artifacts"][1]["path"]):
            receipt = copy.deepcopy(original)
            if relative == original["artifacts"][1]["path"]:
                receipt["artifacts"][0] = copy.deepcopy(original["artifacts"][1])
            receipt["artifacts"][0]["path"] = relative
            self.write(receipt)
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "escapes root or repeats"):
                adapter.read_observations(self.store, [{**pin, "receipt_sha256": receipt["sha256"]}])

    def test_missing_started_streams_and_oversized_artifact_reject(self):
        pin = self.retain("ok")
        for kind in ("missing-stream", "oversized"):
            receipt = self.receipt(pin)
            if kind == "missing-stream":
                receipt["artifacts"] = receipt["artifacts"][1:]
            else:
                receipt["artifacts"][0]["size"] = 262145
            self.write(receipt)
            pin["receipt_sha256"] = receipt["sha256"]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                adapter.read_observations(self.store, [pin])

    def test_unstarted_negative_receipt_retained_but_wrong_root_rejected(self):
        pin = self.retain("unstarted", "FAILED")
        receipt = self.receipt(pin)
        receipt.update(process_started=False, artifacts=[])
        self.write(receipt)
        pin["receipt_sha256"] = receipt["sha256"]
        result = adapter.read_observations(self.store, [pin])
        self.assertEqual(result["observations"][0]["artifacts"], [])
        receipt["cwd"] = str(self.store.root.parent)
        self.write(receipt)
        with self.assertRaises(ValueError):
            adapter.read_observations(self.store, [{**pin, "receipt_sha256": receipt["sha256"]}])
