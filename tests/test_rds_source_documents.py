"""Operation-local parsing reuse must preserve evidence and source boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rds_artifacts as artifacts
import rds_source_documents as documents
from rds_verify_types import digest


class SourceDocumentImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rds-source-documents-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binding = {
            "run_id": "r1", "code_sha256": "a" * 64,
            "config_sha256": "b" * 64, "data_sha256": "c" * 64,
            "data_split": "development",
            "metric": {"definition": "loss", "reduction": "single record"},
        }

    def source(self, identifier, raw=b'{"loss":0.5}', *, name="result.json",
               kind="metric", fmt="json", selector=None, binding=None):
        (self.root / name).write_bytes(raw)
        return {
            "id": identifier, "path": name, "kind": kind, "format": fmt,
            "expected_sha256": digest(raw),
            "binding": deepcopy(self.binding if binding is None else binding),
            "facts": [{"id": identifier, **(selector or {"pointer": "/loss"})}],
        }

    def manifest(self, sources):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"schema": artifacts.SCHEMA, "sources": sources}),
                        encoding="utf-8")
        return path

    def ingest(self, sources):
        return artifacts.ingest_manifest(self.manifest(sources))

    def test_same_file_sources_parse_and_scan_metadata_once(self):
        first = self.source("first")
        second = deepcopy(first)
        second.update(id="second", path="./result.json",
                      facts=[{"id": "second", "pointer": "/loss"}])
        third = deepcopy(first)
        third.update(id="third", facts=[{"id": "third", "pointer": "/loss"}])
        path = self.manifest([first, second, third])
        with patch.object(artifacts, "_read", wraps=artifacts._read) as read, \
                patch.object(documents, "_parse", wraps=documents._parse) as parse, \
                patch.object(documents, "_metadata", wraps=documents._metadata) as metadata:
            result = artifacts.ingest_manifest(path)
        self.assertEqual(result["status"], "IMPORTED")
        self.assertEqual(read.call_count, 2)  # The manifest and the unique original.
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(metadata.call_count, 1)
        for identifier in ("first", "second", "third"):
            fact = result["facts"][identifier]
            self.assertEqual((fact["kind"], fact["value"]), ("OBSERVED", 0.5))
            self.assertEqual(fact["source"]["sha256"], first["expected_sha256"])
            self.assertEqual(fact["source"]["locator"], "pointer:/loss")
        self.assertEqual(result["facts"]["second"]["source"]["path"], "./result.json")

    def test_each_source_checks_its_expected_hash_in_either_order(self):
        good = self.source("good")
        bad = deepcopy(good)
        bad.update(id="bad", expected_sha256="0" * 64,
                   facts=[{"id": "bad", "pointer": "/loss"}])
        for sources in ([good, bad], [bad, good]):
            with self.subTest(order=[s["id"] for s in sources]):
                result = self.ingest(sources)
                self.assertEqual(result["facts"]["good"]["kind"], "OBSERVED")
                self.assertEqual(result["facts"]["good"]["value"], 0.5)
                self.assertEqual(result["facts"]["bad"]["kind"], "UNKNOWN")
                self.assertIsNone(result["facts"]["bad"]["value"])
                self.assertIn("source hash mismatch", result["facts"]["bad"]["reason"])
                self.assertEqual(result["facts"]["bad"]["source"]["sha256"],
                                 good["expected_sha256"])

    def test_interleaved_file_aliases_reuse_until_last_consumer_each_operation(self):
        first = self.source("a-first", name="a.json")
        middle = self.source("b", b'{"loss":0.25}', name="b.json")
        last = deepcopy(first)
        last.update(id="a-last", path="./a.json",
                    facts=[{"id": "a-last", "pointer": "/loss"}])
        path = self.manifest([first, middle, last])
        with patch.object(artifacts, "_read", wraps=artifacts._read) as read, \
                patch.object(documents, "_parse", wraps=documents._parse) as parse, \
                patch.object(documents, "_metadata", wraps=documents._metadata) as metadata:
            before = artifacts.ingest_manifest(path)
            self.assertEqual((read.call_count, parse.call_count, metadata.call_count), (3, 2, 2))
            after = artifacts.ingest_manifest(path)
            self.assertEqual((read.call_count, parse.call_count, metadata.call_count), (6, 4, 4))
        self.assertEqual(after, before)
        self.assertEqual(after["status"], "IMPORTED")
        for identifier, value, physical_path in (("a-first", 0.5, "a.json"),
                                                  ("b", 0.25, "b.json"),
                                                  ("a-last", 0.5, "./a.json")):
            fact = after["facts"][identifier]
            self.assertEqual((fact["kind"], fact["value"]), ("OBSERVED", value))
            self.assertEqual(fact["source"]["path"], physical_path)
            self.assertEqual(fact["source"]["locator"], "pointer:/loss")
        self.assertEqual(after["facts"]["a-first"].reading_identity,
                         after["facts"]["a-last"].reading_identity)

    def test_each_source_checks_its_binding_in_either_order(self):
        good = self.source("good")
        incomplete = deepcopy(good)
        incomplete.update(id="incomplete", facts=[{"id": "incomplete", "pointer": "/loss"}])
        incomplete["binding"].pop("code_sha256")
        for sources in ([good, incomplete], [incomplete, good]):
            with self.subTest(order=[s["id"] for s in sources]):
                result = self.ingest(sources)
                self.assertEqual(result["facts"]["good"]["kind"], "OBSERVED")
                self.assertEqual(result["facts"]["incomplete"]["kind"], "UNKNOWN")
                self.assertIn("code_sha256", result["facts"]["incomplete"]["reason"])
                self.assertNotIn("code_sha256", result["facts"]["incomplete"]["binding"])

    def test_source_binding_conflicts_do_not_pollute_cached_metadata(self):
        good = self.source("good", b'{"run_id":"r1","loss":0.5}')
        conflict = deepcopy(good)
        conflict.update(id="conflict", facts=[{"id": "conflict", "pointer": "/loss"}])
        conflict["binding"]["run_id"] = "r2"
        for sources in ([conflict, good], [good, conflict]):
            with self.subTest(order=[s["id"] for s in sources]):
                result = self.ingest(sources)
                self.assertEqual(result["facts"]["good"]["kind"], "OBSERVED")
                self.assertEqual(result["facts"]["good"]["binding"]["run_id"], "r1")
                self.assertEqual(result["facts"]["conflict"]["kind"], "UNKNOWN")
                source_conflicts = [c for c in result["conflicts"] if "source_id" in c]
                self.assertEqual(source_conflicts, [{"source_id": "conflict", "fields": ["run_id"]}])

    def test_kind_is_part_of_the_parse_boundary(self):
        good = self.source("good", b"loss\n0.5\n", name="result.csv", fmt="csv",
                           selector={"row": 1, "column": "loss"})
        unsupported = deepcopy(good)
        unsupported.update(id="unsupported", kind="log",
                           facts=[{"id": "unsupported", "row": 1, "column": "loss"}])
        for sources in ([good, unsupported], [unsupported, good]):
            with self.subTest(order=[s["id"] for s in sources]):
                result = self.ingest(sources)
                self.assertEqual(result["facts"]["good"]["value"], 0.5)
                self.assertEqual(result["facts"]["good"]["kind"], "OBSERVED")
                self.assertEqual(result["facts"]["unsupported"]["kind"], "UNKNOWN")
                self.assertIn("unsupported source kind/format",
                              result["facts"]["unsupported"]["reason"])

    def test_same_path_json_and_jsonl_keep_physical_locators(self):
        raw = b'\n\n{"loss":0.5}\r\n'
        whole = self.source("whole", raw)
        line = self.source("line", raw, kind="log", fmt="jsonl",
                           selector={"row": 3, "pointer": "/loss"})
        for sources in ([whole, line], [line, whole]):
            with self.subTest(order=[s["id"] for s in sources]):
                result = self.ingest(sources)
                self.assertEqual(result["status"], "IMPORTED")
                self.assertEqual(result["facts"]["whole"]["source"]["locator"], "pointer:/loss")
                self.assertEqual(result["facts"]["line"]["source"]["locator"],
                                 "line:3:pointer:/loss")
                self.assertEqual(result["facts"]["whole"].reading_identity,
                                 result["facts"]["line"].reading_identity)

    def test_multi_record_jsonl_does_not_inherit_whole_json_parse(self):
        raw = b'{"loss":0.5}\n\n{"loss":0.25}\n'
        whole = self.source("whole", raw)
        line = self.source("line", raw, kind="log", fmt="jsonl",
                           selector={"row": 3, "pointer": "/loss"})
        for sources in ([whole, line], [line, whole]):
            with self.subTest(order=[s["id"] for s in sources]):
                result = self.ingest(sources)
                self.assertEqual(result["facts"]["whole"]["kind"], "UNKNOWN")
                self.assertEqual(result["facts"]["line"]["kind"], "OBSERVED")
                self.assertEqual(result["facts"]["line"]["value"], 0.25)
                self.assertEqual(result["facts"]["line"].reading_identity,
                                 (digest(raw), "line:3:pointer:/loss"))

    def test_structured_values_are_independent_between_sources_and_context(self):
        raw = b'{"payload":{"items":[1,{"nested":[2]}]}}'
        first = self.source("first", raw, selector={"pointer": "/payload"})
        second = self.source("second", raw, selector={"pointer": "/payload"})
        result = self.ingest([first, second])
        original = deepcopy(result["facts"]["second"]["value"])
        result["facts"]["first"]["value"]["items"][1]["nested"].append(3)
        self.assertEqual(result["facts"]["second"]["value"], original)
        self.assertEqual(result["context"]["facts"]["second"]["value"], original)
        self.assertEqual((self.root / "result.json").read_bytes(), raw)

    def test_next_import_reads_fresh_bytes_and_rejects_old_expected_hash(self):
        source = self.source("loss")
        path = self.manifest([source])
        with patch.object(artifacts, "_read", wraps=artifacts._read) as read:
            before = artifacts.ingest_manifest(path)
            (self.root / "result.json").write_bytes(b'{"loss":0.1}')
            after = artifacts.ingest_manifest(path)
        self.assertEqual(read.call_count, 4)
        self.assertEqual(before["facts"]["loss"]["value"], 0.5)
        self.assertEqual(after["facts"]["loss"]["kind"], "UNKNOWN")
        self.assertIn("source hash mismatch", after["facts"]["loss"]["reason"])
        self.assertNotEqual(before["facts"]["loss"]["source"]["sha256"],
                            after["facts"]["loss"]["source"]["sha256"])

    def test_invalid_json_aliases_keep_each_source_unknown(self):
        cases = (
            (b'{"loss":', "Expecting value"),
            (b'{"loss":NaN}', "non-finite JSON constant"),
            (b'{"loss":1e999}', "non-finite numeric value"),
            (b'{"loss":1,"loss":0}', "duplicate JSON key"),
            (b'{"loss":' + b"[" * 33 + b"0" + b"]" * 33 + b"}", "JSON nesting exceeds 32"),
        )
        for raw, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                first = self.source("first", raw)
                second = self.source("second", raw)
                result = self.ingest([first, second])
                self.assertEqual(result["status"], "INCOMPLETE")
                for identifier in ("first", "second"):
                    fact = result["facts"][identifier]
                    self.assertEqual(fact["kind"], "UNKNOWN")
                    self.assertIsNone(fact["value"])
                    self.assertIn(expected_reason, fact["reason"])
                self.assertEqual({row["source_id"] for row in result["missing"]},
                                 {"first", "second"})


class SourceDocumentOwnershipTests(unittest.TestCase):
    def test_metadata_results_are_owned_by_each_caller(self):
        document = documents.SourceDocument(
            b'{"binding":{"metric":{"definition":"loss","reduction":"mean"}},"loss":0.5}')
        observed, conflicts = document.metadata("metric", "json")
        observed["metric"]["definition"] = "changed by caller"
        conflicts.append("caller conflict")
        fresh, fresh_conflicts = document.metadata("metric", "json")
        self.assertEqual(fresh["metric"]["definition"], "loss")
        self.assertEqual(fresh_conflicts, [])

    def test_extracted_structures_cannot_mutate_later_selectors(self):
        document = documents.SourceDocument(b'{"payload":{"nested":[1,2]}}')
        first, locator, _ = document.extract({"pointer": "/payload"}, "metric", "json")
        first["nested"].append(3)
        second, second_locator, _ = document.extract({"pointer": "/payload"}, "metric", "json")
        self.assertEqual(second, {"nested": [1, 2]})
        self.assertEqual((locator, second_locator), ("pointer:/payload", "pointer:/payload"))


class OwnedSourceDocumentTests(unittest.TestCase):
    def test_real_completed_output_parses_once_per_collection_and_rechecks_tampering(self):
        # Reuse the original CLI fixture, including its real launch/receipt/budget.
        # This is composition of its helpers, not inheritance of its full suite.
        import test_rds_owned_advisor as existing
        import rds_owned_advisor as owned
        from rds_project import ProjectStore

        fixture = existing.OwnedAdvisorCLITests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

        def add_observations(policy):
            for identifier, pointer in (("baseline.score.alias1", "/score"),
                                        ("baseline.score.alias2", "/score"),
                                        ("baseline.run_id", "/run_id")):
                policy["observations"].append({
                    "fact": identifier, "run_id": "baseline", "path": "outputs/baseline.json",
                    "selector": {"pointer": pointer}, "format": "json",
                })

        fixture.initialize(mutate_policy=add_observations)
        fixture.create()
        completed = fixture.execute()
        fixture.assert_owned_receipt(completed["receipt"], "baseline", "SUCCEEDED")
        store = ProjectStore(fixture.root)
        original_snapshot = store.snapshot()
        original_launches = fixture.starts()
        self.assertEqual(original_launches, ["baseline"])
        output = fixture.root / "outputs/baseline.json"
        original_bytes = output.read_bytes()
        receipt_sha = completed["receipt"]["sha256"]
        fact_ids = ("baseline.score", "baseline.score.alias1", "baseline.score.alias2", "baseline.run_id")

        def collect():
            with store._db(True) as db:
                db.execute("BEGIN")
                state = owned._state(store, db)
            with patch.object(documents, "strict_json", wraps=documents.strict_json) as parse:
                result = owned._collect(store, state)
            return result, parse.call_count

        first, first_parses = collect()
        second, second_parses = collect()
        self.assertEqual((first_parses, second_parses), (1, 1))
        self.assertEqual(second, first)
        nodes, _, coverage, _ = second
        facts = {node["owned_fact"]["id"]: node["owned_fact"]
                 for node in nodes if "owned_fact" in node}
        self.assertEqual(coverage["parsed_observations"], 4)
        self.assertEqual(coverage["errors"], [])
        for identifier in fact_ids:
            fact = facts[identifier]
            self.assertEqual(fact["value"], "baseline" if identifier == "baseline.run_id" else -1)
            self.assertEqual(fact["kind"], "OBSERVED")
            self.assertTrue(fact["reliable"])
            self.assertEqual(fact["source"], {
                "path": "outputs/baseline.json", "sha256": digest(original_bytes),
                "locator": "pointer:/run_id" if identifier == "baseline.run_id" else "pointer:/score",
                "receipt_id": receipt_sha,
            })

        # Equal byte length ensures the second read detects digest drift, rather
        # than merely classifying a size change. No new execution is requested.
        changed_bytes = original_bytes.replace(b'"score": -1', b'"score": -2')
        self.assertNotEqual(changed_bytes, original_bytes)
        self.assertEqual(len(changed_bytes), len(original_bytes))
        output.write_bytes(changed_bytes)
        changed, changed_parses = collect()
        self.assertEqual(changed_parses, 0)
        changed_nodes, _, changed_coverage, _ = changed
        changed_facts = {node["owned_fact"]["id"]: node["owned_fact"]
                         for node in changed_nodes if "owned_fact" in node}
        self.assertEqual(changed_coverage["parsed_observations"], 0)
        self.assertTrue(any(error.get("path") == "outputs/baseline.json"
                            and "Artifact hash mismatch" in error["reason"]
                            for error in changed_coverage["errors"]))
        for identifier in fact_ids:
            self.assertEqual(changed_facts[identifier]["kind"], "UNKNOWN")
            self.assertIsNone(changed_facts[identifier]["value"])
            self.assertFalse(changed_facts[identifier]["reliable"])
        final_snapshot = store.snapshot()
        for field in ("runs", "receipts", "budget"):
            self.assertEqual(final_snapshot[field], original_snapshot[field])
        self.assertEqual(fixture.starts(), original_launches)


if __name__ == "__main__":
    unittest.main()
