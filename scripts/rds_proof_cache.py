"""Rebuildable WAL cache; every returned proof is checked outside SQLite."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3

import rds_verify as engine
from rds_mutation import mutation
from rds_verify_types import MAX_CERTIFICATE_BYTES, canonical, digest

BUSY_TIMEOUT_SECONDS = 0.05


class ProofCache:
    def __init__(self, path):
        self.path = Path(path)
        self.workspace_root = None
        self.ready = False
        try:
            if (os.path.normcase(self.path.name) == "proofs.sqlite3"
                    and os.path.normcase(self.path.parent.name) == ".rds"):
                self.workspace_root = self.path.parent.parent.resolve()
            if self.path.is_file():
                db = self._connect(readonly=True)
                try:
                    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proofs'").fetchone()
                    self.ready = mode.lower() == "wal" and table is not None
                finally:
                    db.close()
                if self.ready:
                    return
            with self._write_admission():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = self._connect()
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("CREATE TABLE IF NOT EXISTS proofs (cache_key TEXT PRIMARY KEY, certificate BLOB NOT NULL)")
                    db.commit()
                finally:
                    db.close()
            self.ready = True
        except (OSError, sqlite3.Error, ValueError):
            # Cache availability cannot decide a mathematical claim.
            pass

    @contextmanager
    def _write_admission(self):
        if self.workspace_root is None:
            yield
            return
        from rds_workspace import check_admission
        # Only metadata writes own the gate; admission is fresh even for an
        # instance opened before an ancestor was bound. Keep it through close.
        with mutation(timeout=BUSY_TIMEOUT_SECONDS):
            check_admission(self.workspace_root)
            yield

    def _connect(self, readonly=False):
        target = self.path.resolve().as_uri() + "?mode=ro" if readonly else self.path
        db = sqlite3.connect(target, uri=readonly, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        try:
            if not readonly:
                db.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            db.close()
            raise
        return db

    def _read(self, key):
        if not self.ready:
            return None
        try:
            db = self._connect(readonly=True)
            try:
                row = db.execute(
                    "SELECT length(CAST(certificate AS BLOB)), substr(CAST(certificate AS BLOB),1,?) "
                    "FROM proofs WHERE cache_key=?", (MAX_CERTIFICATE_BYTES + 1, key)).fetchone()
            finally:
                db.close()
            if row is None or type(row[0]) is not int or not 0 < row[0] <= MAX_CERTIFICATE_BYTES:
                return None
            return json.loads(row[1].decode("utf-8"))
        except (OSError, sqlite3.Error, UnicodeError, ValueError, TypeError, RecursionError):
            return None

    def _store(self, key, certificate):
        if not self.ready:
            return False
        try:
            raw = canonical(certificate).encode("utf-8")
            if len(raw) > MAX_CERTIFICATE_BYTES:
                return False
            with self._write_admission():
                db = self._connect()
                try:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO proofs VALUES (?,?) ON CONFLICT(cache_key) "
                               "DO UPDATE SET certificate=excluded.certificate", (key, raw))
                    db.commit()
                finally:
                    db.close()
            return True
        except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError):
            return False

    @staticmethod
    def _checked(spec, certificate):
        try:
            result = engine.checked_result(spec, certificate)
            if isinstance(result, dict) and result.get("status") in {"PASS", "FAIL"}:
                return result
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            pass
        return None

    def verify(self, spec):
        try:
            engine._bounded_json(spec)
            verifier = engine.verifier_id()
            key = digest(spec) + verifier
        except (ValueError, TypeError, RecursionError, OverflowError, OSError):
            return {**engine.verify(spec), "cache": {"hit": False, "stored": False}}
        certificate = self._read(key)
        if certificate is not None:
            checked = self._checked(spec, certificate)
            if checked is not None:
                return {**checked, "cache": {"hit": True, "stored": False}}
        # _read has closed its snapshot. Neither proving nor checking owns a
        # database connection or transaction, including on a busy-cache path.
        result = engine.verify(spec)
        certificate = result.get("certificate") if isinstance(result, dict) else None
        checked = self._checked(spec, certificate) if certificate is not None else None
        stored = False
        if checked is not None:
            try:
                unchanged = engine.verifier_id() == verifier
            except OSError:
                return {**engine.verify(spec), "cache": {"hit": False, "stored": False}}
            if unchanged:
                result = checked
                stored = self._store(key, checked["certificate"])
        return {**result, "cache": {"hit": False, "stored": stored}}
