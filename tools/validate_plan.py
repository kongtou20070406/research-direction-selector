"""Map a git diff to the checks that must run before it can merge.

Read-only. Does not import the inspected code and does not run tests unless asked.

The rule this enforces: a change is only reviewable if the evidence it needs is
named in advance. Local use and CI use the same planner, so the two cannot drift.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Declared change budget. Exceeding either number means the change must be split.
MAX_FILES = 8
MAX_NET_LOC = 250

TEST_IMPORT = re.compile(r"^\s*(?:from|import)\s+(rds_\w+)", re.MULTILINE)


def git(root, *args):
    out = subprocess.run(["git", "-C", str(root), *args],
                         capture_output=True, text=True, encoding="utf-8")
    return out.stdout if out.returncode == 0 else ""


def untracked(root):
    """New files are invisible to `git diff`. A new module is exactly the case
    that most needs checking, so untracked files are first-class here."""
    out = git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    return [p.replace("\\", "/") for p in out if p.strip()]


def changed_files(root, base):
    names = git(root, "diff", "--name-status", base).splitlines()
    rows = []
    for line in names:
        if not line.strip():
            continue
        parts = line.split("\t")
        status, path = parts[0][:1], parts[-1]
        rows.append((status, path.replace("\\", "/")))
    seen = {p for _, p in rows}
    for path in untracked(root):
        if path not in seen:
            rows.append(("A", path))
    return rows


def _count_lines(root, path):
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


def net_loc(root, base):
    numstat = git(root, "diff", "--numstat", base).splitlines()
    added = deleted = 0
    for line in numstat:
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        try:
            a, d = cols[0], cols[1]
            added += 0 if a == "-" else int(a)
            deleted += 0 if d == "-" else int(d)
        except ValueError:
            continue
    for path in untracked(root):
        added += _count_lines(root, path)
    return added, deleted


def test_map(root):
    """test module -> set of rds modules it imports or names."""
    mapping = {}
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return mapping
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names = set(TEST_IMPORT.findall(text))
        # Convention: tests/test_rds_x.py covers scripts/rds_x.py.
        stem = path.stem
        if stem.startswith("test_"):
            names.add(stem[len("test_"):])
        mapping[path.name] = names
    return mapping


def classify(path):
    """Kind of change. Only `behaviour` changes count against the LOC budget."""
    name = Path(path).name
    if path.startswith("tests/") and name.endswith(".py"):
        return "test"
    if path.startswith("scripts/") and name.endswith(".py"):
        return "behaviour"
    if (path.startswith("src/") or path.startswith("benchmark/")) and name.endswith((".py", ".rs")):
        return "behaviour"
    if path.startswith(".github/"):
        return "ci"
    if path.startswith("tools/"):
        return "tool"
    if path.startswith("docs/") and name.endswith(".py"):
        return "scratch"
    if name.endswith(".md"):
        return "prose"
    return "other"


def plan(root, base):
    rows = changed_files(root, base)
    added, deleted = net_loc(root, base)
    mapping = test_map(root)
    scripts_touched, tests_needed, checks, notes = [], set(), [], []
    behaviour_files, behaviour_added, behaviour_deleted = 0, 0, 0
    per_file = {}

    for line in git(root, "diff", "--numstat", base).splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        a, d, path = cols[0], cols[1], cols[2].replace("\\", "/")
        try:
            per_file[path] = (0 if a == "-" else int(a), 0 if d == "-" else int(d))
        except ValueError:
            per_file[path] = (0, 0)
    for path in untracked(root):
        if path not in per_file:
            per_file[path] = (_count_lines(root, path), 0)

    for status, path in rows:
        if status == "D":
            notes.append(f"deleted: {path} (confirm no caller remains)")
            continue
        kind = classify(path)
        if path not in per_file:
            continue  # deleted file, already noted
        if kind == "behaviour":
            behaviour_files += 1
            behaviour_added += per_file[path][0]
            behaviour_deleted += per_file[path][1]
        name = Path(path).name
        if kind == "behaviour" and path.startswith("scripts/"):
            module = name[:-3]
            scripts_touched.append(module)
            hits = {t for t, names in mapping.items() if module in names}
            if hits:
                tests_needed |= hits
            else:
                notes.append(f"NO_TEST_MAPPED: scripts/{name} — add one or justify")
        elif kind == "test":
            tests_needed.add(name)
        elif kind == "behaviour":
            notes.append(f"behaviour outside scripts/: {path} — map its tests by hand")
        elif kind == "prose":
            notes.append(f"prose: {path} (must not change behaviour)")
        elif kind == "scratch":
            notes.append(f"scratch script: {path} (one-off analysis, no unit test required)")
        elif kind == "tool":
            notes.append(f"dev tooling: {path} (no product behaviour; run it once to verify)")

    if scripts_touched:
        graph = root / "tools" / "import_graph.py"
        if graph.exists():
            checks.append("python -B tools/import_graph.py > .import-graph.json  "
                          "then require upward/cycles counts unchanged")
        else:
            notes.append("SKIP: tools/import_graph.py missing")
        checks.append("python -B -m unittest discover -s tests -p '<test module>' (focused only)")

    behaviour_net = behaviour_added - behaviour_deleted
    over = []
    if behaviour_files > MAX_FILES:
        over.append(f"behaviour files {behaviour_files} > {MAX_FILES}")
    if behaviour_net > MAX_NET_LOC:
        over.append(f"behaviour net +{behaviour_net} > {MAX_NET_LOC}")

    return {
        "base": base,
        "files_changed": len([r for r in rows if r[0] != "D"]),
        "added": added,
        "deleted": deleted,
        "net_loc": added - deleted,
        "behaviour": {"files": behaviour_files, "added": behaviour_added,
                      "deleted": behaviour_deleted, "net_loc": behaviour_net},
        "budget": {"max_files": MAX_FILES, "max_net_loc": MAX_NET_LOC,
                   "status": "OVER" if over else "OK", "exceeded": over},
        "scripts_touched": scripts_touched,
        "tests_to_run": sorted(tests_needed),
        "checks": checks,
        "notes": notes,
        "verdict": ("SPLIT" if over else
                    "NO_TESTS_MAPPED" if any(n.startswith("NO_TEST_MAPPED") for n in notes) else
                    "PROSE_ONLY_OK" if not scripts_touched else
                    "PLAN_READY"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", default="HEAD~1", help="git ref to diff against")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    try:
        result = plan(args.root.resolve(), args.base)
    except (OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f"plan failed: {error}\n")
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        print()
        return
    print(f"base              : {result['base']}")
    print(f"files / net loc   : {result['files_changed']} files, {result['net_loc']:+d} net "
          f"(+{result['added']}/-{result['deleted']})")
    b = result["behaviour"]
    print(f"behaviour only    : {b['files']} files, {b['net_loc']:+d} net "
          f"(+{b['added']}/-{b['deleted']})  <- the budget counts this")
    print(f"budget            : {result['budget']['status']} "
          f"(max {result['budget']['max_files']} behaviour files / "
          f"{result['budget']['max_net_loc']} behaviour net)")
    if result["budget"]["exceeded"]:
        for item in result["budget"]["exceeded"]:
            print(f"  OVER            : {item}")
    print(f"scripts touched   : {', '.join(result['scripts_touched']) or '-'}")
    print("tests to run      :")
    for name in result["tests_to_run"] or ["<none mapped>"]:
        print(f"  - {name}")
    if result["checks"]:
        print("checks            :")
        for item in result["checks"]:
            print(f"  - {item}")
    if result["notes"]:
        print("notes             :")
        for item in result["notes"]:
            print(f"  - {item}")
    print(f"verdict           : {result['verdict']}")
    sys.exit(1 if result["verdict"] in {"SPLIT", "NO_TESTS_MAPPED"} else 0)


if __name__ == "__main__":
    main()
