"""Stdlib-only shared value, identity and rule-schema contracts.

Every layer may import this module; it must never import another rds_ module,
and it stays on hashlib+json only so the leaf-import contract holds.
"""
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


def require(condition, message):
    """Refuse with a plain ValueError; the caller keeps its own message wording."""
    if not condition:
        raise ValueError(message)


def json_digest(value):
    """Sorted-JSON sha256 with default separators; raw bytes hash unchanged.

    This is the identity RSI/ledger records were written with. It is *not*
    ``object_digest``: compact separators change the encoded bytes, so swapping
    the two silently rewrites every stored digest.
    """
    raw = value if isinstance(value, bytes) else json.dumps(
        value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


REQUIRED_RULE_KEYS = {
    "id", "scope", "trigger", "correction", "alternatives",
    "discriminator", "primary_gate", "falsifier", "sources"
}
FORBIDDEN_WORDS = {"guaranteed_gain", "always_works", "universal_optimum", "unfalsifiable"}


def identity(value):
    """Accept the rule-ID alphabet without importing ``re`` (leaf-import contract)."""
    require(isinstance(value, str) and 1 <= len(value) <= 80
            and value[0].isascii() and value[0].isalnum()
            and all(character.isascii() and (character.isalnum() or character in "_.-")
                    for character in value[1:]),
            "Invalid rule ID (up to 80 ASCII letters, numbers, _, . or -)")
    return value


def validate_rule(rule_dict, existing_ids=None, allow_update=False):
    """Strictly validates a decision rule against RSI safety invariants."""
    require(isinstance(rule_dict, dict), "Rule spec must be a dictionary")
    missing = REQUIRED_RULE_KEYS - set(rule_dict)
    require(not missing, f"Missing required rule fields: {', '.join(sorted(missing))}")

    rid = identity(rule_dict["id"])
    if existing_ids and rid in existing_ids and not allow_update:
        raise ValueError(f"Rule ID '{rid}' already exists; use allow_update/--force to revise")

    for str_key in ("scope", "trigger", "correction", "discriminator", "primary_gate", "falsifier"):
        val = rule_dict.get(str_key)
        require(isinstance(val, str) and val.strip(), f"Field '{str_key}' must be a non-empty string")
        for bad in FORBIDDEN_WORDS:
            require(bad not in val.lower(), f"Unscientific/unfalsifiable claim '{bad}' forbidden in '{str_key}'")

    for list_key in ("alternatives", "sources"):
        val = rule_dict.get(list_key)
        require(isinstance(val, list) and len(val) >= 1, f"Field '{list_key}' must be a non-empty list")
        require(all(isinstance(item, str) and item.strip() for item in val),
                f"All elements in '{list_key}' must be non-empty strings")

    return rule_dict
