"""No-state golden parity for the receipt codec against the frozen parent method."""

import ast
import builtins
import hashlib
import inspect
import json
from pathlib import Path
import random
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rds_project as project
import rds_project_records as records


PARENT_HEAD = "92130d1b21d6dec0278f870176e541d6ae5077dc"
PARENT_SOURCE_SHA256 = "c2e4c6c2ae0167c3f670c1efd98ced731bd1b5d9dd5c11ea1cbe973f4184c63b"
# Literal source, dedented from ProjectStore._receipt at PARENT_HEAD; never read live Store code.
PARENT_SOURCE = r'''def _receipt(row):
    """One owned receipt row (run_id, sha256, body); a damaged row is rejected, never repaired (#104)."""
    prefix = f"Project receipt integrity failure: run {row['run_id']} body "
    suffix = "; inspect retained state"

    def unique(items):
        keys = [key for key, _ in items]
        if len(set(keys)) != len(keys):
            raise ReceiptIntegrityError(prefix + "repeats a JSON key" + suffix)
        return dict(items)
    try:
        receipt = json.loads(row["body"], object_pairs_hook=unique)
    except ReceiptIntegrityError:
        raise
    except (ValueError, RecursionError):
        raise ReceiptIntegrityError(prefix + "is not valid JSON" + suffix) from None
    if not isinstance(receipt, dict):
        kind = ("null" if receipt is None else "boolean" if isinstance(receipt, bool) else "array"
                if isinstance(receipt, list) else "string" if isinstance(receipt, str) else "number")
        raise ReceiptIntegrityError(prefix + f"is a JSON {kind}, not an object" + suffix)
    if receipt.get("run_id") != row["run_id"]:
        raise ReceiptIntegrityError(prefix + "names a different run" + suffix)
    # The writer stores digest(body without sha256) in both the body and the row; bind the value read.
    mismatch = ReceiptIntegrityError(prefix + "does not match its recorded sha256" + suffix)
    if receipt.get("sha256") != row["sha256"]:
        raise mismatch
    try:
        recomputed = digest({key: value for key, value in receipt.items() if key != "sha256"})
    except (ValueError, RecursionError):
        # The decoder accepts NaN/Infinity, overflowing floats and lone surrogate escapes; the writer emits none.
        raise ReceiptIntegrityError(prefix + "has no canonical encoding (e.g. a non-finite number or an unpaired"
                                    " surrogate)" + suffix) from None
    if recomputed != row["sha256"]:
        raise mismatch
    return receipt
'''


def original(error_type, digest):
    namespace = {"json": json, "ReceiptIntegrityError": error_type, "digest": digest}
    exec(compile(PARENT_SOURCE, "<frozen-parent-receipt>", "exec"), namespace)
    return namespace["_receipt"]


def exception_signature(exc):
    if exc is None:
        return None
    return (type(exc), str(exc), exc.args, exc.__suppress_context__,
            exception_signature(exc.__cause__), exception_signature(exc.__context__))


def outcome(function, row):
    try:
        return ("return", function(row))
    except Exception as exc:
        return ("raise", exception_signature(exc))


def sealed_row(body=None):
    body = {"run_id": "r1", "assessment": {"task_gain": "UNKNOWN"}} if body is None else body
    sha = project.digest(body)
    return {"run_id": body["run_id"], "sha256": sha,
            "body": project.canonical({**body, "sha256": sha})}


