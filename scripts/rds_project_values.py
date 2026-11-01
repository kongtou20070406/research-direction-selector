"""Leaf value contracts: canonical JSON, JSON-only digest, assertion, bounded number.

Stdlib/rds_core-only mirror of the rds_project helper contracts, so consumers
such as the owned advisor need no ProjectStore (L2) import. Canonical bytes,
digest JSON-only behavior (bytes fail, never a raw-buffer digest), exception
text and numeric validation are byte-identical to the rds_project originals.
"""
from __future__ import annotations

import hashlib
import math

from rds_core import canonical

__all__ = ["canonical", "digest", "number", "require"]


def digest(value):
    """Hash compact JSON encoded as UTF-8; bytes are not JSON values."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, name, positive=False):
    require(not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (value > 0 if positive else value >= 0),
            f"Invalid {name}")
    return float(value)
