"""Agent-authored display information; no graph mutation or evidence authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

MAX_BYTES = 8 * 1024 * 1024
TEXT_LIMITS = {"title": 120, "summary": 600}
FIELDS = {"schema", "graph_sha256", "snapshot_sha256", "graph", "nodes", "hyperedges"}


def graph_digest(graph):
    """Bind every normalized raw field, including status and array ordering."""
    raw = json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def agent_input(result):
    """Two separate layers. Empty strings are unfilled, not inferred explanations."""
    graph = result.get("graph")
    if not isinstance(graph, dict):
        raise ValueError("An available graph is required for agent input")
    readable = {"schema": 1, "graph_sha256": graph_digest(graph),
                "snapshot_sha256": result.get("snapshot_sha256"),
                "graph": {"title": "", "summary": ""}}
    for key in ("nodes", "hyperedges"):
        readable[key] = {row["id"]: {"title": "", "summary": ""} for row in graph[key]}
    return {"raw": deepcopy(graph), "readable": readable}


def _text_entry(value, where):
    if not isinstance(value, dict) or set(value) - set(TEXT_LIMITS):
        raise ValueError(f"{where} permits only title and summary")
    for key, text in value.items():
        if not isinstance(text, str) or len(text) > TEXT_LIMITS[key]:
            raise ValueError(f"{where}.{key} must be text of at most {TEXT_LIMITS[key]} characters")
        if any(ord(char) < 32 and char not in "\n\t" or 0xd800 <= ord(char) <= 0xdfff for char in text):
            raise ValueError(f"{where}.{key} contains a control character")


def validate_readable(value, result):
    """Reject stale/invalid overlays atomically. Never merge into raw records."""
    graph = result.get("graph")
    if not isinstance(graph, dict):
        raise ValueError("An available graph is required for readable information")
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("Expected a readable object with schema, graph_sha256, snapshot_sha256, graph, nodes, hyperedges; not the raw/readable envelope")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("Readable schema must be integer 1")
    digest = value["graph_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != graph_digest(graph):
        raise ValueError("Readable information is stale or bound to a different raw graph; export fresh agent input")
    snapshot = value["snapshot_sha256"]
    if snapshot is not None and (not isinstance(snapshot, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot)):
        raise ValueError("Readable snapshot_sha256 must be lowercase SHA256 or null")
    if snapshot != result.get("snapshot_sha256"):
        raise ValueError("Readable information belongs to a different snapshot; export fresh agent input")
    _text_entry(value["graph"], "graph")
    for key in ("nodes", "hyperedges"):
        entries = value[key]
        ids = {row["id"] for row in graph[key]}
        if len(ids) > (4096 if key == "nodes" else 8192):
            raise ValueError("Readable graph exceeds display limits")
        if not isinstance(entries, dict) or set(entries) - ids:
            raise ValueError(f"Readable {key} must be an ID-keyed object containing only existing {key}")
        for ident, entry in entries.items():
            _text_entry(entry, f"{key}[{ident}]")
    # Also bound direct API calls, not just files. Non-finite/invalid UTF8 fails.
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Readable information exceeds 8 MiB")
    return deepcopy(value)


def load_readable(path, result):
    def unique_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate readable JSON key: " + key)
            obj[key] = value
        return obj

    def invalid_number(value):
        raise ValueError("Non-finite readable JSON number: " + value)

    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Readable information exceeds 8 MiB")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs, parse_constant=invalid_number)
        return validate_readable(value, result)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid readable information encoding or nesting") from exc


def with_readable(result, value):
    """Return a new result with a separate presentation layer."""
    return {**result, "readable": validate_readable(value, result)}
