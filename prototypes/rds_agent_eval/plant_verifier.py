"""Independent original-byte arithmetic check; never writes scientific truth.

The caller supplies original artifact bytes and pins, using native receipt
decoding for RDS. This pure verifier alone cannot establish receipt assurance.
It intentionally does not import the worker or read gold/model answers.
"""
import csv
import hashlib
import io
import json


def verify(source, measured, *, source_sha256, measured_sha256, claim):
    if (len(source) > 2097152 or len(measured) > 65536
            or hashlib.sha256(source).hexdigest() != source_sha256
            or hashlib.sha256(measured).hexdigest() != measured_sha256):
        raise ValueError("Original source/measurement byte pin mismatch or read bound exceeded")
    if type(claim) is not dict or set(claim) != {
            "earliest_observed_main_period", "main_periods", "exact_crossover_year", "scope"}:
        raise ValueError("Require the explicit structured period claim")
    # Independent positional parse and cell inventory; no shared worker helpers.
    rows = list(csv.reader(io.StringIO(source.decode("utf-8-sig")), delimiter="\t", strict=True))
    periods = ["Before 1500", "1501-1900", "1901-1984", "1985-2019"]
    pathways = ["AgriForest", "Gardening", "Unintentional"]
    if len(rows) != 13 or rows[0] != ["introduction.period", "pathway", "n"]:
        raise ValueError("Original TSV shape/header cannot be verified")
    cells = {}
    for row in rows[1:]:
        if (len(row) != 3 or row[0] not in periods or row[1] not in pathways
                or not row[2].isascii() or not row[2].isdigit() or tuple(row[:2]) in cells):
            raise ValueError("Original TSV cell inventory cannot be verified")
        cells[tuple(row[:2])] = int(row[2])
    expected = {p: {w: cells[p, w] for w in pathways} for p in periods}
    main = [p for p in periods if cells[p, "Gardening"] > cells[p, "AgriForest"]
            and cells[p, "Gardening"] > cells[p, "Unintentional"]]
    first = main[0] if main else None
    actual = json.loads(measured)
    if type(actual) is not dict:
        raise ValueError("Original measurement must be a JSON object")
    method = actual.get("method")
    fields = {"schema", "method", "data_sha256", "period_counts"}
    if method == "gardening_dominance":
        fields |= {"main_periods", "earliest_observed_main_period", "precision"}
    counts = actual.get("period_counts")
    typed_counts = (type(counts) is dict and set(counts) == set(periods) and all(
        type(counts[p]) is dict and set(counts[p]) == set(pathways)
        and all(type(n) is int for n in counts[p].values()) for p in periods))
    checks = {
        "measurement_schema": set(actual) == fields and actual.get("schema") == "rds-plant-measurement-v1"
            and method in {"period_counts", "gardening_dominance"} and actual.get("data_sha256") == source_sha256,
        "original_counts": typed_counts and counts == expected,
        "measurement_dominance": method == "period_counts" or (
            actual.get("main_periods") == main and actual.get("earliest_observed_main_period") == first
            and actual.get("precision") == "RECORDED_PERIOD_BIN"),
        "period_claim": claim["earliest_observed_main_period"] == first and claim["main_periods"] == main,
        "claim_precision": claim["scope"] == "RECORDED_PERIOD_COUNTS" and claim["exact_crossover_year"] is None}
    return {"schema": "rds-plant-verification-v1", "source_sha256": source_sha256,
            "measured_sha256": measured_sha256, "checks": checks,
            "task_correctness": "PASS" if all(checks.values()) else "FAIL",
            "scope": "SCOPED_STRUCTURED_CLAIM", "receipt_assurance": "CALLER_MUST_BIND_NATIVE_RECEIPT",
            "free_text_review": "UNKNOWN", "scientific_support": "UNKNOWN", "execution_effect": "NONE"}
