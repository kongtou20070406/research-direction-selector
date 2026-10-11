"""Stdlib-only compact JSON and distinct object/raw-byte identity contracts."""
import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def object_digest(value):
    """Hash compact JSON encoded as UTF-8; bytes are not JSON values."""
    return bytes_digest(canonical(value).encode("utf-8"))


def bytes_digest(raw):
    """Hash the supplied buffer unchanged, without JSON encoding or coercion."""
    return hashlib.sha256(raw).hexdigest()
