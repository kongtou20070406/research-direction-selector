"""Export a research program and selected evidence for collaborators.

Record progress in a changelog and verify the shared files against a manifest.
"""

import sys

sys.dont_write_bytecode = True

import argparse
from datetime import date
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path


TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".lean", ".csv", ".txt",
                 ".py", ".tex", ".yaml", ".yml", ".toml"}
MANIFEST = "MANIFEST.json"
CHANGELOG = "CHANGES.md"
TITLE = "# 共享进展"


def changes(old, new) -> list[str]:
    result = []
    old_star = old.get("north_star", {}).get("current")
    new_star = new.get("north_star", {}).get("current")
    if old_star != new_star:
        result.append(f"北极星 {old_star} → {new_star}")

    for section in ("propositions", "claims", "routes", "papers"):
        before = {item["id"]: item["status"] for item in old.get(section, [])}
        after = {item["id"]: item["status"] for item in new.get(section, [])}
        for item_id, status in after.items():
            if item_id not in before:
                result.append(f"{item_id} 新增（{status}）")
            elif before[item_id] != status:
                result.append(f"{item_id}: {before[item_id]} → {status}")
        result.extend(f"{item_id} 移除" for item_id in before if item_id not in after)
    return result


def _relative_path(reference):
    if not isinstance(reference, str) or ":" in reference or "\0" in reference:
        return None
    path = Path(reference.replace("\\", "/"))
    if path.parts and not path.anchor and ".." not in path.parts:
        return path
    return None


def _is_junction(path):
    """Windows junction test that also works on Python 3.11 (Path.is_junction is 3.12+)."""
    if hasattr(os.path, "isjunction"):
        return os.path.isjunction(path)
    try:  # FILE_ATTRIBUTE_REPARSE_POINT on a directory that is not a symlink
        return bool(os.lstat(path).st_file_attributes & 0x400) and not os.path.islink(path)
    except (AttributeError, OSError):
        return False


def _linked(path):
    return path.is_symlink() or _is_junction(path)


def _tree(root, ignore_git=False):
    """Walk without descending into symlinks or Windows junctions."""
    for directory, subdirs, filenames in os.walk(root):  # Path.walk is 3.12+
        directory = Path(directory)
        if ignore_git and directory == root and ".git" in subdirs:
            subdirs.remove(".git")
        for name in subdirs[:]:
            if _linked(directory / name):
                subdirs.remove(name)
                filenames.append(name)
        yield directory, filenames


def _evidence(root, relative, max_bytes):
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        return None, "missing"
    if relative.suffix.lower() not in TEXT_SUFFIXES:
        return None, "not text"
    with source.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None, "too large"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not text"
    if b"\0" in data:
        return None, "not text"
    return data, None


def _prune_evidence(folder, keep):
    if _linked(folder):
        raise ValueError(f"Linked evidence directory: {folder}")
    for directory, filenames in reversed(list(_tree(folder))):
        for name in filenames:
            path = directory / name
            if _is_junction(path):
                path.rmdir()
            elif path.is_symlink() or path not in keep:
                path.unlink()
        if directory != folder and not any(directory.iterdir()):
            directory.rmdir()


