"""Bounded, local-only text catalog for Lean source files.

Search results are source-text matches, not parsed Lean declarations or proofs.
"""
import hashlib
import json
import os
import stat


MAX_FILES = 512
MAX_BYTES = 8_388_608
MAX_FILE_BYTES = 1_048_576
MAX_ENTRIES = 8192
MAX_OUTPUT_BYTES = 262_144
MAX_QUERY_CHARS = 200
MAX_LIMIT = 100
MAX_METADATA_BYTES = 65_536
MAX_SNIPPET_CHARS = 400
SKIP_DIRS = {".git", ".rds", "target", ".lake", "node_modules", "__pycache__"}
LEAN_SUFFIXES = {".lean"}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _safe_root(root):
    try:
        raw_path = os.fspath(root)
    except TypeError:
        return "", None, "root_not_pathlike"
    if not isinstance(raw_path, str):
        return "", None, "root_path_must_be_text"
    path = os.path.abspath(raw_path)
    try:
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            return path, None, "root_not_local_directory"
        return path, info, None
    except OSError as exc:
        return path, None, "root_unavailable:" + type(exc).__name__


def _walk_lean(root_path):
    """Yield a bounded deterministic inventory without descending reparse points."""
    pending = [(root_path, "")]
    files = []
    entries_seen = 0
    reason = None
    while pending:
        directory, rel_dir = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = []
                for entry in iterator:
                    entries_seen += 1
                    if entries_seen > MAX_ENTRIES:
                        reason = "directory_entry_limit"
                        break
                    entries.append(entry)
        except OSError:
            reason = reason or "directory_read_error"
            continue
        entries.sort(key=lambda item: (item.name.casefold(), item.name))
        descend = []
        for entry in entries:
            rel = os.path.join(rel_dir, entry.name) if rel_dir else entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                reason = reason or "entry_stat_error"
                continue
            if _is_reparse(info) or entry.is_symlink():
                continue
            if stat.S_ISDIR(info.st_mode):
                if entry.name.casefold() not in SKIP_DIRS:
                    descend.append((entry.path, rel))
            elif stat.S_ISREG(info.st_mode) and os.path.splitext(entry.name)[1].lower() in LEAN_SUFFIXES:
                files.append((rel.replace(os.sep, "/"), entry.path, info.st_size))
        pending.extend(reversed(descend))
        if entries_seen > MAX_ENTRIES:
            break
    files.sort(key=lambda item: (item[0].casefold(), item[0]))
    return files, reason


