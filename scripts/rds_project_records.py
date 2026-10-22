"""Pure retained-record decoding; callers supply integrity errors and hashing."""

import json


def decode_receipt(row, integrity_error, digest):
    """Decode one existing row without reading storage or repairing its contents."""
    prefix = f"Project receipt integrity failure: run {row['run_id']} body "
    suffix = "; inspect retained state"

    def unique(items):
        keys = [key for key, _ in items]
        if len(set(keys)) != len(keys):
            raise integrity_error(prefix + "repeats a JSON key" + suffix)
        return dict(items)
    try:
        receipt = json.loads(row["body"], object_pairs_hook=unique)
    except integrity_error:
        raise
    except (ValueError, RecursionError):
        raise integrity_error(prefix + "is not valid JSON" + suffix) from None
    if not isinstance(receipt, dict):
        kind = ("null" if receipt is None else "boolean" if isinstance(receipt, bool) else "array"
                if isinstance(receipt, list) else "string" if isinstance(receipt, str) else "number")
        raise integrity_error(prefix + f"is a JSON {kind}, not an object" + suffix)
    if receipt.get("run_id") != row["run_id"]:
        raise integrity_error(prefix + "names a different run" + suffix)
    # The writer stores digest(body without sha256) in both the body and the row; bind the value read.
    mismatch = integrity_error(prefix + "does not match its recorded sha256" + suffix)
    if receipt.get("sha256") != row["sha256"]:
        raise mismatch
    try:
        recomputed = digest({key: value for key, value in receipt.items() if key != "sha256"})
    except (ValueError, RecursionError):
        # The decoder accepts NaN/Infinity, overflowing floats and lone surrogate escapes; the writer emits none.
        raise integrity_error(prefix + "has no canonical encoding (e.g. a non-finite number or an unpaired"
                              " surrogate)" + suffix) from None
    if recomputed != row["sha256"]:
        raise mismatch
    return receipt
