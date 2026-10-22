"""SHARED bounded JSON values. No storage, clock, callbacks or code loading."""
import hashlib
import json
import math
import re

MAX_BYTES = 262144
MAX_ITEMS = 16384
MAX_DEPTH = 16
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def text(value, label, maximum=8192):
    require(type(value) is str and bool(value.strip()) and len(value) <= maximum,
            label + " must be a bounded nonempty string")
    return value


def token(value, label):
    text(value, label, 128)
    require(TOKEN.fullmatch(value) is not None, label + " must be an identifier")
    return value


def number(value, label, minimum=-1e15, maximum=1e15):
    require(type(value) in (int, float), label + " must be numeric, not boolean")
    require(minimum <= value <= maximum and math.isfinite(value),
            label + " must be finite and in bounds")
    return value


def fields(value, names, label):
    require(type(value) is dict and set(value) == set(names.split()),
            label + " fields mismatch")


def sha256(value, label):
    require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
            label + " must be a lowercase SHA-256")
    return value


def identity(value, label):
    fields(value, "id version sha256", label)
    token(value["id"], label + ".id")
    text(value["version"], label + ".version", 64)
    sha256(value["sha256"], label + ".sha256")


def bounded(value):
    """Reject oversized/cyclic/non-JSON inputs before encoding or copying them."""
    stack, items, weight = [(value, 0)], 0, 0
    while stack:
        item, depth = stack.pop()
        items += 1
        require(items <= MAX_ITEMS and depth <= MAX_DEPTH, "JSON structure exceeds bound")
        kind = type(item)
        if kind is dict:
            require(len(item) <= MAX_ITEMS, "JSON object exceeds bound")
            require(all(type(key) is str for key in item), "JSON keys must be strings")
            for key, child in item.items():
                stack.extend(((key, depth + 1), (child, depth + 1)))
        elif kind is list:
            require(len(item) <= MAX_ITEMS, "JSON list exceeds bound")
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            require(len(item) <= 8192, "JSON string exceeds bound")
            weight += len(item.encode("utf-8"))
        elif kind in (int, float):
            number(item, "JSON number")
            weight += 24
        else:
            require(item is None or kind is bool, "Unsupported JSON value")
            weight += 5
        require(weight <= MAX_BYTES, "JSON payload exceeds bound")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False)
    require(len(raw.encode("utf-8")) <= MAX_BYTES, "JSON payload exceeds byte bound")
    return json.loads(raw)


def digest(value):
    value = bounded(value)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def provenance(value):
    require(type(value) is list and 1 <= len(value) <= 32, "Provenance must have 1..32 sources")
    for source in value:
        fields(source, "source_id locator sha256 version", "provenance")
        token(source["source_id"], "source_id")
        text(source["locator"], "locator", 2048)
        text(source["version"], "source version", 64)
        sha256(source["sha256"], "source hash")


def costs(value):
    """Missing measurements stay null; this does not settle the existing ledger."""
    fields(value, "wall_seconds cpu_seconds status", "costs")
    for key in ("wall_seconds", "cpu_seconds"):
        if value[key] is not None:
            number(value[key], key, 0)
    expected = "KNOWN" if all(value[key] is not None for key in
                              ("wall_seconds", "cpu_seconds")) else "UNKNOWN"
    require(value["status"] == expected, "Missing costs must remain UNKNOWN")
