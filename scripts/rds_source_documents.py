"""Operation-local parsed result documents; no IO or evidence authority.

The byte owner still verifies paths, digests, receipts and budgets. Consumers
borrow a document only for validation; metadata and selected values are copied
so one source cannot mutate another source's report. Clear parsed views after
the last consumer to avoid retaining every unique document in a large import.
"""
from copy import deepcopy
import csv
import io
import json

from rds_costs import COST_BINDING_FIELDS, finite, receipt_identity
from rds_verify_types import require

MAX_ROWS = 10000
BINDING_FIELDS = ("run_id",) + COST_BINDING_FIELDS + ("metric",)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("non-finite JSON constant: " + value)

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    _finite_tree(result)
    return result



def _finite_tree(value, depth=0):
    require(depth <= 32, "JSON nesting exceeds 32")
    if isinstance(value, float):
        require(finite(value), "non-finite numeric value")
    elif isinstance(value, dict):
        for child in value.values():
            _finite_tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _finite_tree(child, depth + 1)



def _pointer(document, pointer):
    require(isinstance(pointer, str) and (pointer == "" or pointer.startswith("/")), "expected JSON pointer")
    segments = pointer.split("/")[1:] if pointer else []
    require(len(segments) <= 32, "pointer depth exceeds 32")
    # RFC 6901: every '~' is followed by 0 or 1. Stripping escapes can rebuild one ('~~01' -> '~1'),
    # so check each tilde directly; a malformed spelling would read a field under a second locator.
    # Check the whole pointer before reading, so the reason does not depend on the document.
    require(all(part[:1] in ("0", "1") for segment in segments for part in segment.split("~")[1:]),
            "invalid JSON pointer escape")
    result = document
    for segment in segments:
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(result, list):
            # RFC 6901 indexes are ASCII; Unicode digits would alias one element under a second locator.
            require(key.isascii() and key.isdigit() and (key == "0" or not key.startswith("0")), "invalid array index")
            try:
                result = result[int(key)]
            except IndexError as exc:
                raise MissingPointer(key) from exc
        elif isinstance(result, dict):
            if key not in result:
                raise MissingPointer(key)
            result = result[key]
        else:
            result = result[key]
    return result


class MissingPointer(KeyError):
    """A syntactically valid JSON pointer selects an absent object member or array item."""



def _parse(raw, kind, fmt):
    text = raw.decode("utf-8-sig")
    if fmt == "json":
        return strict_json(text)
    if fmt == "csv" and kind == "metric":
        reader = csv.DictReader(io.StringIO(text))
        require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames), "CSV needs unique headers")
        rows = []
        for row in reader:
            require(len(rows) < MAX_ROWS, "CSV row limit exceeded")
            require(None not in row and None not in row.values(), "CSV row/header width mismatch")
            rows.append(row)
        return rows
    if fmt in ("jsonl", "kv") and kind == "log":
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            require(number <= MAX_ROWS, "log row limit exceeded")
            if fmt == "jsonl":
                rows.append((number, strict_json(line)))
            else:
                require("=" in line, "log must contain explicit key=value records")
                key, value = line.split("=", 1)
                key = key.strip()
                require(key and key not in {r[1] for r in rows}, "duplicate or empty log key")
                try:
                    parsed = strict_json(value.strip())
                except json.JSONDecodeError:
                    parsed = value.strip()
                rows.append((number, key, parsed))
        return rows
    raise ValueError("unsupported source kind/format")



def _metadata(document, fmt):
    if fmt == "jsonl":
        objects = [r[1] for r in document]
    elif fmt == "kv":
        objects = [{row[1]: row[2] for row in document}]
    elif isinstance(document, list):
        objects = document
    else:
        objects = [document]
    result, conflicts = {}, []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        identity, collision = receipt_identity(obj)
        conflicts.extend(collision)
        origins = [obj] + [obj[k] for k in ("binding", "protocol") if isinstance(obj.get(k), dict)]
        for origin in origins:
            if "metric" in origin:
                if "metric" in identity and identity["metric"] != origin["metric"]:
                    conflicts.append("metric")
                else:
                    identity["metric"] = origin["metric"]
        for key in BINDING_FIELDS:
            if key not in identity:
                continue
            if key in result and result[key] != identity[key]:
                conflicts.append(key)
            else:
                result[key] = identity[key]
    return result, sorted(set(conflicts))



def _extract(document, selector, fmt):
    if fmt == "csv":
        row, column = selector.get("row"), selector.get("column")
        require(type(row) is int and 1 <= row <= len(document), "CSV row is 1-based and must exist")
        value = document[row - 1][column]
        if selector.get("type", "number") == "number":
            value = strict_json(value)
            require(finite(value), "CSV metric is not a finite number")
        else:
            require(selector["type"] == "string", "unsupported CSV value type")
        return value, "row:" + str(row) + ":column:" + str(column), column
    if fmt == "kv":
        matching = [row for row in document if row[1] == selector.get("key")]
        require(len(matching) == 1, "log key missing or ambiguous")
        number, key, value = matching[0]
        return value, "line:" + str(number) + ":key:" + key, key
    if fmt == "jsonl":
        number = selector.get("row")
        # 1.0 and true equal 1 but would mint a second locator for the same physical line.
        require(type(number) is int and number >= 1, "JSONL row is a 1-based physical line number")
        matching = [row for row in document if row[0] == number]
        require(len(matching) == 1, "JSONL physical line must exist")
        pointer = selector.get("pointer")
        return _pointer(matching[0][1], pointer), "line:" + str(number) + ":pointer:" + pointer, pointer.rsplit("/", 1)[-1]
    pointer = selector.get("pointer")
    return _pointer(document, pointer), "pointer:" + pointer, pointer.rsplit("/", 1)[-1]


class SourceDocument:
    """One original byte string and its lazily parsed, compatible views.

    The caller owns this object for one operation. A cached parse never verifies
    a file, a receipt, a supplied binding or scientific support. Failed parses
    retain the original exceptions and are not converted into cached success.
    """
    def __init__(self, raw):
        if not isinstance(raw, bytes):
            raise TypeError('SourceDocument needs original bytes')
        self._raw = raw
        self._documents = {}
        self._metadata = {}

    def document(self, kind, fmt):
        """Borrow the parsed value for read-only internal validation."""
        key = (kind, fmt)
        if key not in self._documents:
            self._documents[key] = _parse(self._raw, kind, fmt)
        return self._documents[key]

    def metadata(self, kind, fmt):
        key = (kind, fmt)
        if key not in self._metadata:
            self._metadata[key] = _metadata(self.document(kind, fmt), fmt)
        return deepcopy(self._metadata[key])

    def extract(self, selector, kind, fmt):
        value, locator, selected = _extract(self.document(kind, fmt), selector, fmt)
        return deepcopy(value), locator, selected

    def clear(self):
        """Drop parsed views after the last consumer; original bytes stay owned."""
        self._documents.clear()
        self._metadata.clear()
