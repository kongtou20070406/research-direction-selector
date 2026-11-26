"""First run (and every pull) for a collaborator: verify the shared mirror, then show where the
project stands and what can be picked up. Standard library only; reads program.json, CHANGES.md
and the tools next to this file."""

from __future__ import annotations

import datetime as _dt
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def latest_changes(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines, seen = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            seen += 1
            if seen > 1:
                break
        if seen == 1:
            lines.append(line)
    return lines


def main() -> int:
    share, program, board = _load("share"), _load("program"), _load("program_board")
    problems = share.check_shared(ROOT)
    errors, _ = program.check(program.load(ROOT / "program.json"))
    if problems or errors:
        print("共享库不完整或程序文件有错，先 git pull 或联系负责人：")
        for p in problems + errors:
            print(f"  - {p}")
        return 1

    prog = program.load(ROOT / "program.json")
    star = prog["north_star"]
    current = next(v for v in star["versions"] if v["id"] == star["current"])
    print(f"项目：{prog.get('program')}")
    print(f"北极星 {star['current']}：{current.get('statement', '')}")
    focus = prog.get("focus") or {}
    if focus:
        print(f"当前焦点（{focus.get('since', '')} 起）：{focus.get('note', '')}")

    print("\n最近一次同步：")
    for line in latest_changes(ROOT / "CHANGES.md") or ["（还没有变化记录）"]:
        print(f"  {line}")

    agenda = program.review(prog, today=_dt.date.today().isoformat())
    in_focus = board.focus_set(prog)
    props = {p["id"]: p for p in prog.get("propositions", [])}
    pick = [i for i in agenda.get("ready_leaves", []) if in_focus is None or i in in_focus]
    split = [i for i in agenda.get("too_big", []) if in_focus is None or i in in_focus]
    print("\n可以认领的小任务（开分支 leaf/<编号>-<名字>）：")
    for i in pick or []:
        print(f"  {i}  {props[i]['statement']}")
    if not pick:
        print("  暂时没有已拆到可解的叶子。")
    if split:
        print("需要先拆开的节点（拆法也可以提 PR 讨论）：" + "、".join(split))

    print("\n论文（每篇一位负责人；想参与写作请主动向负责人提出）：")
    claims = {c["id"]: c for c in prog.get("claims", [])}
    for paper in prog.get("papers", []):
        if paper.get("status") in ("merged", "abandoned"):
            continue
        missing = [c for c in paper.get("primary_claims", []) if claims.get(c, {}).get("status") != "supported"]
        need = "、".join(missing) if missing else "主贡献主张都已成立"
        print(f"  {paper['id']}（{paper.get('status')}，负责人 {paper.get('owner', '未定')}）还缺：{need}")
    print("\n完整拆分树见 BOARD.md；每次 git pull 后再运行一次本脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