def _read_local_file(root_path, rel_path, path, bound):
    """Open a checked regular file with a no-follow flag where the OS provides it."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_size > bound:
            raise ValueError("file_not_regular_or_over_bound")
        # Recheck the path chain immediately before reading; do not resolve links.
        current = root_path
        for part in rel_path.split("/")[:-1]:
            current = os.path.join(current, part)
            component = os.lstat(current)
            if not stat.S_ISDIR(component.st_mode) or _is_reparse(component):
                raise ValueError("path_reparse_changed")
        final = os.lstat(path)
        if not stat.S_ISREG(final.st_mode) or _is_reparse(final):
            raise ValueError("path_reparse_changed")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(bound + 1)
        if len(raw) > bound:
            raise ValueError("file_read_over_bound")
        return raw
    finally:
        os.close(fd)


def _metadata(root_path, name):
    path = os.path.join(root_path, name)
    try:
        info = os.lstat(path)
        if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
            return {"status": "UNAVAILABLE", "reason": "not_local_regular_file"}
        raw = _read_local_file(root_path, name, path, MAX_METADATA_BYTES)
    except ValueError as exc:
        return {"status": "INVALID", "reason": str(exc)}
    except OSError as exc:
        if getattr(exc, "errno", None) == 2:
            return {"status": "ABSENT"}
        return {"status": "UNAVAILABLE", "reason": type(exc).__name__}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "INVALID", "reason": "metadata_not_utf8"}
    record = {"status": "DETECTED", "raw": text}
    if name == "lake-manifest.json":
        try:
            json.loads(text)
        except (json.JSONDecodeError, ValueError):
            record["status"] = "INVALID"
            record["reason"] = "malformed_json"
    return record


def inspect_project(root) -> dict:
    """Report bounded local directory and metadata observations; run no tools."""
    root_path, info, error = _safe_root(root)
    if error:
        return {"status": "UNKNOWN", "directory": {"status": "UNAVAILABLE", "reason": error},
                "toolchain": {"status": "UNAVAILABLE"}, "lake_manifest": {"status": "UNAVAILABLE"}}
    files, walk_reason = _walk_lean(root_path)
    toolchain = _metadata(root_path, "lean-toolchain")
    manifest = _metadata(root_path, "lake-manifest.json")
    partial = bool(walk_reason or toolchain["status"] in {"INVALID", "UNAVAILABLE"} or
                   manifest["status"] in {"INVALID", "UNAVAILABLE"})
    return {"status": "PARTIAL" if partial else "PASS",
            "directory": {"status": "PRESENT", "lean_file_count": len(files),
                          "scan_limit": MAX_ENTRIES, "truncated": walk_reason is not None,
                          "reason": walk_reason, "root_mtime_ns": info.st_mtime_ns},
            "toolchain": toolchain, "lake_manifest": manifest}


def search_declarations(root, query, *, limit=10, max_files=512, max_bytes=8388608) -> dict:
    """Find bounded raw-text matches; this does not parse declarations or Lean."""
    if not isinstance(query, str) or not query or len(query) > MAX_QUERY_CHARS:
        return {"status": "UNKNOWN", "matches": [], "reason": "query_must_be_1_to_200_characters"}
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        return {"status": "UNKNOWN", "matches": [], "reason": "limit_out_of_range"}
    if type(max_files) is not int or not 1 <= max_files <= MAX_FILES:
        return {"status": "UNKNOWN", "matches": [], "reason": "max_files_out_of_range"}
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        return {"status": "UNKNOWN", "matches": [], "reason": "max_bytes_out_of_range"}
    root_path, _, error = _safe_root(root)
    if error:
        return {"status": "UNKNOWN", "matches": [], "reason": error}

    files, walk_reason = _walk_lean(root_path)
    matches = []
    bytes_read = 0
    files_read = 0
    reasons = []
    truncated = False
    for rel, path, size in files:
        if files_read >= max_files:
            reasons.append("file_limit")
            truncated = True
            break
        if size > MAX_FILE_BYTES or size > max_bytes - bytes_read:
            reasons.append("byte_limit_or_per_file_limit")
            truncated = True
            break
        files_read += 1
        try:
            raw = _read_local_file(root_path, rel, path, min(MAX_FILE_BYTES, max_bytes - bytes_read))
        except (OSError, ValueError) as exc:
            reasons.append("file_read_error:" + type(exc).__name__)
            truncated = True
            continue
        bytes_read += len(raw)
        text = raw.decode("utf-8", errors="replace")
        digest = hashlib.sha256(raw).hexdigest()
        for line_number, line in enumerate(text.splitlines(), 1):
            if query not in line:
                continue
            item = {"path": rel, "line": line_number,
                    "snippet": line[:MAX_SNIPPET_CHARS], "file_sha256": digest,
                    "match_kind": "TEXT_ONLY", "record_type": "TEXT_MATCH"}
            matches.append(item)
            # Keep serialized response bounded, including JSON and UTF-8 expansion.
            encoded_size = len(json.dumps(matches, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if encoded_size > MAX_OUTPUT_BYTES:
                matches.pop()
                reasons.append("output_limit")
                truncated = True
                break
            if len(matches) >= limit:
                # Check if there may be more matches without scanning further lines.
                reasons.append("result_limit")
                truncated = True
                break
        if truncated:
            break
    if walk_reason:
        reasons.append(walk_reason)
        truncated = True
    return {"status": "PARTIAL" if truncated else "PASS", "truncated": truncated,
            "reason": ";".join(dict.fromkeys(reasons)) or None,
            "query": query, "match_semantics": "raw_text_substring_not_Lean_parsing",
            "files_scanned": files_read, "bytes_read": bytes_read, "matches": matches,
            "limits": {"max_files": max_files, "max_bytes": max_bytes,
                       "max_output_bytes": MAX_OUTPUT_BYTES}}
