"""Shared reference values; no ledger or CLI dependencies."""
import json

import rds_core as _core

# Keep the CLI build-version literal in sync without importing the facade.
VERSION = "6.2.0"
# Reading an explicitly supported ledger does not grant execution admission;
# the contract digest and engine binding are checked at their existing boundaries.
READABLE_STATE_VERSIONS = frozenset({
    VERSION, "5.1.0", "5.2.0", "5.3.0", "5.4.0", "5.5.0-rc.1", "5.5.0-rc.2",
    "5.6.0-rc.1", "5.6.0-rc.2", "5.7.0", "5.8.0", "5.9.0-rc.1", "5.9.0-rc.2", "6.0.0-rc.1", "6.0.0",
})
RESOURCES = {"runtime_ms", "runs"}
# Supported local capability names; the L4 CLI probe and L3 search share this
# catalog so neither has to import the other's layer.
CAPABILITIES = ('python_exact', 'sympy_exact', 'mpmath_iv', 'torch_cpu', 'rust_native')


def canonical(value):
    return _core.canonical(value)


def digest(value):
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return _core.bytes_digest(raw)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key: " + key)
            result[key] = value
        return result

    def bad_constant(value):
        raise ValueError("Non-finite JSON value: " + value)

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the parser limit") from exc
