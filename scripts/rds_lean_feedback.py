"""Bounded, source-bound display parsing for Lean 4 --json diagnostics.

This module treats compiler output as untrusted text. Its output is never proof
evidence and cannot grant permission or establish a premise.
"""

from dataclasses import dataclass
import hashlib
import json

SCHEMA_VERSION = 1
MAX_OUTPUT_BYTES = 256 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_DIAGNOSTICS = 512
MAX_MESSAGE_CHARS = 8192
MAX_PARSE_ERRORS = 64


def _reject_constant(value):
    raise ValueError("non-finite JSON number: " + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + str(key)[:80])
        result[key] = value
    return result


def _bounded_error(errors, message):
    if len(errors) < MAX_PARSE_ERRORS:
        errors.append(message[:512])
    elif errors[-1] != "additional parse errors omitted (limit 64)":
        errors[-1] = "additional parse errors omitted (limit 64)"


def _position(value):
    if not isinstance(value, dict):
        return None, None
    line, column = value.get("line"), value.get("column")
    if type(line) is not int or line < 1:
        line = None
    if type(column) is not int or column < 0:
        column = None
    return line, column


@dataclass(frozen=True, slots=True)
class LeanFeedback:
    """Immutable JSON snapshot; parsing status is not a proof verdict."""

    _json: str

    def to_dict(self):
        return json.loads(self._json)

    def to_json(self):
        return self._json


def parse_feedback(source, compiler_output):
    """Parse Lean 4 line-delimited ``--json`` diagnostics without executing Lean.

    ``source`` is the exact source byte sequence used for the source binding.
    Text inputs are encoded as UTF-8. Oversized compiler output is clipped only
    at complete line boundaries and reported as truncated.
    """
    if isinstance(source, str):
        source = source.encode("utf-8", errors="strict")
    if not isinstance(source, bytes):
        raise TypeError("source must be bytes or text")
    if isinstance(compiler_output, str):
        raw = compiler_output.encode("utf-8", errors="surrogatepass")
    elif isinstance(compiler_output, bytes):
        raw = compiler_output
    else:
        raise TypeError("compiler_output must be bytes or text")

    errors = []
    diagnostics = []
    unresolved = []
    truncated = len(raw) > MAX_OUTPUT_BYTES
    data = raw[:MAX_OUTPUT_BYTES]
    if truncated:
        # Never parse a JSON object that may have been cut at the byte limit.
        boundary = data.rfind(b"\n")
        data = data[:boundary + 1] if boundary >= 0 else b""
        _bounded_error(errors, "compiler output exceeded the byte limit; trailing data omitted")

    lines = data.splitlines()
    if len(lines) > MAX_DIAGNOSTICS:
        lines = lines[:MAX_DIAGNOSTICS]
        truncated = True
        _bounded_error(errors, "compiler output exceeded the diagnostic count limit")

    for index, raw_line in enumerate(lines, 1):
        if not raw_line.strip():
            continue
        if len(raw_line) > MAX_LINE_BYTES:
            truncated = True
            _bounded_error(errors, f"line {index}: diagnostic line exceeded the byte limit")
            continue
        try:
            line_text = raw_line.decode("utf-8", errors="strict")
            item = json.loads(line_text, object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
            if not isinstance(item, dict):
                raise ValueError("diagnostic must be a JSON object")
            severity = item.get("severity")
            if severity not in {"error", "warning", "information", "warningAsError"}:
                raise ValueError("missing or unsupported diagnostic severity")
            message = item.get("data")
            if not isinstance(message, str):
                message = item.get("caption")
            if not isinstance(message, str):
                raise ValueError("diagnostic has no text message")
            if len(message) > MAX_MESSAGE_CHARS:
                message = message[:MAX_MESSAGE_CHARS]
                truncated = True
                _bounded_error(errors, f"line {index}: diagnostic message clipped")
            line, column = _position(item.get("pos"))
            diagnostic = {"severity": severity, "line": line,
                          "column": column, "message": message}
            diagnostics.append(diagnostic)
            if item.get("kind") == "Tactic.unsolvedGoals":
                unresolved.append(message)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError,
                RecursionError) as exc:
            _bounded_error(errors, f"line {index}: {str(exc)}")

    if truncated and not errors:
        _bounded_error(errors, "compiler output was truncated")
    if truncated or errors:
        parse_status = "PARTIAL" if diagnostics else "UNKNOWN"
    else:
        parse_status = "COMPLETE"

    result = {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "parse_status": parse_status,
        "diagnostics": diagnostics,
        "unresolved_subgoals": unresolved,
        "parse_errors": errors,
        "truncated": truncated,
        "proof_status": "UNKNOWN",
        "assurance": "NONE",
        "permission_granted": False,
        "usable_as_proved_premise": False,
    }
    frozen_json = json.dumps(result, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
    return LeanFeedback(frozen_json)
