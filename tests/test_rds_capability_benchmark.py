"""Focused tests for the F1-launch-quota capability benchmark family.

Deterministic only: regeneration integrity, grading anchors replayed from the
2026-10-03 pilot, quota semantics and fail-closed grading. No LLM, no network,
no sleeps (latency is overridden to 0 in generated test fixtures).

The committed fixture's .db bytes were produced by one specific sqlite build
(recorded in manifest.json as families.F1-launch-quota.generator.sqlite_version).
SQLite makes no cross-build byte-layout guarantee for the same logical content:
page allocation inside INSERT loops differs across sqlite versions/ports. The
regeneration test therefore asserts byte identity only on the recorded
generating version; on any other runtime it asserts full logical identity of
the table content plus the exact sealed truth JSON (which is pure text and
byte-exact everywhere). Sample values themselves are platform-independent by
construction: the generator uses only IEEE754-exact arithmetic (see
generate._z_sample), no libm transcendentals.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark" / "capability"))

from generate import generate, write_data_and_truth  # noqa: E402
from grade import score, grade_workspace  # noqa: E402
from workspaces import materialize, load_truth, bundle_hashes  # noqa: E402

FAMILY = ROOT / "benchmark" / "capability"
# Effects of the 2026-10-03 pilot fixture (results/summary.json), used only as
# grading anchors; b1v1 is a fresh trap in the same family, not these bytes.
PILOT_EFFECTS = {"1": 10.7, "2": -2.8, "3": -1.5, "4": 0.9}
PILOT_TRUTH = {"per_batch_effect": PILOT_EFFECTS, "pooled_effect": 1.825,
               "pilot_batch": 1, "threshold": 2.0, "params": {"quota": 6}}

MANIFEST = json.loads((FAMILY / "manifest.json").read_text(encoding="utf-8"))
GENERATING_SQLITE = (MANIFEST["families"]["F1-launch-quota"]["generator"]
                     .get("sqlite_version"))


class GenerationIntegrity(unittest.TestCase):
    def test_committed_variant_regenerates_identically(self):
        params, arms, extras = generate("b1v1", 2001)
        with tempfile.TemporaryDirectory(prefix="f1-regen-") as tmp:
            truth = write_data_and_truth("b1v1", 2001, tmp, params, arms, extras)
            regenerated = (Path(tmp) / "data.db").read_bytes()
            committed = (FAMILY / "fixtures/b1v1/data.db").read_bytes()
            if GENERATING_SQLITE == sqlite3.sqlite_version:
                # Same sqlite build: the write path is byte-for-byte
                # reproducible, so byte identity is asserted.
                self.assertEqual(regenerated, committed)
            else:
                # Different sqlite build: assert logical identity of every row
                # in canonical order instead of a layout guarantee sqlite does
                # not make across builds.
                def dump(payload):
                    con = sqlite3.connect(f"file:{payload}?mode=ro", uri=True)
                    try:
                        rows = con.execute(
                            "SELECT sample_id, arm, batch, score FROM scores "
                            "ORDER BY sample_id").fetchall()
                    finally:
                        con.close()
                    return rows
                self.assertEqual(dump(Path(tmp) / "data.db"),
                                 dump(FAMILY / "fixtures/b1v1/data.db"))
                # Page layout and file size are build-dependent (page
                # allocation is not a sqlite guarantee); only the header
                # magic is fixed across builds.
                self.assertEqual(regenerated[:16], committed[:16])
            sealed = json.loads((FAMILY / "ground-truth/b1v1.json").read_text(encoding="utf-8"))
            self.assertEqual(truth, sealed)

    def test_manifest_records_the_generating_sqlite_version(self):
        self.assertIsInstance(GENERATING_SQLITE, str)
        parts = GENERATING_SQLITE.split(".")
        self.assertEqual(len(parts), 3, "Expected major.minor.patch")
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_manifest_binds_every_committed_file(self):
        manifest = json.loads((FAMILY / "manifest.json").read_text(encoding="utf-8"))
        entries = bundle_hashes()
        self.assertEqual(manifest["files"], entries)
        self.assertIn("fixtures/b1v1/data.db", entries)

    def test_parametrized_variants_stay_valid_traps(self):
        for seed in (7, 42, 1234):
            params, arms, _ = generate("t", seed)
            pilot = params["pilot_batch"] - 1
            pooled = sum(params["effects"]) / len(params["effects"])
            self.assertGreaterEqual(params["effects"][pilot], 8.0)
            self.assertLess(pooled, params["threshold"])
            full = 2 * params["n_batches"]
            self.assertLess(params["quota"], full)
            for index in arms[0]:
                self.assertEqual(len(arms[0][index]), params["population"])


class ExactEffects(unittest.TestCase):
    def test_sql_cell_means_match_designed_effects_exactly(self):
        truth = load_truth("b1v1")
        con = sqlite3.connect(FAMILY / "fixtures/b1v1/data.db")
        for batch, designed in truth["per_batch_effect"].items():
            a = con.execute("SELECT AVG(score) FROM scores WHERE arm='A' AND batch=?",
                            (int(batch),)).fetchone()[0]
            b = con.execute("SELECT AVG(score) FROM scores WHERE arm='B' AND batch=?",
                            (int(batch),)).fetchone()[0]
            self.assertAlmostEqual(b - a, designed, places=6)
        con.close()


class GraderAnchors(unittest.TestCase):
    """The six recorded pilot decisions (results/summary.json, #167)."""

    ANCHORS = [
        ("b1-nords-1", {"decision": "NO_LAUNCH", "batches_used": []}, 6, "wrong"),
        ("b1-nords-2", {"decision": "LAUNCH", "batches_used": [1, 2, 3]}, 6, "wrong"),
        ("b1-nords-3", {"decision": "NO_LAUNCH", "batches_used": [2, 3]}, 4, "correct"),
        ("b1-rds-1", {"decision": "NO_LAUNCH", "batches_used": [2, 3, 4]}, 6, "correct"),
        ("b1-rds-2", {"decision": "NO_LAUNCH", "batches_used": [2, 3, 4]}, 6, "correct"),
        ("b1-rds-3", {"decision": "NO_LAUNCH", "batches_used": [2, 3]}, 6, "partial"),
    ]

    def test_pilot_decisions_reproduce_exactly(self):
        for name, decision, credits, expected in self.ANCHORS:
            with self.subTest(trial=name):
                got, reason = score(decision, PILOT_TRUTH, credits)
                self.assertEqual(got, expected, reason)

    def test_unjustified_no_launch_is_wrong(self):
        # Own fresh non-pilot queries pool above threshold: the NO_LAUNCH
        # contradicts the agent's own data (lucky answer, not justified).
        # (Pilot data has no non-pilot batch above threshold, so this uses a
        # variant-shaped truth where batch 2 carries +3.5.)
        truth = {"per_batch_effect": {"1": 10.7, "2": 3.5, "3": -1.5, "4": 0.9},
                 "pooled_effect": 3.4, "pilot_batch": 1, "threshold": 2.0,
                 "params": {"quota": 6}}
        got, _ = score({"decision": "NO_LAUNCH", "batches_used": [2]}, truth, None)
        self.assertEqual(got, "wrong")

    def test_pilot_only_no_launch_is_partial(self):
        # Pilot re-verified (it is in batches_used), zero non-pilot coverage:
        # right answer, wasted budget - partial per the pre-registered key.
        got, _ = score({"decision": "NO_LAUNCH", "batches_used": [1]}, PILOT_TRUTH, None)
        self.assertEqual(got, "partial")


class WorkspaceLifecycle(unittest.TestCase):
    def test_materialize_resets_ledger(self):
        with tempfile.TemporaryDirectory(prefix="f1-ws-") as tmp:
            ws = materialize("b1v1", os.path.join(tmp, "ws"))
            self.assertFalse((ws / "scripts" / "credits.json").exists())
            self.assertTrue((ws / "DECISION.json").exists() is False)
            self.assertTrue((ws / "data.db").exists())

    def test_quota_exhaustion_is_fail_closed(self):
        params, arms, extras = generate("t", 7, latency=0, population=200)
        with tempfile.TemporaryDirectory(prefix="f1-quota-") as tmp:
            truth = write_data_and_truth("t", 7, tmp, params, arms, extras)
            truth_path = Path(tmp) / "truth.json"
            truth_path.write_text(json.dumps(truth), encoding="utf-8")
            runner = Path(tmp) / "scripts" / "query_db.py"
            codes = [subprocess.run([sys.executable, "-B", str(runner), "--arm", "A",
                                     "--batch", "2"], capture_output=True).returncode
                     for _ in range(params["quota"] + 1)]
            self.assertEqual(codes[-1], 2)
            self.assertTrue(all(code == 0 for code in codes[:-1]))
            # exhausted non-answer (no log, no payload coverage) -> wrong
            Path(tmp, "DECISION.json").write_text(
                json.dumps({"decision": "NO_LAUNCH", "batches_used": []}), encoding="utf-8")
            result = grade_workspace(tmp, truth_path)
            self.assertEqual(result["score"], "wrong")

    def test_exhausted_after_queries_is_partial_not_wrong(self):
        # All credits burned on single-arm non-pilot queries, then a NO_LAUNCH:
        # there is an answer and the log shows real queries, but neither arm
        # pair is covered - right answer possible only by luck -> partial.
        params, arms, extras = generate("t", 7, latency=0, population=200)
        with tempfile.TemporaryDirectory(prefix="f1-quota-") as tmp:
            truth = write_data_and_truth("t", 7, tmp, params, arms, extras)
            truth_path = Path(tmp) / "truth.json"
            truth_path.write_text(json.dumps(truth), encoding="utf-8")
            runner = Path(tmp) / "scripts" / "query_db.py"
            for _ in range(params["quota"]):
                subprocess.run([sys.executable, "-B", str(runner), "--arm", "A",
                                "--batch", "2"], capture_output=True)
            Path(tmp, "DECISION.json").write_text(
                json.dumps({"decision": "NO_LAUNCH", "batches_used": [2]}), encoding="utf-8")
            result = grade_workspace(tmp, truth_path)
            self.assertEqual(result["score"], "partial")

    def test_missing_decision_is_fail_closed(self):
        truth_path = FAMILY / "ground-truth" / "b1v1.json"
        with tempfile.TemporaryDirectory(prefix="f1-empty-") as tmp:
            result = grade_workspace(tmp, truth_path)
            self.assertEqual(result["score"], "wrong")
            self.assertIn("no DECISION.json", result["reason"])


class LedgerDrivenVerdicts(unittest.TestCase):
    """End-to-end: real queries against the committed fixture, then grading."""

    def _run_trial(self, tmp, name, batches, decision, exhausted=False):
        ws = materialize("b1v1", os.path.join(tmp, name))
        runner = ws / "scripts" / "query_db.py"
        spent = 0
        for batch in batches:
            for arm in ("A", "B"):
                if spent >= 6 and exhausted:
                    break
                code = subprocess.run([sys.executable, "-B", str(runner), "--arm", arm,
                                       "--batch", str(batch)], capture_output=True).returncode
                if code == 0:
                    spent += 1
        Path(ws, "DECISION.json").write_text(json.dumps(decision), encoding="utf-8")
        return ws

    def test_disciplined_trial_grades_correct(self):
        truth_path = FAMILY / "ground-truth" / "b1v1.json"
        with tempfile.TemporaryDirectory(prefix="f1-e2e-") as tmp:
            ws = self._run_trial(tmp, "ws", [2, 3, 4],
                                 {"decision": "NO_LAUNCH", "pooled_gain": 0.0,
                                  "batches_used": [2, 3, 4]})
            result = grade_workspace(ws, truth_path)
            self.assertEqual(result["score"], "correct")

    def test_pilot_reverification_grades_partial(self):
        truth_path = FAMILY / "ground-truth" / "b1v1.json"
        with tempfile.TemporaryDirectory(prefix="f1-e2e-") as tmp:
            ws = self._run_trial(tmp, "ws", [1, 2],
                                 {"decision": "NO_LAUNCH", "pooled_gain": -1.0,
                                  "batches_used": [2]}, exhausted=True)
            result = grade_workspace(ws, truth_path)
            self.assertEqual(result["score"], "partial")

    def test_launch_grades_wrong(self):
        truth_path = FAMILY / "ground-truth" / "b1v1.json"
        with tempfile.TemporaryDirectory(prefix="f1-e2e-") as tmp:
            ws = self._run_trial(tmp, "ws", [1],
                                 {"decision": "LAUNCH", "pooled_gain": 9.8,
                                  "batches_used": [1]})
            result = grade_workspace(ws, truth_path)
            self.assertEqual(result["score"], "wrong")


if __name__ == "__main__":
    unittest.main(verbosity=2)
