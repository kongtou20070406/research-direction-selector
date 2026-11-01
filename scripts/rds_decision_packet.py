"""F1 immutable, JSON-only decision data. Tags confer no evidence authority."""
from dataclasses import dataclass
import json
import math

from rds_core import canonical, object_digest

SCHEMA = "rds-decision-packet-v1"
BLOCKS = ("identity", "goal", "evidence", "candidates", "provenance")
KINDS = frozenset(("DECLARED", "OBSERVED", "INFERRED", "UNKNOWN"))
MAX_PACKET_BYTES = 4 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _strict(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def finite(value, depth=0):
        require(depth <= 40, "packet nesting limit")
        if isinstance(value, float):
            require(math.isfinite(value), "non-finite packet value")
        elif isinstance(value, (dict, list)):
            for child in value.values() if isinstance(value, dict) else value:
                finite(child, depth + 1)

    require(isinstance(text, str) and len(text) <= MAX_PACKET_BYTES, "packet text limit")
    require(len(text.encode("utf-8")) <= MAX_PACKET_BYTES, "packet byte limit")
    try:
        value = json.loads(text, object_pairs_hook=pairs)
        finite(value)
    except RecursionError as exc:
        raise ValueError("packet nesting limit") from exc
    return value


@dataclass(frozen=True, slots=True)
class Field:
    """Canonical JSON storage prevents mutable nested values escaping the boundary."""
    name: str
    kind: str
    value_json: str
    sources: tuple[str, ...] = ()

    def __post_init__(self):
        require(isinstance(self.name, str) and 0 < len(self.name) <= 512, "invalid field name")
        require(self.kind in KINDS, "invalid field kind")
        require(type(self.sources) is tuple and len(self.sources) <= 64
                and all(isinstance(s, str) and 0 < len(s) <= 1024 for s in self.sources)
                and tuple(sorted(set(self.sources))) == self.sources, "invalid source references")
        value = _strict(self.value_json)
        require(canonical(value) == self.value_json, "field JSON must be canonical")
        require(self.kind != "UNKNOWN" or value is None, "UNKNOWN must have null value")

    @property
    def value(self):
        return json.loads(self.value_json)

    def to_dict(self):
        return {"kind": self.kind, "value": self.value, "sources": list(self.sources)}


@dataclass(frozen=True, slots=True)
class DecisionPacket:
    identity: tuple[Field, ...]
    goal: tuple[Field, ...]
    evidence: tuple[Field, ...]
    candidates: tuple[Field, ...]
    provenance: tuple[Field, ...]

    def __post_init__(self):
        for name in BLOCKS:
            fields = getattr(self, name)
            require(type(fields) is tuple and len(fields) <= 64
                    and all(type(f) is Field for f in fields), "invalid block: " + name)
            names = tuple(f.name for f in fields)
            require(names == tuple(sorted(set(names))), "duplicate or unsorted fields")
        require(tuple(f.name for f in self.identity) == ("project",), "identity needs project")
        require(tuple(f.name for f in self.goal) == ("statement",), "goal needs statement")
        require(tuple(f.name for f in self.provenance) == ("issues", "limits", "sources", "status"),
                "invalid provenance fields")
        for field in self.identity + self.goal:
            require(field.kind in ("DECLARED", "UNKNOWN") and
                    (field.value is None or isinstance(field.value, str)), "invalid declaration")
        meta = {f.name: f.value for f in self.provenance}
        require(meta["status"] in ("COMPLETE", "PARTIAL"), "invalid import status")
        require(isinstance(meta["sources"], dict) and len(meta["sources"]) <= 64, "invalid sources")
        require(isinstance(meta["issues"], list) and len(meta["issues"]) <= 256
                and all(isinstance(s, str) for s in meta["issues"]), "invalid issues")
        require(isinstance(meta["limits"], dict), "invalid limits")
        require(all(f.kind == ("DECLARED" if f.name == "limits" else "INFERRED")
                    for f in self.provenance), "invalid provenance tags")
        require(not meta["issues"] or meta["status"] == "PARTIAL", "issues require PARTIAL")
        for ref, source in meta["sources"].items():
            require(isinstance(source, dict) and set(source) == {"locator", "sha256", "bytes", "status"}
                    and source["locator"] == ref and source["status"] in ("READ", "PARTIAL", "UNKNOWN"),
                    "invalid source record")
            digest, size = source["sha256"], source["bytes"]
            require((digest is None and size is None) or
                    (isinstance(digest, str) and len(digest) == 64
                     and all(c in "0123456789abcdef" for c in digest)
                     and type(size) is int and 0 <= size <= 1048576), "invalid source digest/size")
            require(source["status"] == "UNKNOWN" or digest is not None, "read source needs digest")
            require(source["status"] == "READ" or meta["status"] == "PARTIAL", "incomplete source requires PARTIAL")
        for fields in (getattr(self, name) for name in BLOCKS):
            for field in fields:
                require(set(field.sources) <= set(meta["sources"]), "dangling source reference")
                require(field.kind != "OBSERVED" or bool(field.sources), "observation needs source")
                require(field.kind != "OBSERVED" or all(meta["sources"][s]["status"] in ("READ", "PARTIAL")
                        for s in field.sources), "observation needs readable source")
        evidence = {f.name for f in self.evidence}
        for field in self.candidates:
            value = field.value
            require(field.kind == "DECLARED" and isinstance(value, dict)
                    and set(value) == {"claim", "actor", "premises", "goal"}, "invalid candidate")
            require(isinstance(value["claim"], str) and 0 < len(value["claim"]) <= 16384,
                    "candidate needs claim")
            require(value["actor"] in ("human", "ai", "unknown"), "invalid candidate actor")
            premises = value["premises"]
            require(isinstance(premises, list) and all(isinstance(p, str) for p in premises)
                    and len(premises) <= 64 and premises == sorted(set(premises))
                    and set(premises) <= evidence, "invalid candidate premises")
            require(value["goal"] in (None, "statement"), "invalid candidate goal")
        require(len(self.to_json().encode("utf-8")) <= MAX_PACKET_BYTES, "packet byte limit")

    @classmethod
    def from_json(cls, text):
        value = _strict(text)
        require(isinstance(value, dict) and set(value) == {"schema", *BLOCKS}
                and value["schema"] == SCHEMA, "invalid packet schema/blocks")
        blocks = []
        for name in BLOCKS:
            require(isinstance(value[name], dict) and len(value[name]) <= 64, "invalid block")
            fields = []
            for key, row in sorted(value[name].items()):
                require(isinstance(row, dict) and set(row) == {"kind", "value", "sources"}
                        and isinstance(row["sources"], list), "invalid tagged field")
                fields.append(Field(key, row["kind"], canonical(row["value"]), tuple(row["sources"])))
            blocks.append(tuple(fields))
        return cls(*blocks)

    def to_dict(self):
        return {"schema": SCHEMA, **{name: {f.name: f.to_dict() for f in getattr(self, name)}
                                    for name in BLOCKS}}

    def to_json(self):
        return canonical(self.to_dict())

    @property
    def id(self):
        return "packet:" + object_digest(self.to_dict())
