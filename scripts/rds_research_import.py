"""L1 read-only import to F1 JSON. Explicit local files/text only; no discovery.

The JSON handoff avoids an L1 -> L2 dependency. Deserialize with
DecisionPacket.from_json, then project separately. Imported bytes are DATA.
"""
import csv
import io
from pathlib import Path

from rds_bounded_io import read_regular_bytes
from rds_core import canonical, bytes_digest
from rds_source_documents import strict_json

DEFAULT_LIMITS = {"max_sources": 32, "max_file_bytes": 262144,
                  "max_total_bytes": 1048576, "max_rows": 128, "max_text_chars": 16384}
HARD_LIMITS = {"max_sources": 64, "max_file_bytes": 1048576,
               "max_total_bytes": 2097152, "max_rows": 1024, "max_text_chars": 65536}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _field(value, kind="DECLARED", sources=()):
    return {"value": value, "kind": kind, "sources": sorted(sources)}


def import_research(root, *, project=None, goal=None, paths=(), texts=None, candidates=None, limits=None):
    """Return canonical packet JSON, not an execution plan or verified evidence.

Evidence keys are 'file:<relative-posix-path>' / 'text:<name>'. Candidate
premises explicitly name these keys; absent premises become UNKNOWN fields.
Paths must resolve within root; directories and special files are not read.
Limits bound both reads and retained rows/text; any omission yields PARTIAL.
"""
    bounds = {**DEFAULT_LIMITS, **(limits or {})}
    _require(set(bounds) == set(HARD_LIMITS) and all(type(v) is int and 1 <= v <= HARD_LIMITS[k]
             for k, v in bounds.items()), "invalid import limits")
    _require(isinstance(paths, (list, tuple)) and len(paths) <= 256, "path request limit")
    texts, candidates = texts or {}, candidates or {}
    _require(isinstance(texts, dict) and len(texts) <= 256, "text request limit")
    _require(isinstance(candidates, dict) and len(candidates) <= 64, "candidate request limit")
    _require(all(value is None or isinstance(value, str) and len(value) <= 16384
                 for value in (project, goal)), "invalid project/goal declaration")
    base = Path(root).resolve(strict=True)
    _require(base.is_dir(), "root must be a directory")
    entries, evidence, inventory, issues = {}, {}, {}, []
    for name in paths:
        _require(isinstance(name, str) and 0 < len(name) <= 480, "invalid path")
        path = (base / name).resolve()
        _require(path.is_relative_to(base), "source path escapes root")
        relative = path.relative_to(base).as_posix()
        entries["file:" + relative] = (path, None)
    for name, text in texts.items():
        _require(isinstance(name, str) and 0 < len(name) <= 480 and isinstance(text, str)
                 and len(text) <= HARD_LIMITS["max_total_bytes"], "invalid inline text")
        entries["text:" + name] = (None, text)
    if len(entries) > bounds["max_sources"]:
        issues.append("source_limit")
    remaining = bounds["max_total_bytes"]
    for ref, (path, text) in sorted(entries.items())[:bounds["max_sources"]]:
        record = {"locator": ref, "sha256": None, "bytes": None, "status": "UNKNOWN"}
        inventory[ref] = record
        value, kind = None, "UNKNOWN"
        try:
            cap = min(bounds["max_file_bytes"], remaining)
            _require(cap > 0, "total_byte_limit")
            if path is None:
                raw = text.encode("utf-8")
                _require(len(raw) <= cap, "text_byte_limit")
            else:
                raw = read_regular_bytes(path, cap, limit_message="file_byte_limit")
            remaining -= len(raw)
            record.update(sha256=bytes_digest(raw), bytes=len(raw))
            text = raw.decode("utf-8-sig")
            suffix = path.suffix.lower() if path is not None else ""
            truncated = False
            if suffix == ".json":
                value = strict_json(text)
                if isinstance(value, list) and len(value) > bounds["max_rows"]:
                    value, truncated = value[:bounds["max_rows"]], True
            elif suffix == ".csv":
                reader = csv.DictReader(io.StringIO(text), strict=True)
                headers = reader.fieldnames
                _require(headers and all(headers) and len(set(headers)) == len(headers), "invalid CSV headers")
                value = []
                for row in reader:
                    _require(None not in row and None not in row.values(), "CSV width mismatch")
                    if len(value) == bounds["max_rows"]:
                        truncated = True
                        break
                    value.append(row)
            else:
                value = text[:bounds["max_text_chars"]]
                truncated = len(value) < len(text)
            canonical(value).encode("utf-8")
            kind = "OBSERVED"
            record["status"] = "PARTIAL" if truncated else "READ"
            if truncated:
                issues.append(ref + ":content_limit")
        except (OSError, ValueError, UnicodeError, RecursionError, csv.Error) as exc:
            # Stable reason codes exclude host-dependent paths and OS messages.
            reason = str(exc) if str(exc) in {"total_byte_limit", "text_byte_limit", "file_byte_limit"} else type(exc).__name__
            issues.append(ref + ":" + reason)
            value, kind = None, "UNKNOWN"
        evidence[ref] = _field(value, kind, (ref,))
    candidate_fields = {}
    for name, candidate in sorted(candidates.items()):
        _require(isinstance(name, str) and 0 < len(name) <= 512 and isinstance(candidate, dict)
                 and set(candidate) == {"claim", "actor", "premises", "goal"}, "invalid candidate")
        claim, actor, premises, target = (candidate[k] for k in ("claim", "actor", "premises", "goal"))
        _require(isinstance(claim, str) and 0 < len(claim) <= 16384
                 and actor in ("human", "ai", "unknown") and target in (None, "statement")
                 and isinstance(premises, (list, tuple)) and len(premises) <= 64
                 and all(isinstance(p, str) and 0 < len(p) <= 512 for p in premises), "invalid candidate fields")
        for premise in sorted(set(premises)):
            if premise not in evidence:
                evidence[premise] = _field(None, "UNKNOWN")
                issues.append(premise + ":missing_evidence")
        candidate_fields[name] = _field({"claim": claim, "actor": actor,
                                        "premises": sorted(set(premises)), "goal": target})
    _require(len(evidence) <= 64 and len(issues) <= 256, "evidence request limit")
    result = {"schema": "rds-decision-packet-v1", "identity": {"project": _field(project, "UNKNOWN" if project is None else "DECLARED")},
              "goal": {"statement": _field(goal, "UNKNOWN" if goal is None else "DECLARED")},
              "evidence": evidence, "candidates": candidate_fields,
              "provenance": {"sources": _field(inventory, "INFERRED"), "limits": _field(bounds),
                             "issues": _field(sorted(set(issues)), "INFERRED"),
                             "status": _field("PARTIAL" if issues else "COMPLETE", "INFERRED")}}
    serialized = canonical(result)
    _require(len(serialized.encode("utf-8")) <= 4 * 1024 * 1024, "packet byte limit")
    return serialized