class ReceiptCodecParity(unittest.TestCase):
    def assert_parity(self, row, error_type=None, digest=None):
        error_type = project.ReceiptIntegrityError if error_type is None else error_type
        digest = project.digest if digest is None else digest
        expected = outcome(original(error_type, digest), row)
        self.assertEqual(outcome(lambda value: records.decode_receipt(value, error_type, digest), row), expected)
        with patch.object(project, "ReceiptIntegrityError", error_type), patch.object(project, "digest", digest):
            self.assertEqual(outcome(project.ProjectStore._receipt, row), expected)
        return expected

    def test_golden_source_identity(self):
        self.assertEqual(hashlib.sha256(PARENT_SOURCE.encode("utf-8")).hexdigest(), PARENT_SOURCE_SHA256)

    def test_valid_spellings_and_binary_encodings(self):
        for value in [None, True, False, 0, -0.0, 1.5, 10 ** 400, [], {},
                      [1, {"x": "\u4e2d\u6587 \U0001f600\n\t\\\""}], "\u00e9"]:
            row = sealed_row({"run_id": "r1", "extra": value})
            receipt = json.loads(row["body"])
            spellings = [row["body"], json.dumps(receipt, indent=2), " \n" + row["body"] + "\t"]
            spellings += [row["body"].encode(encoding) for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-32")]
            spellings.append(bytearray(row["body"], "utf-8"))
            for body in spellings:
                with self.subTest(value=value, representation=type(body).__name__):
                    candidate = {**row, "body": body}
                    before = dict(candidate)
                    self.assertEqual(self.assert_parity(candidate), ("return", receipt))
                    self.assertEqual(candidate, before)

    def test_sqlite_rows_without_storage_reads_in_codec(self):
        row = sealed_row()
        with sqlite3.connect(":memory:") as db:
            db.row_factory = sqlite3.Row
            retained = db.execute("SELECT ? AS run_id, ? AS sha256, ? AS body",
                                  (row["run_id"], row["sha256"], row["body"])).fetchone()
        db.close()
        self.assertEqual(self.assert_parity(retained), ("return", json.loads(row["body"])))

    def test_named_integrity_negatives_and_precedence(self):
        row = sealed_row()
        content = json.loads(row["body"])
        mismatch = "does not match its recorded sha256"
        cases = [("{", "is not valid JSON"), ("\ufeff{}", "is not valid JSON"),
                 (b"\xff", "is not valid JSON"), ("[" * 10000 + "]" * 10000, "is not valid JSON")]
        cases += [(body, "is a JSON " + kind + ", not an object") for body, kind in
                  [("null", "null"), ("true", "boolean"), ("false", "boolean"), ("[]", "array"),
                   ('"x"', "string"), ("1", "number"), ("NaN", "number"), ("1e400", "number")]]
        cases += [("{}", "names a different run"),
                  (project.canonical({**content, "run_id": "other"}), "names a different run"),
                  (project.canonical({**content, "sha256": "wrong"}), mismatch),
                  (project.canonical({**content, "assessment": {"task_gain": "PASS"}}), mismatch),
                  (project.canonical({"run_id": "r1", "sha256": row["sha256"]}), mismatch),
                  ('{"run_id":"other",' + row["body"][1:], "repeats a JSON key"),
                  (row["body"][:-1] + ',"extra":{"x":1,"x":2}}', "repeats a JSON key"),
                  ('{"run_id":"r1","run_\\u0069d":"r1"}', "repeats a JSON key")]
        for token in ["NaN", "Infinity", "-Infinity", "1e400", "-1e400", '"\\ud800"', '"\\udfff"']:
            cases.append((row["body"][:-1] + ',"extra":{"x":' + token + "}}",
                          "has no canonical encoding (e.g. a non-finite number or an unpaired surrogate)"))
            cases.append((json.dumps({**content, "sha256": "wrong"})[:-1] + ',"extra":' + token + "}", mismatch))
        for body, reason in cases:
            with self.subTest(body=repr(body)[:100], reason=reason):
                result = self.assert_parity({**row, "body": body})
                self.assertEqual(result[0], "raise")
                self.assertIs(result[1][0], project.ReceiptIntegrityError)
                self.assertEqual(result[1][1], "Project receipt integrity failure: run r1 body " + reason +
                                 "; inspect retained state")

    def test_unsupported_rows_and_body_types_keep_native_exceptions(self):
        row = sealed_row()
        cases = [None, False, 1, "row", [], (), {}, {"run_id": "r1"},
                 {"run_id": "r1", "body": row["body"]}]
        cases += [{**row, "body": body} for body in [None, True, 1, 1.5, {}, [], (), memoryview(b"{}")]]
        cases += [{**row, "sha256": value} for value in [None, False, 1, [], {}, b"hash"]]
        cases += [{**row, "run_id": value} for value in [None, False, 1, [], {}, b"r1"]]
        for candidate in cases:
            with self.subTest(row=repr(candidate)[:120]):
                self.assertEqual(self.assert_parity(candidate)[0], "raise")

    def test_seeded_malformed_json_corpus(self):
        rng = random.Random(6004)
        row = sealed_row()
        for index in range(200):
            body = "".join(rng.choice('{}[],:\"\\nulltrue0129e+- \n\ud800') for _ in range(rng.randrange(100)))
            with self.subTest(index=index):
                self.assert_parity({**row, "body": body})

    def test_large_integer_parser_limit_parity(self):
        # Honor the interpreter's configured digit limit, including a disabled limit.
        row = sealed_row()
        for digits in (5000, 10000):
            with self.subTest(digits=digits):
                self.assert_parity({**row, "body": "1" * digits})
                self.assert_parity({**row, "body": row["body"][:-1] + ',"extra":' + "1" * digits + "}"})

    def test_existing_missing_hash_behavior_with_injected_digest(self):
        row = {"run_id": None, "sha256": None, "body": "{}"}
        self.assertEqual(self.assert_parity(row, digest=lambda value: None), ("return", {}))

    def test_digest_exception_parity_and_identity(self):
        row = sealed_row()
        exceptions = [ValueError("value"), RecursionError("depth"),
                      UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogate"),
                      OverflowError("overflow"), TypeError("type"), RuntimeError("runtime")]
        for exception in exceptions:
            def fail(value):
                raise exception
            with self.subTest(exception=type(exception).__name__):
                self.assert_parity(row, digest=fail)
                if not isinstance(exception, (ValueError, RecursionError)):
                    with self.assertRaises(type(exception)) as caught:
                        records.decode_receipt(row, project.ReceiptIntegrityError, fail)
                    self.assertIs(caught.exception, exception)

    def test_injected_error_type_and_duplicate_reraise(self):
        class CustomIntegrityError(RuntimeError):
            pass
        row = sealed_row()
        for body in ["{", "[]", '{}', '{"x":1,"x":2}', row["body"][:-1] + ',"x":NaN}']:
            with self.subTest(body=body):
                result = self.assert_parity({**row, "body": body}, error_type=CustomIntegrityError)
                self.assertIs(result[1][0], CustomIntegrityError)
        sentinel = CustomIntegrityError("decoder sentinel")
        with patch.object(project.json, "loads", side_effect=sentinel):
            for decode in (original(CustomIntegrityError, project.digest),
                           lambda row: records.decode_receipt(row, CustomIntegrityError, project.digest)):
                with self.assertRaises(CustomIntegrityError) as caught:
                    decode(row)
                self.assertIs(caught.exception, sentinel)

    def test_facade_is_static_one_argument_and_forwards_original_objects(self):
        self.assertIsInstance(inspect.getattr_static(project.ProjectStore, "_receipt"), staticmethod)
        self.assertEqual(str(inspect.signature(project.ProjectStore._receipt)), "(row)")
        row, result = object(), object()
        with patch.object(project.receipt_records, "decode_receipt", return_value=result) as decode:
            self.assertIs(project.ProjectStore._receipt(row), result)
            decode.assert_called_once_with(row, project.ReceiptIntegrityError, project.digest)

    def test_live_digest_and_canonical_monkeypatch_seams(self):
        row = sealed_row()
        calls = []
        def digest(body):
            calls.append(body)
            return row["sha256"]
        self.assert_parity(row, digest=digest)
        expected = {key: value for key, value in json.loads(row["body"]).items() if key != "sha256"}
        self.assertEqual(calls, [expected] * 3)
        with patch.object(project, "canonical", return_value='"patched"') as canonical:
            result = self.assert_parity(row)
            self.assertEqual(result[0], "raise")
            self.assertEqual(canonical.call_count, 3)

    def test_leaf_import_isolation_without_a_child_process(self):
        source = Path(records.__file__).read_text(encoding="utf-8")
        imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(len(imports), 1)
        self.assertIsInstance(imports[0], ast.Import)
        self.assertEqual([alias.name for alias in imports[0].names], ["json"])
        seen = []
        def guarded_import(name, *args, **kwargs):
            self.assertEqual(name, "json")
            seen.append(name)
            return builtins.__import__(name, *args, **kwargs)
        with patch.object(builtins, "open", side_effect=AssertionError("codec must not open files")):
            isolated = {"__builtins__": {**vars(builtins), "__import__": guarded_import}}
            exec(compile(source, "<isolated-record-codec>", "exec"), isolated)
            row = sealed_row()
            self.assertEqual(isolated["decode_receipt"](row, project.ReceiptIntegrityError, project.digest),
                             json.loads(row["body"]))
        self.assertEqual(seen, ["json"])


if __name__ == "__main__":
    unittest.main()
