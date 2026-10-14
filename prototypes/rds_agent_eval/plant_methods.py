"""Finite shared methods for the frozen plant TSV task; no provider or RDS state."""
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

PERIODS = ("Before 1500", "1501-1900", "1901-1984", "1985-2019")
PATHWAYS = ("AgriForest", "Gardening", "Unintentional")


def measure(raw, method):
    if method not in {"period_counts", "gardening_dominance"} or len(raw) > 2097152:
        raise ValueError("Require a declared finite method and bounded original TSV")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter="\t", strict=True)
    if reader.fieldnames != ["introduction.period", "pathway", "n"]:
        raise ValueError("Require the original three-column TSV header")
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise ValueError("Invalid original TSV syntax") from exc
    counts = {period: {} for period in PERIODS}
    for row in rows:
        period, pathway, number = row["introduction.period"], row["pathway"], row["n"]
        if (set(row) != set(reader.fieldnames) or period not in counts or pathway not in PATHWAYS
                or pathway in counts[period] or number is None or not number.isascii() or not number.isdigit()):
            raise ValueError("Invalid, duplicate or non-integral period count")
        counts[period][pathway] = int(number)
    if any(set(values) != set(PATHWAYS) for values in counts.values()):
        raise ValueError("Require every original period/pathway cell")
    result = {"schema": "rds-plant-measurement-v1", "method": method,
              "data_sha256": hashlib.sha256(raw).hexdigest(), "period_counts": counts}
    if method == "gardening_dominance":
        main = [p for p in PERIODS if counts[p]["Gardening"] > max(
            counts[p]["AgriForest"], counts[p]["Unintentional"])]
        result.update(main_periods=main, earliest_observed_main_period=main[0] if main else None,
                      precision="RECORDED_PERIOD_BIN")
    return result


def run(source, method, output):
    root = Path.cwd().resolve()
    source, output = Path(source).resolve(), Path(output).resolve()
    if not all(p != root and p.is_relative_to(root) for p in (source, output)) or source == output:
        raise ValueError("Declared data/output paths must stay inside the study root")
    with source.open("rb") as stream:
        raw = stream.read(2097153)
    result = measure(raw, method)
    encoded = json.dumps(result, indent=2, allow_nan=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
    sys.stdout.buffer.write((encoded + "\n").encode("utf-8"))


if __name__ == "__main__":
    run(*sys.argv[1:])
