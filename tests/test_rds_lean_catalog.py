import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rds_lean_catalog as catalog


class LeanCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_locates_text_and_reports_hash_without_claiming_parse(self):
        self.put("A.lean", "-- theorem Commented : True\ntheorem Actual : True := by trivial\n")
        result = catalog.search_declarations(self.root, "theorem Actual")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual((match["path"], match["line"]), ("A.lean", 2))
        self.assertEqual(match["match_kind"], "TEXT_ONLY")
        self.assertEqual(match["record_type"], "TEXT_MATCH")
        self.assertEqual(len(match["file_sha256"]), 64)
        comment = catalog.search_declarations(self.root, "theorem Commented")
        self.assertEqual(comment["matches"][0]["record_type"], "TEXT_MATCH")
        self.assertEqual(comment["matches"][0]["line"], 1)

    def test_chinese_query_and_duplicate_names_are_preserved(self):
        self.put("one/同名.lean", "-- 中文标签\n")
        self.put("two/同名.lean", "def 中文标签 := 1\n")
        result = catalog.search_declarations(self.root, "中文标签")
        self.assertEqual([item["path"] for item in result["matches"]],
                         ["one/同名.lean", "two/同名.lean"])

    def test_empty_project_and_metadata_are_reported_as_local_observations(self):
        inspected = catalog.inspect_project(self.root)
        self.assertEqual(inspected["status"], "PASS")
        self.assertEqual(inspected["directory"]["lean_file_count"], 0)
        self.assertEqual(inspected["toolchain"]["status"], "ABSENT")
        self.assertEqual(catalog.search_declarations(self.root, "needle")["matches"], [])

    def test_inspect_marks_malformed_manifest_non_success(self):
        self.put("lake-manifest.json", "{broken")
        result = catalog.inspect_project(self.root)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["lake_manifest"]["status"], "INVALID")

    def test_metadata_is_bounded_raw_local_text(self):
        (self.root / "lean-toolchain").write_bytes(b"leanprover/lean4:v4.12.0\n")
        result = catalog.inspect_project(self.root)
        self.assertEqual(result["toolchain"],
                         {"status": "DETECTED", "raw": "leanprover/lean4:v4.12.0\n"})
        (self.root / "lake-manifest.json").write_bytes(b"\xff")
        self.assertEqual(catalog.inspect_project(self.root)["lake_manifest"]["status"], "INVALID")

    def test_ignored_build_directories_and_reparse_attribute(self):
        self.put("target/build.lean", "needle\n")
        self.put(".lake/packages/build.lean", "needle\n")
        result = catalog.search_declarations(self.root, "needle")
        self.assertEqual(result["matches"], [])
        reparse = SimpleNamespace(st_file_attributes=catalog._REPARSE)
        self.assertTrue(catalog._is_reparse(reparse))

    def test_result_limit_is_explicitly_partial(self):
        self.put("many.lean", "hit\nhit\nhit\n")
        result = catalog.search_declarations(self.root, "hit", limit=2)
        self.assertEqual(len(result["matches"]), 2)
        self.assertEqual((result["status"], result["truncated"]), ("PARTIAL", True))
        self.assertIn("result_limit", result["reason"])

    def test_file_and_byte_limits_are_reported(self):
        self.put("a.lean", "hit\n")
        self.put("b.lean", "hit\n")
        limited_files = catalog.search_declarations(self.root, "hit", max_files=1)
        self.assertEqual(limited_files["status"], "PARTIAL")
        self.assertIn("file_limit", limited_files["reason"])
        limited_bytes = catalog.search_declarations(self.root, "hit", max_bytes=4)
        self.assertEqual(limited_bytes["status"], "PARTIAL")
        self.assertIn("byte_limit", limited_bytes["reason"])

    def test_output_limit_is_enforced(self):
        self.put("a.lean", "hit\nhit\n")
        with mock.patch.object(catalog, "MAX_OUTPUT_BYTES", 32):
            result = catalog.search_declarations(self.root, "hit")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("output_limit", result["reason"])
        self.assertLessEqual(len(str(result["matches"]).encode("utf-8")), 1024)

    def test_bad_query_and_root_paths_are_rejected(self):
        self.assertEqual(catalog.search_declarations(self.root, "x" * 201)["status"], "UNKNOWN")
        self.assertEqual(catalog.search_declarations(os.fsencode(self.root), "needle")["status"], "UNKNOWN")
        outside = self.root.parent / (self.root.name + "-outside.lean")
        outside.write_text("secret", encoding="utf-8")
        try:
            result = catalog.search_declarations(self.root, "secret/../")
            self.assertEqual(result["matches"], [])
            self.assertEqual(catalog.search_declarations(outside, "secret")["status"], "UNKNOWN")
        finally:
            outside.unlink(missing_ok=True)

    def test_symlinked_directory_is_not_followed(self):
        outside = self.root.parent / (self.root.name + "-external")
        outside.mkdir()
        (outside / "escape.lean").write_text("needle", encoding="utf-8")
        self.addCleanup(outside.rmdir)
        self.addCleanup((outside / "escape.lean").unlink, missing_ok=True)
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink creation unavailable: " + str(exc))
        try:
            result = catalog.search_declarations(self.root, "needle")
            self.assertEqual(result["matches"], [])
        finally:
            link.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
