"""Keep the installed skills and this repo as one source.

Magpie distributes these skills. Its library entries are links (Windows junctions) to
``skills/<name>`` in this repo, it links them into the Claude and Codex routers, and it copies
them into the Gemini dir (agy, antigravity). Every location in ``sync_targets.json`` is
checked: ``links`` must resolve to this repo, ``copies`` must match it byte for byte (CRLF
aside), so an edit made anywhere shows up either in ``git status`` or here.

    python scripts/sync_skills.py              status: links, copies, git state (exit 1 if not in sync)
    python scripts/sync_skills.py link         turn missing or identical copies into links
    python scripts/sync_skills.py hook         install a pre-push hook that runs the repo checks

``link`` never overwrites: an install copy that differs from the repo is reported and left
alone until the difference is merged into the repo. Identical copies are moved to
``~/.skill-link-backups/<date>/`` before the link is created.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
TARGETS = Path(__file__).resolve().parent / "sync_targets.json"
IGNORED_NAMES = {"__pycache__", ".pytest_cache", ".magpie-library", ".git"}
IGNORED_DIRS = {"papers"}  # per-machine downloads (git-ignored)


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def load_targets(path: Path = TARGETS) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    links = {k: [expand(p) for p in v] for k, v in data.get("links", {}).items()}
    copies = {k: [expand(p) for p in v] for k, v in data.get("copies", {}).items()}
    return links, copies


def is_link(p: Path) -> bool:
    return p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p))


def make_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def _files(root: Path) -> dict[str, Path]:
    out = {}
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if any(part in IGNORED_NAMES for part in rel.parts):
            continue
        if any(part in IGNORED_DIRS for part in rel.parts[:-1]) or f.suffix == ".pyc" or not f.is_file():
            continue
        out[rel.as_posix()] = f
    return out


def tree_diff(a: Path, b: Path) -> list[str]:
    """Relative paths that differ between two trees, ignoring CRLF and caches."""
    fa, fb = _files(a), _files(b)
    diffs = [f"only in install: {r}" for r in sorted(fa.keys() - fb.keys())]
    diffs += [f"only in repo: {r}" for r in sorted(fb.keys() - fa.keys())]
    for r in sorted(fa.keys() & fb.keys()):
        if fa[r].read_bytes().replace(b"\r\n", b"\n") != fb[r].read_bytes().replace(b"\r\n", b"\n"):
            diffs.append(f"differs: {r}")
    return diffs


def link_state(link: Path, target: Path) -> str:
    if is_link(link):
        try:
            return "linked" if link.resolve() == target.resolve() else "wrong-target"
        except OSError:
            return "broken-link"
    if link.is_dir():
        return "copy"
    return "missing"


def git(*args: str, repo: Path = REPO) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    return r.stdout.strip() if r.returncode == 0 else ""


def status(repo: Path = REPO, targets: Path = TARGETS, fetch: bool = False) -> int:
    links, copies = load_targets(targets)
    bad = 0
    for skill, places in links.items():
        for place in places:
            state = link_state(place, repo / "skills" / skill)
            note = ""
            if state == "copy":
                diffs = tree_diff(place, repo / "skills" / skill)
                note = f" ({len(diffs)} differences)" if diffs else " (identical; run `link`)"
            bad += state != "linked"
            print(f"{'OK ' if state == 'linked' else 'BAD'} link  {skill:20s} {state}{note}  {place}")
    for skill, places in copies.items():
        for place in places:
            if not place.is_dir():
                print(f"BAD copy  {skill:20s} missing  {place}")
                bad += 1
                continue
            diffs = tree_diff(place, repo / "skills" / skill)
            bad += bool(diffs)
            state = "matches repo" if not diffs else f"{len(diffs)} differences: run `magpie library sync`, or merge edits made in the copy into the repo first"
            print(f"{'OK ' if not diffs else 'BAD'} copy  {skill:20s} {state}  {place}")
    if fetch:
        git("fetch", "--quiet", repo=repo)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", repo=repo)
    dirty = [l for l in git("status", "--porcelain", repo=repo).splitlines() if l]
    counts = git("rev-list", "--left-right", "--count", "@{u}...HEAD", repo=repo).split()
    behind, ahead = (counts + ["?", "?"])[:2]
    print(f"git: {branch}, {len(dirty)} uncommitted paths, ahead {ahead} / behind {behind} of upstream")
    print("IN SYNC" if not bad else f"NOT IN SYNC ({bad} problems)")
    return 0 if not bad else 1


def link_all(repo: Path = REPO, targets: Path = TARGETS, backup_root: Path | None = None) -> int:
    links, _ = load_targets(targets)
    backup_root = backup_root or Path.home() / ".skill-link-backups" / _dt.date.today().isoformat()
    refused = 0
    for skill, places in links.items():
        target = repo / "skills" / skill
        for place in places:
            state = link_state(place, target)
            if state == "linked":
                continue
            if state in ("wrong-target", "broken-link"):
                print(f"REFUSED {place}: {state}; remove the old link by hand after checking it")
                refused += 1
                continue
            if state == "copy":
                diffs = tree_diff(place, target)
                if diffs:
                    print(f"REFUSED {place}: differs from the repo; merge these into skills/{skill} first:")
                    for d in diffs[:10]:
                        print(f"    {d}")
                    refused += 1
                    continue
                dest = backup_root / "__".join(p.strip(":\\/") for p in place.parts[-4:])
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(place), str(dest))
                print(f"moved identical copy {place} -> {dest}")
            make_link(place, target)
            print(f"linked {place} -> {target}")
    return 1 if refused else 0


HOOK = """#!/bin/sh
# Installed by scripts/sync_skills.py hook. Blocks a push when the repo checks fail.
PY="${{RDS_PYTHON:-{python}}}"
export PYTHONDONTWRITEBYTECODE=1
"$PY" -B scripts/check_routes.py --repo-only && "$PY" -B -m pytest -q -p no:cacheprovider tests skills/paper-write/tests
"""

# Links already point at this clone; only the copies (Gemini) go stale after a commit or pull.
SYNC_HOOK = """#!/bin/sh
# Installed by scripts/sync_skills.py hook. Refreshes agent skill copies; never blocks git.
if command -v magpie >/dev/null 2>&1; then
  magpie library sync >/dev/null 2>&1 || echo "research-direction-selector: magpie library sync failed; run it by hand" >&2
fi
exit 0
"""


def install_hook(repo: Path = REPO) -> int:
    hooks = repo / ".git" / "hooks"
    py = Path(sys.executable).as_posix()
    for name, body in (("pre-push", HOOK.format(python=py)), ("post-commit", SYNC_HOOK), ("post-merge", SYNC_HOOK)):
        hook = hooks / name
        hook.write_text(body, encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        print(f"installed {hook}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="status", choices=["status", "link", "hook"])
    ap.add_argument("--fetch", action="store_true", help="git fetch before reporting ahead/behind")
    args = ap.parse_args(argv)
    if args.command == "link":
        return link_all()
    if args.command == "hook":
        return install_hook()
    return status(fetch=args.fetch)


if __name__ == "__main__":
    raise SystemExit(main())
