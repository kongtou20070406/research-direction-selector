"""Read-only observation adapter; the caller supplies the native ProjectStore.

No production imports, execution, ledger writes or inferred domain transitions.
Raw bytes remain distinct from future before/action/after domain projections.
"""
import hashlib
from pathlib import Path
import re


def read_observations(store, pins):
    """Read all supplied receipt pins in order, including negative observations.

    Pins contain run_id and receipt_sha256. Native receipt decoding remains
    authoritative; errors propagate without repair, filtering or execution.
    The 256 KiB cap bounds observation reads, not an execution budget.
    """
    if (type(pins) is not list or not 1 <= len(pins) <= 128
            or any(type(p) is not dict or set(p) != {"run_id", "receipt_sha256"}
                   or type(p["run_id"]) is not str or not 1 <= len(p["run_id"]) <= 128
                   or type(p["receipt_sha256"]) is not str
                   or re.fullmatch(r"[a-f0-9]{64}", p["receipt_sha256"]) is None for p in pins)
            or len({p["run_id"] for p in pins}) != len(pins)):
        raise ValueError("Require 1..128 distinct original receipt pins")
    root = Path(store.root).resolve()
    receipts = []
    with store._db(readonly=True) as db:
        db.execute("BEGIN")
        for pin in pins:
            row = db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id=?",
                             (pin["run_id"],)).fetchone()
            if row is None:
                raise ValueError("Original receipt missing; no rerun: " + pin["run_id"])
            receipt = store._receipt(row)
            if receipt["sha256"] != pin["receipt_sha256"] or Path(receipt["cwd"]).resolve() != root:
                raise ValueError("Original receipt pin or root mismatch")
            receipts.append(receipt)
    observations = []
    for receipt in receipts:
        artifacts, paths, streams, size = [], set(), set(), 0
        for item in receipt["artifacts"]:
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root) or path == root or path in paths:
                raise ValueError("Artifact escapes root or repeats a resolved path")
            paths.add(path)
            if item["kind"] in {"stdout.bin", "stderr.bin"}:
                if item["kind"] in streams:
                    raise ValueError("Duplicate original process stream")
                streams.add(item["kind"])
            size += item["size"]
            if type(item["size"]) is not int or item["size"] < 0 or size > 262144:
                raise ValueError("Observation exceeds declared byte cap")
            with path.open("rb") as stream:
                raw = stream.read(item["size"] + 1)
            if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Original artifact missing or tampered: " + item["path"])
            artifacts.append({**item, "bytes": raw})
        if receipt["process_started"] is True and streams != {"stdout.bin", "stderr.bin"}:
            raise ValueError("Original started-process streams are missing")
        observations.append({"receipt": receipt, "artifacts": artifacts})
    return {"schema": "memento-rds-observations-v1", "root": str(root),
            "scope": "SUPPLIED_PINS_ONLY", "observations": observations,
            "receipt_assurance": "NATIVE_DECODED_BYTES_BOUND", "domain_projection": "UNKNOWN",
            "selection_effect": "NONE", "evidence_status": "UNKNOWN"}
