"""Real wheel/kernel regression coverage for canonical workspace admission."""
import io
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import rds_wheel
from rds_project import ProjectStore
from rds_workspace import bind, pointer_path
from test_rds_wheel import fixture


class WheelWorkspaceAdmissionTests(unittest.TestCase):
    def prepare(self, *, bound=True, ready=False):
        temporary = tempfile.TemporaryDirectory(prefix="rds-wheel-workspace-")
        self.addCleanup(temporary.cleanup)
        wheel = fixture.prepare(Path(temporary.name) / "project", factors=["flat"])
        fixture.bootstrap(wheel)
        if bound:
            bind(wheel.root)
        if ready:
            self.assertEqual(wheel.tick(), 0)
            self.assertEqual(wheel.tick(), 0)
        return wheel

    def files(self, wheel):
        return {str(p.relative_to(wheel.root)): rds_wheel.sha(p)
                for p in wheel.root.rglob("*") if p.is_file()
                and p.name not in {"project.sqlite3-shm", "project.sqlite3-wal"}}

    def ledger_rows(self, wheel):
        store = ProjectStore(wheel.root)
        with store._db(True) as db:
            return tuple(db.iterdump())

    def damage(self, wheel, kind):
        store = ProjectStore(wheel.root)
        if kind == "ledger_identity":
            # Bypass supported writers only to inject damage into the real ledger.
            with closing(sqlite3.connect(store.path)) as db, db:
                for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='events'").fetchall():
                    db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
                db.execute("DELETE FROM events WHERE json_extract(body,'$.kind')='WORKSPACE_BOUND'")
        elif kind == "rollback":
            retained = store.path.read_bytes()
            store.register(wheel.manifest("screen_change", "screen", 1000))
            store.path.write_bytes(retained)
        elif kind == "invalid_ledger":
            store.path.write_bytes(b"damaged SQLite ledger")
        else:
            pointer = pointer_path(wheel.root)
            if kind == "malformed_pointer":
                pointer.write_text("{", encoding="utf-8")
            else:
                value = rds_wheel.read_json(pointer)
                value["event_digest"] = "0" * 64
                pointer.write_text(rds_wheel.canonical(value), encoding="utf-8")

    def invoke(self, wheel, action):
        args = ["--root", str(wheel.root)]
        if action == "initialize":
            args += ["--initialize", str(wheel.root / "project-contract.json")]
        elif action == "prompt":
            args += ["--prompt"]
        elif action == "propose":
            args += ["--propose", "researcher-a"]
        with patch("sys.stdin", io.StringIO("screen_change\n")), \
                patch("sys.stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")), \
                patch("sys.stderr", io.StringIO()):
            return rds_wheel.main(args)

    def assert_refused(self, action, *, journal=False):
        for kind in ("ledger_identity", "rollback", "pointer_identity",
                     "malformed_pointer", "invalid_ledger"):
            with self.subTest(action=action, damage=kind, journal=journal):
                wheel = self.prepare(ready=action in {"prompt", "propose"})
                if journal:
                    state = rds_wheel.read_json(wheel.state_path("state.json"))
                    retained = wheel.state_path("dead.txt").read_text(encoding="utf-8")
                    state["effects"] = [wheel.effect("dead.txt", retained + "interrupted\n", append=True)]
                    wheel.write("state.json", state)
                self.damage(wheel, kind)
                before = self.files(wheel)
                rows = self.ledger_rows(wheel) if kind != "invalid_ledger" else None
                if kind not in {"malformed_pointer", "invalid_ledger"}:
                    status = ProjectStore(wheel.root).snapshot()
                    self.assertEqual(status["workspace_binding"]["status"], "MISMATCH")
                    self.assertEqual(status["contract_sha256"],
                                     rds_wheel.digest(status["contract"]))
                    self.assertEqual(self.files(wheel), before, "status must remain read-only")
                outcome = self.invoke(wheel, action)
                self.assertEqual(self.files(wheel), before,
                                 "refusal must preserve journal, inbox, state, pointer, ledger and attempts")
                if rows is not None:
                    self.assertEqual(self.ledger_rows(wheel), rows)
                self.assertEqual(outcome, 2)

    def test_tick_refuses_damage_before_dispatch(self):
        self.assert_refused("tick")

    def test_tick_refuses_damage_before_journal_recovery(self):
        self.assert_refused("tick", journal=True)

    def test_prompt_refuses_damage_before_journal_recovery(self):
        self.assert_refused("prompt", journal=True)

    def test_propose_refuses_damage_before_inbox_or_journal_write(self):
        self.assert_refused("propose", journal=True)

    def test_initialize_refuses_damage_before_any_write(self):
        self.assert_refused("initialize")

    def test_bound_and_unbound_continuations(self):
        for bound in (True, False):
            with self.subTest(bound=bound):
                wheel = self.prepare(bound=bound, ready=True)
                status = wheel.project("status")
                self.assertEqual(len(status["receipts"]), 3)
                self.assertTrue(all(run["attempt_id"] for run in status["runs"]))
                before = self.files(wheel)
                self.assertEqual(self.invoke(wheel, "prompt"), 0)
                self.assertEqual(self.files(wheel), before)
                self.assertEqual(self.invoke(wheel, "propose"), 0)
                self.assertEqual(len(wheel.inbox_contents()[1]), 1)
                self.assertEqual(self.invoke(wheel, "tick"), 0)
                after = wheel.project("status")
                self.assertEqual(len(after["receipts"]), 4)
                self.assertEqual(after["workspace_binding"]["status"] if bound else "UNBOUND",
                                 "BOUND" if bound else "UNBOUND")


if __name__ == "__main__":
    unittest.main()
