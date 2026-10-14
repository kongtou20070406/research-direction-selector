"""Synthetic coded rows only; independent verifier never imports the worker."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prototypes.rds_agent_eval import survey_methods as worker, survey_verifier as verifier

sha = lambda raw: hashlib.sha256(raw).hexdigest()


class SurveyDomainTests(unittest.TestCase):
    def setUp(self):
        header = "ID," + ",".join("Q8_ML_Addressing_" + r for r in worker.ROLES)
        rows = [["quoted", "not quoted", "not quoted", "not quoted", value, "not quoted", "not quoted", "not quoted"]
                for value in ("quoted", "not quoted", "quoted")]
        rows += [["not quoted"] * 8, ["-77"] * 8]
        self.source = (header + "\n" + "\n".join(str(i) + "," + ",".join(row) for i, row in enumerate(rows)) + "\n").encode()
        self.claim = {"top_roles": ["Project_Lead", "Data_Scientist"],
                      "proportion_basis": "QUOTED_OVER_QUOTED_PLUS_NOT_QUOTED", "bootstrap_ci": None}

    def check(self, value, source=None, claim=None, **pins):
        source = self.source if source is None else source
        measured = json.dumps(value).encode()
        return verifier.verify(source, measured, source_sha256=pins.get("source_sha256", sha(source)),
            measured_sha256=pins.get("measured_sha256", sha(measured)), claim=self.claim if claim is None else claim)

    def test_both_methods_count_all_codes_and_verify_only_declared_scope(self):
        for method in ("role_inventory", "quoted_fractions"):
            result = self.check(worker.measure(self.source, method))
            self.assertEqual(result["measurement_correctness"], "PASS")
            self.assertEqual(result["structured_claim_correctness"], "PASS")
            self.assertEqual(result["task_correctness"], "UNKNOWN")
            self.assertEqual(result["scientific_support"], "UNKNOWN")
        self.assertEqual(worker.measure(self.source, "role_inventory")["role_counts"]["Project_Lead"]["-77"], 1)

    def test_bad_codes_duplicate_ids_width_and_header_reject_both_parsers(self):
        lines = self.source.splitlines(keepends=True)
        variants = [self.source.replace(b"quoted", b"guessed", 1), b"".join(lines + [lines[1]]),
                    self.source.replace(b"0,", b"0,extra,", 1), self.source.replace(b"ID,", b"ID,ID,", 1)]
        value = worker.measure(self.source, "role_inventory")
        for source in variants:
            with self.subTest(source=source), self.assertRaises(ValueError):
                worker.measure(source, "role_inventory")
            with self.assertRaises(ValueError):
                self.check(value, source=source)

    def test_byte_pins_numeric_types_query_ci_claim_and_unknown_rank_fail_closed(self):
        value = worker.measure(self.source, "quoted_fractions")
        for name in ("source_sha256", "measured_sha256"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check(value, **{name: "0" * 64})
        for number in (99, 3.0, True):
            changed = deepcopy(value)
            changed["role_counts"]["Project_Lead"]["quoted"] = number
            self.assertEqual(self.check(changed)["measurement_correctness"], "FAIL")
        self.assertEqual(self.check(value, claim={**self.claim, "bootstrap_ci": [49.219, 49.981]})["structured_claim_correctness"], "FAIL")
        sparse = self.source.replace(b"quoted", b"-77").replace(b"not -77", b"-77")
        result = worker.measure(sparse, "quoted_fractions")
        self.assertIsNone(result["top_roles"])
        self.assertEqual(self.check(result, source=sparse, claim={**self.claim, "top_roles": None})["measurement_correctness"], "PASS")
        tied = self.source.replace(b"0,quoted,not quoted", b"0,quoted,quoted").replace(b"2,quoted,not quoted", b"2,quoted,quoted")
        result = worker.measure(tied, "quoted_fractions")
        self.assertIsNone(result["top_roles"])
        self.assertEqual(self.check(result, source=tied, claim={**self.claim, "top_roles": None})["measurement_correctness"], "PASS")

    def test_cli_paths_raw_bytes_and_original_output_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source, output = root / "data.csv", root / "out.json"
            source.write_bytes(self.source)
            with patch.object(worker.Path, "cwd", return_value=root), patch.object(worker.sys, "stdout"):
                worker.run(source, "quoted_fractions", output)
                original = output.read_bytes()
                with self.assertRaises(FileExistsError):
                    worker.run(source, "quoted_fractions", output)
                with self.assertRaises(ValueError):
                    worker.run(source, "role_inventory", root.parent / "outside.json")
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(source.read_bytes(), self.source)
