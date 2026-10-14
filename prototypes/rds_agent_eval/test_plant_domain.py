"""Controlled numeric fixtures; original paid tasks are never restarted here."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker, verifier = load("plant_methods"), load("plant_verifier")
sha = lambda raw: hashlib.sha256(raw).hexdigest()


class PlantDomainTests(unittest.TestCase):
    def setUp(self):
        cells = ((5, 1, 3), (2, 4, 1), (1, 7, 3), (1, 9, 2))
        lines = ['"introduction.period"\t"pathway"\t"n"']
        for period, counts in zip(worker.PERIODS, cells):
            lines.extend(f'"{period}"\t"{pathway}"\t{n}' for pathway, n in zip(worker.PATHWAYS, counts))
        self.source = ("\n".join(lines) + "\n").encode()
        self.claim = {"earliest_observed_main_period": "1501-1900", "main_periods": list(worker.PERIODS[1:]),
                      "exact_crossover_year": None, "scope": "RECORDED_PERIOD_COUNTS"}

    def check(self, value, claim=None, source=None, **pins):
        source = self.source if source is None else source
        raw = json.dumps(value).encode()
        return verifier.verify(source, raw, source_sha256=pins.get("source_sha256", sha(source)),
                               measured_sha256=pins.get("measured_sha256", sha(raw)),
                               claim=self.claim if claim is None else claim)

    def test_both_methods_independently_checked_without_gold_or_scientific_promotion(self):
        for method in ("period_counts", "gardening_dominance"):
            result = self.check(worker.measure(self.source, method))
            self.assertEqual(result["task_correctness"], "PASS")
            self.assertTrue(all(result["checks"].values()))
            self.assertEqual(result["scientific_support"], "UNKNOWN")
            self.assertEqual(result["free_text_review"], "UNKNOWN")

    def test_missing_duplicate_wrong_delimiter_and_invalid_count_reject_both_parsers(self):
        lines = self.source.splitlines(keepends=True)
        variants = [self.source.replace(b"\t", b","), b"".join(lines[:-1]),
                    b"".join(lines[:-1] + [lines[1]]), self.source.replace(b"\t5\n", b"\t5.5\n")]
        measured = worker.measure(self.source, "period_counts")
        for source in variants:
            with self.subTest(source=source), self.assertRaises(ValueError):
                worker.measure(source, "period_counts")
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.check(measured, source=source)

    def test_original_pins_tampered_measurements_and_unjustified_claims_fail_closed(self):
        measured = worker.measure(self.source, "gardening_dominance")
        for pin in ("source_sha256", "measured_sha256"):
            with self.subTest(pin=pin), self.assertRaises(ValueError):
                self.check(measured, **{pin: "0" * 64})
        for number in (99, 5.0, True):
            changed = deepcopy(measured)
            changed["period_counts"]["Before 1500"]["AgriForest"] = number
            self.assertEqual(self.check(changed)["task_correctness"], "FAIL")
        for change in ({"earliest_observed_main_period": "Before 1500"}, {"exact_crossover_year": 1501},
                       {"main_periods": ["1501-1900"]}, {"scope": "HISTORICAL_CAUSATION"}):
            self.assertEqual(self.check(measured, claim={**self.claim, **change})["task_correctness"], "FAIL")
        changed = {**measured, "main_periods": ["Before 1500"]}
        self.assertFalse(self.check(changed)["checks"]["measurement_dominance"])

    def test_cli_containment_original_bytes_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            data, output = root / "table.csv", root / "out.json"
            data.write_bytes(self.source)
            with patch.object(worker.Path, "cwd", return_value=root), patch.object(worker.sys, "stdout"):
                worker.run(data, "gardening_dominance", output)
                self.assertEqual(data.read_bytes(), self.source)
                raw = output.read_bytes()
                self.assertEqual(verifier.verify(self.source, raw, source_sha256=sha(self.source),
                    measured_sha256=sha(raw), claim=self.claim)["task_correctness"], "PASS")
                with self.assertRaises(FileExistsError):
                    worker.run(data, "gardening_dominance", output)
                with self.assertRaises(ValueError):
                    worker.run(data, "period_counts", root.parent / "escaped.json")
            self.assertEqual(output.read_bytes(), raw)