def export(program_path, project_root, shared_dir, today, max_bytes=1_000_000,
           extra_files=("DECISIONS.md",)) -> dict:
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    root = Path(project_root).resolve()
    destination = Path(shared_dir)
    program_data = Path(program_path).read_bytes()
    current = json.loads(program_data.decode("utf-8-sig"))
    previous_path = destination / "program.json"
    first_export = not previous_path.exists()
    previous = {} if first_export else json.loads(previous_path.read_text(encoding="utf-8-sig"))
    delta = changes(previous, current)

    files = {Path("program.json"): program_data}
    reserved = {Path(name) for name in ("program.json", MANIFEST, CHANGELOG, "evidence", ".git")}
    for name in extra_files:
        relative = _relative_path(name)
        if relative is None or Path(relative.parts[0]) in reserved:
            raise ValueError(f"Invalid extra file: {name}")
        source = (root / relative).resolve()
        if source.is_relative_to(root) and source.is_file():
            files[relative] = source.read_bytes()

    skipped = []
    seen = set()
    evidence_dir = destination / "evidence"
    keep = set()
    for claim in current.get("claims", []):
        if claim["status"] not in {"supported", "refuted"}:
            continue
        for reference in (*claim.get("evidence", []), *claim.get("refuted_by", [])):
            relative = _relative_path(reference)
            if relative is None or relative in seen:
                continue
            seen.add(relative)
            data, reason = _evidence(root, relative, max_bytes)
            if reason:
                skipped.append({"path": reference, "reason": reason})
            else:
                files[Path("evidence") / relative] = data
                keep.add(evidence_dir / relative)

    log_path = destination / CHANGELOG
    old_log = log_path.read_text(encoding="utf-8-sig") if log_path.exists() else ""
    title, _, entries = old_log.partition("\n")
    old_log = (entries if title == TITLE else old_log).strip("\n")
    bullets = ["首次导出"] if first_export else delta or ["无变化"]
    log = "\n".join([TITLE, "", f"## {today}", "", *(f"- {line}" for line in bullets)])
    if old_log:
        log += "\n\n" + old_log

    _prune_evidence(evidence_dir, keep)
    destination.mkdir(parents=True, exist_ok=True)
    for relative, data in files.items():
        target = destination / relative
        if target.resolve() != destination.resolve() / relative:
            raise ValueError(f"Linked output path: {relative.as_posix()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    manifest = {
        "date": str(today), "program": current["program"], "max_bytes": max_bytes,
        "files": {path.as_posix(): sha256(data).hexdigest() for path, data in files.items()},
        "skipped": skipped,
    }
    # Program and extra files are copied whole, even when they exceed the evidence cap.
    larger_files = {path.as_posix(): len(data) for path, data in files.items() if len(data) > max_bytes}
    if larger_files:
        manifest["file_size_limits"] = larger_files
    for name, contents in ((CHANGELOG, log), (MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))):
        target = destination / name
        if _linked(target):
            raise ValueError(f"Linked output path: {name}")
        target.write_text(contents + "\n", encoding="utf-8")
    return manifest


def check_shared(shared_dir) -> list[str]:
    destination = Path(shared_dir).resolve()
    try:
        manifest = json.loads((destination / MANIFEST).read_text(encoding="utf-8-sig"))
        files = manifest["files"]
        max_bytes = manifest["max_bytes"]
        size_limits = manifest.get("file_size_limits", {})
        if not isinstance(files, dict) or type(max_bytes) is not int or max_bytes < 0:
            raise ValueError("invalid files or max_bytes")
        if not isinstance(size_limits, dict) or any(type(limit) is not int or limit < max_bytes
                                                  for limit in size_limits.values()):
            raise ValueError("invalid file_size_limits")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"{MANIFEST}: {error}"]

    problems = []
    for name, expected in files.items():
        relative = _relative_path(name)
        path = destination / relative if relative is not None else None
        if path is None or path.resolve() != path or not path.is_file():
            problems.append(f"{name}: missing or invalid file")
            continue
        with path.open("rb") as stream:
            limit = max_bytes if relative.parts[0] == "evidence" else size_limits.get(name, max_bytes)
            if path.stat().st_size > limit:
                problems.append(f"{name}: too large")
            if file_digest(stream, "sha256").hexdigest() != expected:
                problems.append(f"{name}: sha256 mismatch")

    allowed = set(files) | {MANIFEST, CHANGELOG}
    for directory, filenames in _tree(destination, ignore_git=True):
        for filename in sorted(filenames):
            name = (directory / filename).relative_to(destination).as_posix()
            # contrib/ holds collaborators' pull-request submissions; tools/contrib.py checks them.
            if name not in allowed and not name.startswith("contrib/"):
                problems.append(f"{name}: unexpected file")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--program", required=True, type=Path)
    export_parser.add_argument("--root", required=True, type=Path)
    export_parser.add_argument("--to", required=True, type=Path)
    export_parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    export_parser.add_argument("--max-bytes", type=int, default=1_000_000)
    commands.add_parser("check").add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "export":
        if args.max_bytes < 0:
            export_parser.error("--max-bytes must be nonnegative")
        manifest = export(args.program, args.root, args.to, args.today, args.max_bytes)
        print(f"{manifest['program']}: shared {len(manifest['files'])} files, skipped {len(manifest['skipped'])}")
        return 0
    problems = check_shared(args.directory)
    for problem in problems:
        print(problem)
    return int(bool(problems))


if __name__ == "__main__":
    sys.exit(main())
