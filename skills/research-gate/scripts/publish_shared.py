"""Regenerate the shared project repo from a project (0 LLM tokens). Commit and push are separate.

    python publish_shared.py --program <project>/program.json --root <project> --to <shared-repo>
                             [--today D] [--knowledge <registry.jsonl> ...]

Steps: refuse if the shared program.json was edited since the last export (merge it into the
project first); list pending contrib/ folders; run share.export; copy the tools, README and CI
from assets/shared-repo; render BOARD.md; add those files to MANIFEST.json so their integrity is
checked too; run share check, program check and contrib check.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets" / "shared-repo"
TOOLS = ("program.py", "program_board.py", "share.py")
HTTP_SCHEME = re.compile(r"https?://", re.IGNORECASE)
HTTP_URL = re.compile(r"""https?://[^\s<>"'|，、。（）()\[\]]+""", re.IGNORECASE)
LOCAL_EVIDENCE = re.compile(r"(?i)^(?:[a-z]:[/\\]|file:[/\\]|~?[/\\])")
DRIVE_PATH = re.compile(r"(?i)[a-z]:[/\\]")
LOCAL_PATH = re.compile(r"(?i)file:[/\\]|(?<![a-z0-9_./-])/(?:Users|home)/|(?<!\\)\\\\(?=[\w?.])")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def guard(shared: Path) -> list[str]:
    """Problems that must be resolved in the project before regenerating the mirror."""
    manifest = shared / "MANIFEST.json"
    if not manifest.is_file():
        return []
    files = json.loads(manifest.read_text(encoding="utf-8")).get("files", {})
    prog = shared / "program.json"
    if prog.is_file() and files.get("program.json") not in (None, _sha(prog)):
        return ["shared program.json was edited after the last export; merge those changes into the project's program.json first"]
    return []


def _read_registry(path: Path, vault, *, historical: bool = False) -> list[dict]:
    entries = vault.kn.load(path)
    # Previous mirrors may predate the active-placeholder guard. Keep their original
    # status for the changelog and validate their contents as historical records.
    records = [
        dict(entry, status="retracted") if isinstance(entry, dict) and entry.get("status") == "active" else entry
        for entry in entries
    ] if historical else entries
    problems = vault.check_entries(records)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return entries


def _shared_evidence(evidence: list[str], project_root: Path) -> list[str]:
    labels = []
    for value in evidence:
        label = value.strip()
        if not LOCAL_EVIDENCE.match(label):
            labels.append(value)
            continue
        path = Path(label)
        if path.is_absolute():
            try:
                labels.append(path.resolve().relative_to(project_root).as_posix())
            except (OSError, RuntimeError, ValueError):
                pass
    return labels or ["(local-note)"]


def _privacy_problems(contents: dict[str, str]) -> list[str]:
    def has_local_path(value) -> bool:
        if isinstance(value, str):
            return bool(DRIVE_PATH.search(HTTP_SCHEME.sub("", value)) or LOCAL_PATH.search(HTTP_URL.sub("", value)))
        if isinstance(value, dict):
            return any(has_local_path(key) or has_local_path(item) for key, item in value.items())
        return isinstance(value, list) and any(map(has_local_path, value))

    def leaks(name: str, text: str) -> bool:
        if not name.lower().endswith(".jsonl"):
            return has_local_path(text)
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                value = line
            if has_local_path(value):
                return True
        return False

    return [f"privacy blocker: local machine path in {name}"
            for name, text in contents.items() if leaks(name, text)]


def check_knowledge_privacy(shared: Path) -> list[str]:
    """Check exported files, including any registries retained from an earlier export."""
    paths = [shared / "KNOWLEDGE.md"]
    folder = shared / "knowledge"
    if folder.is_dir():
        paths.extend(path for path in sorted(folder.iterdir()) if path.suffix.lower() == ".jsonl")
    problems = []
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        name = path.relative_to(shared).as_posix()
        try:
            problems += _privacy_problems({name: path.read_text(encoding="utf-8")})
        except (OSError, UnicodeError) as error:
            problems.append(f"privacy blocker: cannot read {name} ({error})")
    return problems


