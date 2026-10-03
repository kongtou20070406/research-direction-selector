"""Synthetic JSON/JSONL aliases are one reading, while different cells remain available (#84)."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / "scripts")]
import test_rds_triple_affirmative as base
from rds_artifacts import ingest_manifest
from rds_project import ProjectStore
from rds_verify_types import digest

CLI = Path(__file__).resolve().parents[1] / "scripts" / "rds_cli.py"


class CrossFormatReadingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rds-reading-alias-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = {}
        self.write("context.json", json.dumps(base.context(base.DL_GOAL, base.DL_AFFIRM)).encode())
        self.write("graph.json", json.dumps(base.route("heldout_error")).encode())

    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        self.inputs[name] = raw
        return name

    def source(self, ident, name, raw, fmt, facts):
        self.write(name, raw)
        return {"id": ident, "path": name, "kind": "metric" if fmt == "json" else "log", "format": fmt,
                "expected_sha256": digest(raw), "binding": base.BINDING, "facts": facts}

    def manifest(self, sources, derived=None):
        self.write("manifest.json", json.dumps({"schema": "rds-artifact-manifest-v1", "sources": sources,
                                               "derived": derived or []}).encode())

    def alias(self, prefix=b"", suffix=b"", same_path=False, reverse=False, derived=False):
        raw = prefix + b'{"heldout_error":0.05,"worst_declared_cohort_error":0.09}' + suffix
        json_source = self.source("metric-view", "metric.json", raw, "json",
                                  [{"id": "heldout_error", "pointer": "/heldout_error"},
                                   {"id": "worst_declared_cohort_error", "pointer": "/worst_declared_cohort_error"}])
        line = len(prefix.decode("utf-8-sig").splitlines()) + 1
        # The prefixes below are complete blank lines; BOM alone is not a physical line.
        log_source = self.source("log-view", "metric.json" if same_path else "log.jsonl", raw, "jsonl",
                                 [{"id": "replay_reading" if derived else "clean_root_replay_error",
                                   "row": line, "pointer": "/heldout_error"}])
        sources = [json_source, log_source]
        self.manifest(list(reversed(sources)) if reverse else sources,
                      [{"id": "clean_root_replay_error", "method": "mean", "input_fact_ids": ["replay_reading"]}]
                      if derived else None)

    def cli(self, *args):
        state = self.root.resolve() / ".rds"
        before_paths = set(state.rglob("*"))
        before_bytes = {path: path.read_bytes() for path in before_paths if path.is_file()}
        result = subprocess.run([sys.executable, "-B", str(CLI), "--root", str(self.root), *args],
                                capture_output=True, encoding="utf-8", timeout=60,
                                env={**os.environ, "RDS_USAGE_DB": str(self.root / "usage.sqlite3")})
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout)
        if "--brief" in args:
            # Brief intentionally caches the full advice; no research ledger is created.
            record = Path(answer["record"])
            expected = state / "cas" / (answer["sha256"] + ".json")
            self.assertEqual(record, expected)
            raw = record.read_bytes()
            self.assertEqual(digest(raw), answer["sha256"])
            self.assertEqual(set(state.rglob("*")), before_paths | {record.parent, record})
            full = json.loads(raw)
            review = next(item["search"]["selection_review"] for item in full["recommendations"]
                          if item.get("type") == "EXECUTABLE_DIRECTION_SEARCH")
            self.assert_reuse(review)
        else:
            self.assertEqual(set(state.rglob("*")), before_paths)
        for path, raw in before_bytes.items():
            self.assertEqual(path.read_bytes(), raw)
        for name, raw in self.inputs.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        return answer

    def advise(self, *extra):
        answer = self.cli("advise", "--research-context", str(self.root / "context.json"),
                          "--graph", str(self.root / "graph.json"),
                          "--artifacts", str(self.root / "manifest.json"), *extra)
        if "--brief" in extra:
            return answer
        return next(item["search"]["selection_review"] for item in answer["recommendations"]
                    if item.get("type") == "EXECUTABLE_DIRECTION_SEARCH")

    def assert_reuse(self, review):
        self.assertEqual(review["goal"]["status"], "TRUE")
        triple = review["goal"]["triple_affirmative"]
        self.assertEqual(triple["status"], "UNKNOWN")
        self.assertEqual(triple["open"], ["FOUND", "PORTABLE"])
        self.assertEqual(triple["applicable"]["status"], "TRUE")
        for part in ("found", "portable"):
            self.assertIn("reuses the evidence", triple[part]["conditions"][0]["affirmation_reason"])
        self.assertEqual(review["next_move"]["authorization"], "UNCHANGED")

    def test_same_bytes_cross_format_alias_is_reuse_through_actual_advisor(self):
        for options in ({}, {"same_path": True}, {"prefix": b"\n\n", "suffix": b"\r\n"},
                        {"prefix": b"\xef\xbb\xbf", "reverse": True}):
            with self.subTest(options=options):
                self.alias(**options)
                self.assert_reuse(self.advise())

    def test_derived_alias_retains_the_original_read_identity(self):
        self.alias(derived=True)
        review = self.advise()
        self.assert_reuse(review)
        self.assertEqual(review["goal"]["triple_affirmative"]["portable"]["conditions"][0]["evidence_status"],
                         "PROGRAM_DERIVED")

    def test_distinct_fields_in_same_cross_format_bytes_still_affirm(self):
        raw = b'{"heldout_error":0.05,"clean_root_replay_error":0.06,"worst_declared_cohort_error":0.09}'
        self.manifest([
            self.source("metric-view", "metric.json", raw, "json",
                        [{"id": "heldout_error", "pointer": "/heldout_error"},
                         {"id": "worst_declared_cohort_error", "pointer": "/worst_declared_cohort_error"}]),
            self.source("log-view", "log.jsonl", raw, "jsonl",
                        [{"id": "clean_root_replay_error", "row": 1, "pointer": "/clean_root_replay_error"}])])
        triple = self.advise()["goal"]["triple_affirmative"]
        self.assertEqual(triple["status"], "TRUE")
        self.assertEqual(triple["open"], [])

    def test_distinct_physical_jsonl_lines_are_not_collapsed(self):
        raw = b'{"err":0.05}\n{"err":0.06}\n{"err":0.09}\n'
        facts = [{"id": fid, "row": number, "pointer": "/err"} for number, fid in enumerate(
            ("heldout_error", "clean_root_replay_error", "worst_declared_cohort_error"), 1)]
        self.manifest([self.source("log", "log.jsonl", raw, "jsonl", facts)])
        report = self.cli("artifacts", "import", "--manifest", str(self.root / "manifest.json"))
        for number, fid in enumerate(("heldout_error", "clean_root_replay_error", "worst_declared_cohort_error"), 1):
            self.assertEqual(report["facts"][fid]["source"]["locator"], f"line:{number}:pointer:/err")
        self.assertEqual(self.advise()["goal"]["triple_affirmative"]["status"], "TRUE")

    def test_public_locators_stay_physical_and_private_identity_survives_copy_only(self):
        self.alias()
        public = self.cli("artifacts", "import", "--manifest", str(self.root / "manifest.json"))
        found, portable = (public["facts"][fid] for fid in ("heldout_error", "clean_root_replay_error"))
        self.assertEqual(found["source"]["locator"], "pointer:/heldout_error")
        self.assertEqual(portable["source"]["locator"], "line:1:pointer:/heldout_error")
        self.assertEqual(found["source"]["sha256"], portable["source"]["sha256"])
        self.assertEqual((found["kind"], portable["kind"]), ("OBSERVED", "OBSERVED"))
        trusted = ingest_manifest(self.root / "manifest.json", self.root)["facts"]
        identity = trusted["heldout_error"].reading_identity
        self.assertEqual(identity, trusted["clean_root_replay_error"].reading_identity)
        self.assertEqual(deepcopy(trusted["clean_root_replay_error"]).reading_identity, identity)
        self.assertNotIn("reading_identity", json.loads(json.dumps(trusted["clean_root_replay_error"])))

    def test_actual_brief_advisor_keeps_the_gap_open(self):
        self.alias()
        brief = self.advise("--brief")
        self.assertEqual(brief["goal_input_status"], "TRUE")
        self.assertEqual(brief["next_move"], "RESOLVE_PREMISE")
        self.assertIn("TRIPLE_AFFIRMATIVE_OPEN", brief["flags"])

    def test_existing_project_state_is_unchanged_by_full_and_brief_advice(self):
        self.alias()
        # Initialize the actual project tables without dispatching or reserving a run.
        # The protocol role needs registration-grade identity fields (#159); the
        # research-context JSON keeps the other roles.
        protocol = {"code_sha256": digest(self.inputs["context.json"]), "config_sha256": digest(self.inputs["context.json"]),
                    "data_sha256": digest(self.inputs["context.json"]), "data_split": "synthetic-only",
                    "init": "none", "seed": "none", "checkpoint": "none", "schedule": "none",
                    "sample_work": {"rows": 0}, "numeric_protocol": "no computation"}
        (self.root / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        store = ProjectStore(self.root)
        store.initialize({"schema": 1, "bindings": [
            {"role": role, "path": "context.json", "sha256": digest(self.inputs["context.json"])}
            for role in ("code", "config", "data", "evaluator")] + [
            {"role": "protocol", "path": "protocol.json", "sha256": digest((self.root / "protocol.json").read_bytes())}],
            "allowed_commands": [[sys.executable, "--version"]], "output_roots": ["outputs"],
            "budget": {"wall_seconds": 1}})
        self.assert_reuse(self.advise())
        self.assertIn("TRIPLE_AFFIRMATIVE_OPEN", self.advise("--brief")["flags"])


if __name__ == "__main__":
    unittest.main()
