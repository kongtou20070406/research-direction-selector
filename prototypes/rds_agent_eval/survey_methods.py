"""Finite raw-code inventory/fractions; no inferred filtering or bootstrap."""
import csv
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import sys

ROLES = ("Project_Lead", "Business_Analyst", "Requirement_Engineer", "Solution_Architect",
         "Data_Scientist", "Developer", "Tester", "Others")
CODES = ("quoted", "not quoted", "-77")


def measure(raw, method):
    if method not in {"role_inventory", "quoted_fractions"} or len(raw) > 2097152:
        raise ValueError("Require a declared finite method and bounded original CSV")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        header = reader.fieldnames
        required = {"ID", *("Q8_ML_Addressing_" + r for r in ROLES)}
        if not header or len(set(header)) != len(header) or not required <= set(header):
            raise ValueError("Original role columns/unique header required")
        rows = list(reader)
    except csv.Error as exc:
        raise ValueError("Original CSV syntax cannot be measured") from exc
    counts = {r: dict.fromkeys(CODES, 0) for r in ROLES}
    ids = set()
    for row in rows:
        if set(row) != set(header) or any(v is None for v in row.values()) or not row["ID"] or row["ID"] in ids:
            raise ValueError("Original row width/identity differs")
        ids.add(row["ID"])
        for role in ROLES:
            code = row["Q8_ML_Addressing_" + role]
            if code not in CODES:
                raise ValueError("Unrecognized Q8 code; no recoding")
            counts[role][code] += 1
    if not rows:
        raise ValueError("Original survey is empty")
    result = {"schema": "rds-survey-measurement-v1", "method": method,
              "data_sha256": hashlib.sha256(raw).hexdigest(), "row_count": len(rows),
              "row_filter": "ALL_ORIGINAL_ROWS", "role_counts": counts,
              "proportion_basis": "QUOTED_OVER_QUOTED_PLUS_NOT_QUOTED",
              "bootstrap_ci": None, "bootstrap_status": "UNKNOWN", "population_inference": "UNKNOWN",
              "free_text_analysis": "NOT_PERFORMED"}
    if method == "quoted_fractions":
        ratios = {r: {"numerator": c["quoted"], "denominator": c["quoted"] + c["not quoted"]}
                  for r, c in counts.items()}
        top = None
        if all(v["denominator"] > 0 for v in ratios.values()):
            fractions = {r: Fraction(v["numerator"], v["denominator"]) for r, v in ratios.items()}
            order = sorted(ROLES, key=lambda r: (-fractions[r], r))
            if fractions[order[1]] != fractions[order[2]]:
                top = order[:2]
        result.update(fractions=ratios, top_roles=top,
                      ranking_status="RECORDED_LABEL_FRACTIONS" if top is not None else "UNKNOWN")
    return result


def run(source, method, output):
    root = Path.cwd().resolve()
    source, output = Path(source).resolve(), Path(output).resolve()
    if not all(p != root and p.is_relative_to(root) for p in (source, output)) or source == output:
        raise ValueError("Declared paths must stay inside the study root")
    with source.open("rb") as stream:
        result = measure(stream.read(2097153), method)
    encoded = json.dumps(result, indent=2, allow_nan=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
    sys.stdout.buffer.write((encoded + "\n").encode("utf-8"))


if __name__ == "__main__":
    run(*sys.argv[1:])