def _knowledge_summary(registries: dict[str, list[dict]], vault, share, today: str) -> str:
    def cell(value) -> str:
        return " ".join(str(value).split()).replace("|", "\\|")

    active = []
    for entries in registries.values():
        for entry in entries:
            if entry["status"] == "active":
                active.append((vault.project_of(entry), entry))
    active.sort(key=lambda record: (record[0], record[1]["id"]))
    counts = Counter((project, entry["kind"]) for project, entry in active)
    kinds = vault.KIND_SHORT
    lines = ["# 知识库", "", f"> generated_by: publish_shared.py · {cell(today)} · 自动生成", "",
             "## 按项目统计（有效条目）", "",
             "| 项目 | " + " | ".join(kinds.values()) + " |", "|---|---|---|---|---|"]
    for project in sorted({project for project, _ in active}):
        cells = [cell(project), *(str(counts[project, kind]) for kind in kinds)]
        lines.append("| " + " | ".join(cells) + " |")
    for kind, label in kinds.items():
        lines.extend(["", f"## {label}", "",
                      "| 编号 | 项目 | 内容 | 适用范围 | 日期 | 证据 |", "|---|---|---|---|---|---|"])
        for project, entry in active:
            if entry["kind"] != kind:
                continue
            labels = (label.strip() for label in entry["evidence"])
            evidence = [cell(label) for label in labels
                        if label == "(local-note)" or HTTP_SCHEME.match(label) or share._relative_path(label)]
            values = [entry["id"], project, entry["statement"], entry["scope"], entry["date"]]
            cells = [*(cell(value) for value in values), "、".join(evidence) or "—"]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _knowledge_changes(before: dict, after: dict) -> list[str]:
    changes = []
    for key, entry in after.items():
        label = f"知识库 {key[1]}"
        if key not in before:
            changes.append(f"{label} 新增（{entry['kind']}）")
        elif before[key]["status"] != entry["status"]:
            changes.append(f"{label}: {before[key]['status']} → {entry['status']}")
    changes.extend(f"知识库 {eid} 移除" for name, eid in before if (name, eid) not in after)
    return changes


def _prepare_knowledge(paths: list[Path], previous: dict[str, Path], share, today: str,
                       project_root: Path) -> tuple[dict[str, str], list[str]]:
    vault = _load(HERE / "knowledge_vault.py", "knowledge_vault")
    registries, contents = {}, {}
    names: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise ValueError(f"knowledge file not found: {path}")
        if path.suffix.lower() != ".jsonl":
            raise ValueError(f"knowledge file must be .jsonl: {path}")
        name = path.name
        if share._relative_path(name) is None:
            raise ValueError(f"invalid knowledge filename: {name}")
        if name.casefold() in names:
            raise ValueError(f"duplicate knowledge filename: {name}")
        names.add(name.casefold())
        # Check that the registry did not change during validation.
        text = path.read_text(encoding="utf-8")
        entries = _read_registry(path, vault)
        if path.read_text(encoding="utf-8") != text:
            raise ValueError(f"knowledge file changed during export: {path}")
        registries[name] = [dict(entry, evidence=_shared_evidence(entry["evidence"], project_root)) for entry in entries]
        contents[f"knowledge/{name}"] = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in registries[name])
    before = {(name, entry["id"]): entry for name, path in previous.items()
              for entry in _read_registry(path, vault, historical=True)}
    after = {(name, entry["id"]): entry for name, entries in registries.items() for entry in entries}
    contents["KNOWLEDGE.md"] = _knowledge_summary(registries, vault, share, today)
    return contents, _knowledge_changes(before, after)


