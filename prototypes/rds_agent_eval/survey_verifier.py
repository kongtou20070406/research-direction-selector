"""Independent positional arithmetic; whole bootstrap task and science stay UNKNOWN."""
import csv
from functools import cmp_to_key
import hashlib
import io
import json


def verify(source, measured, *, source_sha256, measured_sha256, claim):
    if (len(source) > 2097152 or len(measured) > 65536
            or hashlib.sha256(source).hexdigest() != source_sha256
            or hashlib.sha256(measured).hexdigest() != measured_sha256):
        raise ValueError("Original source/measurement bytes differ from pins or bounds")
    if type(claim) is not dict or set(claim) != {"top_roles", "proportion_basis", "bootstrap_ci"}:
        raise ValueError("Explicit scoped role claim required")
    try:
        table = list(csv.reader(io.StringIO(source.decode("utf-8-sig")), strict=True))
    except csv.Error as exc:
        raise ValueError("Original CSV syntax cannot be verified") from exc
    roles = ["Project_Lead", "Business_Analyst", "Requirement_Engineer", "Solution_Architect",
             "Data_Scientist", "Developer", "Tester", "Others"]
    if (len(table) < 2 or len(set(table[0])) != len(table[0]) or "ID" not in table[0]
            or any("Q8_ML_Addressing_" + r not in table[0] for r in roles)
            or any(len(row) != len(table[0]) for row in table[1:])):
        raise ValueError("Original survey header/row width cannot be verified")
    ids = [row[table[0].index("ID")] for row in table[1:]]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("Original survey identities cannot be verified")
    counts = {}
    for role in roles:
        column = [row[table[0].index("Q8_ML_Addressing_" + role)] for row in table[1:]]
        if not set(column) <= {"quoted", "not quoted", "-77"}:
            raise ValueError("Unrecognized original Q8 code; no repair")
        counts[role] = {code: column.count(code) for code in ("quoted", "not quoted", "-77")}
    ratios = {r: {"numerator": c["quoted"], "denominator": c["quoted"] + c["not quoted"]}
              for r, c in counts.items()}
    top = None
    if all(v["denominator"] for v in ratios.values()):
        def compare(a, b):
            left, right = ratios[a]["numerator"] * ratios[b]["denominator"], ratios[b]["numerator"] * ratios[a]["denominator"]
            return (left < right) - (left > right) or (a > b) - (a < b)
        order = sorted(roles, key=cmp_to_key(compare))
        a, b = ratios[order[1]], ratios[order[2]]
        if a["numerator"] * b["denominator"] != b["numerator"] * a["denominator"]:
            top = order[:2]
    actual = json.loads(measured)
    if type(actual) is not dict or actual.get("method") not in {"role_inventory", "quoted_fractions"}:
        raise ValueError("Original measurement schema/method cannot be verified")
    expected = {"schema": "rds-survey-measurement-v1", "method": actual["method"], "data_sha256": source_sha256,
        "row_count": len(ids), "row_filter": "ALL_ORIGINAL_ROWS", "role_counts": counts,
        "proportion_basis": "QUOTED_OVER_QUOTED_PLUS_NOT_QUOTED", "bootstrap_ci": None,
        "bootstrap_status": "UNKNOWN", "population_inference": "UNKNOWN", "free_text_analysis": "NOT_PERFORMED"}
    if actual["method"] == "quoted_fractions":
        expected.update(fractions=ratios, top_roles=top,
                        ranking_status="RECORDED_LABEL_FRACTIONS" if top is not None else "UNKNOWN")
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    claim_expected = {"top_roles": top, "proportion_basis": expected["proportion_basis"], "bootstrap_ci": None}
    checks = {"original_measurement": canonical(actual) == canonical(expected),
              "scoped_claim": canonical(claim) == canonical(claim_expected)}
    return {"schema": "rds-survey-verification-v1", "checks": checks,
        "source_sha256": source_sha256, "measured_sha256": measured_sha256,
        "measurement_correctness": "PASS" if checks["original_measurement"] else "FAIL",
        "structured_claim_correctness": "PASS" if checks["scoped_claim"] else "FAIL", "task_correctness": "UNKNOWN",
        "scope": "RECORDED_Q8_LABELS_ONLY", "receipt_assurance": "CALLER_MUST_BIND_NATIVE_RECEIPT",
        "free_text_review": "UNKNOWN", "scientific_support": "UNKNOWN", "execution_effect": "NONE"}
