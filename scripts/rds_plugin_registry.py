"""Bounded plugin metadata and exact selection; no code loading or dispatch.

Schemas and resources describe a capability. They do not replace its validators,
authorize execution, settle costs or establish scientific evidence.
"""
from dataclasses import dataclass
import json
import re

KINDS = frozenset({"verifier", "retrieval", "predictor", "graph"})
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}")
FIELDS = frozenset({"kind", "name", "version", "input_schema", "output_schema",
                    "resources", "availability", "permissions"})
REQUIRED = FIELDS - {"permissions"}
MAX_MANIFEST_BYTES = 16384


class PluginUnavailable(ValueError):
    """Exact selection failed; the registry never substitutes another plugin."""


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


@dataclass(frozen=True)
class PluginManifest:
    """Canonical JSON isolates declarations from subsequent caller mutations."""
    _json: str

    @classmethod
    def from_dict(cls, value):
        _require(isinstance(value, dict) and REQUIRED <= value.keys() <= FIELDS,
                 "Plugin manifest requires metadata only; unknown fields are forbidden")
        _require(isinstance(value["kind"], str) and value["kind"] in KINDS,
                 "Unsupported plugin kind")
        _require(isinstance(value["name"], str) and TOKEN.fullmatch(value["name"]),
                 "Invalid plugin name")
        version = value["version"]
        _require(isinstance(version, str) and bool(version.strip()) and len(version) <= 32,
                 "Invalid plugin version")
        for field in ("input_schema", "output_schema", "resources"):
            _require(isinstance(value[field], dict), field + " must be a JSON object")
        availability = value["availability"]
        _require(isinstance(availability, dict) and set(availability) == {"status", "reason"},
                 "Availability requires status and reason")
        _require(isinstance(availability["status"], str) and
                 availability["status"] in {"AVAILABLE", "UNAVAILABLE"}, "Invalid availability")
        reason = availability["reason"]
        _require(isinstance(reason, str) and len(reason) <= 2048 and
                 (availability["status"] == "AVAILABLE" or bool(reason.strip())),
                 "Unavailable plugin requires an explicit reason")
        permissions = value.get("permissions", {"write": False, "execute": False})
        _require(isinstance(permissions, dict) and set(permissions) == {"write", "execute"}
                 and all(type(flag) is bool for flag in permissions.values()),
                 "Permissions require explicit write/execute booleans")
        try:
            encoded = json.dumps({**value, "permissions": permissions}, sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            _require(len(encoded.encode("utf-8")) <= MAX_MANIFEST_BYTES,
                     "Plugin manifest exceeds 16 KiB")
        except (TypeError, RecursionError) as exc:
            raise ValueError("Plugin metadata must be bounded JSON") from exc
        return cls(encoded)

    def to_dict(self):
        return json.loads(self._json)


class PluginRegistry:
    """Trusted callers register metadata; configuration selects an exact identity.

    There are deliberately no handlers, imports, entrypoints or invocation APIs.
    Permission declarations never grant ledger or operating-system authority.
    """
    def __init__(self):
        self._manifests = {}

    def register(self, manifest):
        value = manifest.to_dict() if isinstance(manifest, PluginManifest) else manifest
        manifest = PluginManifest.from_dict(value)
        value = manifest.to_dict()
        key = tuple(value[field] for field in ("kind", "name", "version"))
        _require(key not in self._manifests, "Duplicate plugin identity")
        self._manifests[key] = manifest
        return manifest

    def select(self, config):
        _require(isinstance(config, dict) and set(config) == {"kind", "name", "version"},
                 "Plugin configuration selects kind, name and version only")
        _require(all(isinstance(value, str) for value in config.values()),
                 "Plugin selection fields must be strings")
        key = tuple(config[field] for field in ("kind", "name", "version"))
        manifest = self._manifests.get(key)
        if manifest is None:
            raise PluginUnavailable("Plugin is not registered: " + "/".join(key))
        availability = manifest.to_dict()["availability"]
        if availability["status"] == "UNAVAILABLE":
            raise PluginUnavailable(availability["reason"])
        return manifest

    def describe(self):
        return [self._manifests[key].to_dict() for key in sorted(self._manifests)]