def _update_changelog(path: Path, previous: bytes | None, knowledge_changes: list[str]) -> None:
    header, current, *history = path.read_text(encoding="utf-8").split("\n## ", 2)
    date, _, bullets = current.partition("\n")
    no_program_changes = bullets.strip() == "- 无变化"
    if knowledge_changes:
        lines = [] if no_program_changes else bullets.strip().splitlines()
        lines.extend(f"- {change}" for change in knowledge_changes)
        current = date + "\n\n" + "\n".join(lines)
        log = "\n## ".join([header, current + ("\n" if history else ""), *history])
        path.write_text(log.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    elif no_program_changes and previous is not None:
        path.write_bytes(previous)


def _retained_knowledge(manifest_path: Path) -> tuple[dict, dict]:
    if not manifest_path.is_file():
        return {}, {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {name: digest for name, digest in manifest.get("files", {}).items()
             if name == "KNOWLEDGE.md"
             or (name.startswith("knowledge/") and name.count("/") == 1 and name.lower().endswith(".jsonl"))}
    limits = {name: size for name, size in manifest.get("file_size_limits", {}).items() if name in files}
    return files, limits


def publish(program: Path, root: Path, shared: Path, today: str,
            knowledge_files: list[Path] | None = None) -> list[str]:
    share = _load(HERE / "share.py", "share")
    blockers = guard(shared)
    if blockers:
        return blockers

    retained_files, retained_limits = {}, {}
    contents = {}
    knowledge_changes = []
    if knowledge_files is None:
        retained_files, retained_limits = _retained_knowledge(shared / share.MANIFEST)
        blockers = check_knowledge_privacy(shared)
    else:
        folder, summary = shared / "knowledge", shared / "KNOWLEDGE.md"
        for path in (folder, summary):
            if share._linked(path):
                return [f"Linked output path: {path.name}"]
        if (folder.exists() and not folder.is_dir()) or (summary.exists() and not summary.is_file()):
            return ["knowledge/ must be a directory and KNOWLEDGE.md must be a file"]
        try:
            previous = {
                p.name: p for p in sorted(folder.iterdir())
                if p.suffix.lower() == ".jsonl" and not share._linked(p) and p.is_file()
            } if folder.is_dir() else {}
            contents, knowledge_changes = _prepare_knowledge(knowledge_files, previous, share, today, project_root=root.resolve())
        except (OSError, SystemExit, ValueError) as error:
            return [str(error)]
        blockers = _privacy_problems(contents)
    if blockers:
        return blockers

    pending = sorted(p.name for p in (shared / "contrib").iterdir() if p.is_dir()) if (shared / "contrib").is_dir() else []
    for name in pending:
        print(f"PENDING contribution contrib/{name}: review it and merge accepted claims into the project")
    changes_path = shared / "CHANGES.md"
    previous_log = changes_path.read_bytes() if changes_path.is_file() else None
    manifest = share.export(program, root, shared, today=today)
    if knowledge_files is not None:
        keep = {path for name, path in previous.items() if f"knowledge/{name}" in contents}
        share._prune_evidence(folder, keep)
        folder.mkdir(exist_ok=True)
        for rel, text in contents.items():
            (shared / rel).write_text(text, encoding="utf-8", newline="\n")
    _update_changelog(changes_path, previous_log, knowledge_changes)

    extra: dict[str, Path] = {f"tools/{t}": HERE / t for t in TOOLS}
    for src in ASSETS.rglob("*"):
        if src.is_file():
            extra[src.relative_to(ASSETS).as_posix()] = src
    for rel, src in extra.items():
        dst = shared / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    board = _load(HERE / "program_board.py", "program_board")
    (shared / "BOARD.md").write_text(board.build(shared / "program.json", today), encoding="utf-8", newline="\n")
    (shared / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8", newline="\n")
    manifest["files"].update(retained_files)
    if retained_limits:
        manifest.setdefault("file_size_limits", {}).update(retained_limits)
    for rel in [*extra, "BOARD.md", ".gitignore", *contents]:
        path = shared / rel
        manifest["files"][rel] = _sha(path)
        size = path.stat().st_size
        if size > manifest["max_bytes"]:
            manifest.setdefault("file_size_limits", {})[rel] = size
    (shared / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    problems = check_knowledge_privacy(shared)
    problems += share.check_shared(shared)
    prog_mod = _load(shared / "tools" / "program.py", "program_shared")
    problems += prog_mod.check(prog_mod.load(shared / "program.json"))[0]
    problems += _load(shared / "tools" / "contrib.py", "contrib").check_contrib(shared)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--program", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--to", type=Path, required=True)
    ap.add_argument("--today", default=_dt.date.today().isoformat())
    ap.add_argument("--knowledge", action="append", type=Path, default=[],
                    help="knowledge registry to mirror (repeat for every registry in the export)")
    args = ap.parse_args(argv)
    problems = publish(args.program, args.root, args.to, args.today, knowledge_files=args.knowledge or None)
    for p in problems:
        print(f"  - {p}")
    print("PUBLISH " + ("BLOCKED" if problems else f"OK -> {args.to} (now review `git status`, commit and push)"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
