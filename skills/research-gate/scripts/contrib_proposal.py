"""List collaborator contributions waiting in the shared repo and propose claim entries (0 LLM tokens).

    python contrib_proposal.py --shared <shared-repo> --program <project>/program.json --out PAGE.md [--fetch]

Looks in three places: ``contrib/`` in the shared repo's working tree, every remote branch, and every
GitHub pull request (``--fetch`` runs ``git fetch origin +refs/pull/*/head:refs/remotes/pr/*``, a
read-only fetch). For each ``contrib/<leaf>-<name>/CLAIM.json`` not yet recorded in the project (no
claim whose ``source`` names that folder), it checks the claim and writes a ready-to-paste
``program.json`` claim with ``status: proposed``. Nothing is merged: the project owner decides.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
STRENGTHS = ("observed", "supported", "proved")


def _git(shared: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(shared), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", check=False)
    return p.returncode, p.stdout


def collect(shared: Path, fetch: bool = False) -> list[dict]:
    """Every contrib folder found, as {folder, where, claim|error, files}. Later sources do not repeat a folder."""
    found: dict[str, dict] = {}
    contrib = shared / "contrib"
    if contrib.is_dir():
        for folder in sorted(p for p in contrib.iterdir() if p.is_dir()):
            files = sorted(f.relative_to(folder).as_posix() for f in folder.rglob("*") if f.is_file())
            item = {"folder": folder.name, "where": "working tree", "files": files}
            try:
                item["claim"] = json.loads((folder / "CLAIM.json").read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                item["error"] = f"CLAIM.json unreadable: {type(exc).__name__}"
            found[folder.name] = item
    if (shared / ".git").exists():
        if fetch:
            _git(shared, "fetch", "--quiet", "origin", "+refs/pull/*/head:refs/remotes/pr/*")
        _, refs = _git(shared, "for-each-ref", "--format=%(refname:short)", "refs/remotes")
        for ref in (r.strip() for r in refs.splitlines()):
            if not ref or ref.endswith("/HEAD"):
                continue
            _, listing = _git(shared, "ls-tree", "-r", "--name-only", ref, "contrib/")
            by_folder: dict[str, list[str]] = {}
            for path in listing.splitlines():
                parts = path.split("/")
                if len(parts) >= 3:
                    by_folder.setdefault(parts[1], []).append("/".join(parts[2:]))
            for name, files in sorted(by_folder.items()):
                if name in found:
                    continue
                item = {"folder": name, "where": ref, "files": sorted(files)}
                rc, raw = _git(shared, "show", f"{ref}:contrib/{name}/CLAIM.json")
                try:
                    item["claim"] = json.loads(raw) if rc == 0 else None
                    if item["claim"] is None:
                        item["error"] = "missing CLAIM.json"
                except json.JSONDecodeError as exc:
                    item["error"] = f"CLAIM.json invalid JSON ({exc.msg})"
                found[name] = item
    return list(found.values())


def check(item: dict, prog: dict) -> list[str]:
    if "error" in item:
        return [item["error"]]
    claim, errors = item["claim"], []
    props = {p["id"] for p in prog.get("propositions", [])}
    if claim.get("proposition") not in props:
        errors.append(f"proposition {claim.get('proposition')!r} is not in the project")
    for key in ("statement", "falsifier", "author"):
        if not str(claim.get(key, "")).strip():
            errors.append(f"missing '{key}'")
    if claim.get("strength") not in STRENGTHS:
        errors.append(f"strength must be one of {list(STRENGTHS)}")
    evidence = claim.get("evidence") or []
    if not evidence:
        errors.append("no evidence files listed")
    missing = [e for e in evidence if e not in item["files"]]
    if missing:
        errors.append(f"evidence not in the folder: {missing}")
    if claim.get("strength") == "proved" and not any(str(e).endswith(".lean") for e in evidence):
        errors.append("a proved claim needs a .lean file")
    return errors


def proposal(item: dict, prog: dict) -> dict:
    claim = item["claim"]
    return {
        "id": f"C-{claim.get('proposition', 'X')}-{item['folder']}",
        "statement": claim.get("statement", ""),
        "strength": claim.get("strength", "observed"),
        "status": "proposed",
        "evidence": [f"contrib/{item['folder']}/{e}" for e in claim.get("evidence", [])],
        "propositions": [claim.get("proposition")],
        "anchored_to": prog.get("north_star", {}).get("current"),
        "source": f"contrib/{item['folder']}",
    }


def pending(items: list[dict], prog: dict) -> list[dict]:
    recorded = {str(c.get("source", "")) for c in prog.get("claims", [])}
    return [i for i in items if f"contrib/{i['folder']}" not in recorded]


def render(items: list[dict], prog: dict, now: str) -> str:
    lines = ["---", "title: 外部投稿合并建议", f"updated: {now}",
             "generated_by: research-direction-selector/skills/research-gate/scripts/contrib_proposal.py", "---", "",
             f"> 本页由夜间任务自动生成（{now}），手改会被覆盖。是否并入由项目负责人决定；本页不改任何文件。", ""]
    if not items:
        return "\n".join(lines + ["目前没有待处理的外部投稿。", ""])
    lines += [f"共 {len(items)} 份待处理。并入方法：把下面的 JSON 加进项目 `program.json` 的 `claims`，"
              "核对证据后把 `status` 改成 `supported`，然后运行 `program.py check`。", ""]
    for item in items:
        problems = check(item, prog)
        claim = item.get("claim") or {}
        lines += [f"## contrib/{item['folder']}（{item['where']}）", "",
                  f"- 投稿人：{claim.get('author', '?')}；命题：{claim.get('proposition', '?')}；强度：{claim.get('strength', '?')}",
                  f"- 主张：{claim.get('statement', '?')}",
                  f"- 证伪条件：{claim.get('falsifier', '?')}",
                  f"- 检查：{'通过' if not problems else '；'.join(problems)}", ""]
        if not problems:
            lines += ["```json", json.dumps(proposal(item, prog), ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shared", type=Path, required=True)
    ap.add_argument("--program", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args(argv)
    prog = json.loads(a.program.read_text(encoding="utf-8-sig"))
    items = pending(collect(a.shared, fetch=a.fetch), prog)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render(items, prog, f"{_dt.datetime.now():%Y-%m-%d %H:%M}"), encoding="utf-8", newline="\n")
    print(f"CONTRIB PENDING {len(items)} -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
