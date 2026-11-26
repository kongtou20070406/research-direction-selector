"""Render a research program as an Obsidian board (0 LLM tokens).

The board shows the split tree (north star -> rungs -> children, edges labelled by the route
that made the split) as a Mermaid diagram, the review agenda (what to split, solve, compose
or retire next), the routes and the ladder. It is regenerated from ``program.json``; never
edit the board by hand.

    python program_board.py PROGRAM.json --out "<vault>/00-研究树看板.md" [--today YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

STATUS_ZH = {"open": "未做", "partial": "部分", "supported": "成立", "refuted": "被推翻", "retired": "退役"}
AGENDA_ZH = [
    ("ready_leaves", "可以开工的叶子", "写卡片或 GPT 契约，直接做"),
    ("ready_to_compose", "可以往回合的节点", "子命题都成立了，写合成论证或实验"),
    ("too_big", "太大、需要再拆", "开一条拆分路线（route-new）"),
    ("resplit", "需要换一种拆法", "当前拆法有子命题被推翻"),
    ("unattacked_rungs", "没人在攻的命题", "决定现在是否开路线"),
    ("stalled", "停滞的路线", "超过两周没有进展"),
    ("over_budget", "超预算的路线", "停下或申请追加"),
    ("past_deadline", "过期的路线", "收口或退役"),
    ("retire_candidates", "建议退役的路线", "它产出的主张都被推翻了"),
    ("paper_candidates", "可以切论文的命题", "有成立的主张还没有论文认领"),
    ("reanchor", "需要重新锚定", "北极星改过，逐条复核"),
]


def _program():
    spec = importlib.util.spec_from_file_location("program", Path(__file__).resolve().parent / "program.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nid(pid: str) -> str:
    return "n_" + re.sub(r"[^0-9A-Za-z_]", "_", pid)


def _label(text: str, limit: int = 28) -> str:
    text = text.replace('"', "'").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def mermaid_tree(prog: dict) -> str:
    props = prog.get("propositions", [])
    routes = {r["id"]: r for r in prog.get("routes", [])}
    star = prog["north_star"]["current"]
    lines = ["```mermaid", "flowchart TD", f'  star(["北极星 {star}"])']
    for p in props:
        tag = " ✓可解" if p.get("tractable") else ""
        lines.append(f'  {_nid(p["id"])}["{p["id"]}<br/>{_label(p.get("statement", ""))}<br/>{STATUS_ZH.get(p.get("status"), p.get("status"))}{tag}"]')
    for p in props:
        parent = p.get("parent")
        if not parent:
            lines.append(f"  star --> {_nid(p['id'])}")
            continue
        route = routes.get(p.get("route") or "", {})
        arrow = "-.->" if route.get("status") in ("retired", "merged") else "-->"
        lines.append(f"  {_nid(parent)} {arrow}|{p.get('route', '')}| {_nid(p['id'])}")
    for status in ("open", "partial", "supported", "refuted", "retired"):
        ids = [_nid(p["id"]) for p in props if p.get("status") == status]
        if ids:
            lines.append(f"  class {','.join(ids)} st_{status}")
    lines += [
        "  classDef st_open fill:#eeeeee,stroke:#888",
        "  classDef st_partial fill:#fff3c4,stroke:#c9a227",
        "  classDef st_supported fill:#d4f4dd,stroke:#2e8b57",
        "  classDef st_refuted fill:#f8d0d0,stroke:#b22222",
        "  classDef st_retired fill:#dddddd,stroke:#999,stroke-dasharray: 3 3",
        "```",
    ]
    return "\n".join(lines)


def _table_cell(text: str) -> str:
    return (text or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def table_tree(prog: dict) -> list[str]:
    ns = prog.get("north_star") or {}
    cur_id = ns.get("current", "")
    ns_stmt = ns.get("statement", "")
    for ver in ns.get("versions", []):
        if ver.get("id") == cur_id:
            ns_stmt = ver.get("statement", ns_stmt)
            break

    props = prog.get("propositions", [])
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for p in props:
        parent = p.get("parent")
        if not parent:
            roots.append(p)
        else:
            children.setdefault(parent, []).append(p)

    lines: list[str] = []
    if ns_stmt:
        lines.append(f"**北极星核心问题（`{cur_id}`）**：{_table_cell(ns_stmt)}")
        lines.append("")
    lines += [
        "| 节点（层级） | 拆法路线 | 完整问题描述 | 状态 | 可解 |",
        "|---|---|---|---|---|",
    ]

    def _walk(node: dict, depth: int, is_last: bool) -> None:
        pid = node["id"]
        if depth == 0:
            node_col = f"**`{pid}`**"
        else:
            branch = "└─ " if is_last else "├─ "
            node_col = f"{'　' * (depth - 1)}{branch}`{pid}`"
        route_col = f"`{node['route']}`" if node.get("route") else "—"
        stmt_col = _table_cell(node.get("statement", ""))
        status_col = STATUS_ZH.get(node.get("status"), node.get("status", ""))
        tract_col = "✓可解" if node.get("tractable") else "需再拆"
        lines.append(f"| {node_col} | {route_col} | {stmt_col} | {status_col} | {tract_col} |")
        kids = children.get(pid, [])
        for idx, ch in enumerate(kids):
            _walk(ch, depth + 1, idx == len(kids) - 1)

    for idx, r in enumerate(roots):
        _walk(r, 0, idx == len(roots) - 1)
    return lines


def focus_set(prog: dict) -> set[str] | None:
    """Ids under the current focus (the focus nodes and all their descendants); None without a focus."""
    active = (prog.get("focus") or {}).get("active")
    if not active:
        return None
    children: dict[str, list[str]] = {}
    for p in prog.get("propositions", []):
        if p.get("parent"):
            children.setdefault(p["parent"], []).append(p["id"])
    seen, stack = set(), list(active)
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack += children.get(node, [])
    return seen


def _touches_focus(item: str, props: dict, routes: dict, in_focus: set[str]) -> bool:
    if item in props:
        return item in in_focus
    if item in routes:
        return any(t in in_focus for t in routes[item].get("targets", []))
    return True


def paper_gaps(prog: dict) -> list[str]:
    """For each active paper: primary claims that are not yet supported, i.e. what the paper still needs."""
    claims = {c["id"]: c for c in prog.get("claims", [])}
    lines = ["## 论文还缺的证据", ""]
    active = [p for p in prog.get("papers", []) if p.get("status") not in ("merged", "abandoned")]
    for paper in active:
        missing = [c for c in paper.get("primary_claims", []) if claims.get(c, {}).get("status") != "supported"]
        if missing:
            lines.append(f"- **{paper['id']}**（{paper.get('status', '')}）：" + "；".join(
                f"`{c}` {_label(claims.get(c, {}).get('statement', ''), 50)}（{claims.get(c, {}).get('status', '未登记')}）" for c in missing))
        else:
            lines.append(f"- **{paper['id']}**（{paper.get('status', '')}）：主贡献主张都已成立")
    if not active:
        lines.append("- 没有在写的论文")
    return lines


def _knowledge():
    path = Path(__file__).with_name("knowledge.py")
    spec = importlib.util.spec_from_file_location("program_board_knowledge", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_kb(knowledge_dir: Path | None) -> tuple[list[dict], list[dict]]:
    if knowledge_dir is None or not knowledge_dir.is_dir():
        return [], []
    kn = _knowledge()
    all_entries, cross_entries = [], []
    for f in sorted(knowledge_dir.glob("*.jsonl")):
        if f.name == "总览刷新记录.jsonl":
            continue
        try:
            rows = [e for e in kn.load(f) if e.get("status") == "active"]
        except (OSError, ValueError, SystemExit):
            continue
        all_entries.extend(rows)
        if f.stem == "跨项目" or any(e.get("kind") == "asset" for e in rows):
            cross_entries.extend(rows)
    return all_entries, cross_entries


def knowledge_radar(prog: dict, agenda: dict, kb_all: list[dict], kb_cross: list[dict]) -> list[str]:
    if not kb_all:
        return []
    kn = _knowledge()
    props = {p["id"]: p for p in prog.get("propositions", [])}
    claims = {c["id"]: c for c in prog.get("claims", [])}
    prims = [e for e in kb_all if e.get("kind") == "primitive"]
    dead_ends = [e for e in kb_all if e.get("kind") == "dead_end"]
    transfers = [e for e in (kb_cross or kb_all) if e.get("kind") in ("asset", "primitive")]
    lines = ["## 北极星产出雷达（知识库联动）", ""]
    lines.append(f"- 知识库弹药：原语 {len(prims)} 条 · 跨项目资产 {sum(e.get('kind') == 'asset' for e in kb_all)} 条 · 禁区 {len(dead_ends)} 条")

    ready = sorted(set((agenda.get("ready_to_compose") or []) + (agenda.get("paper_candidates") or [])))
    for pid in ready[:4]:
        p = props.get(pid, {})
        q = " ".join([p.get("statement", ""), *(claims.get(c, {}).get("statement", "") for c in p.get("claims", []))])
        hits = [f"`{e['id']}` {_label(e['statement'], 36)}" for _, e in kn.search(prims, q, limit=2)]
        lines.append(f"- 可产出节点 `{pid}`：" + ("；".join(hits) if hits else "已有成立主张，可直接往回合或切论文"))

    in_focus = focus_set(prog)
    open_props = [p for p in prog.get("propositions", [])
                  if p.get("status") in ("open", "partial") and (in_focus is None or p["id"] in in_focus)]
    for p in open_props[:3]:
        q = p.get("statement", "")
        asset_hits = [f"`{e['id']}` {_label(e['statement'], 32)}（适用：{e.get('scope', '—')}）"
                      for _, e in kn.search(transfers, q, limit=2)]
        dead_hits = [f"`{e['id']}` {_label(e['statement'], 32)}" for _, e in kn.search(dead_ends, q, limit=1)]
        if asset_hits or dead_hits:
            parts = []
            if asset_hits:
                parts.append("可借资产 " + "；".join(asset_hits))
            if dead_hits:
                parts.append("避坑 " + "；".join(dead_hits))
            lines.append(f"- `{p['id']}` 知识回流：" + " ｜ ".join(parts))
    return lines


def render_board(prog: dict, agenda: dict, today: str,
                 kb_entries: tuple[list[dict], list[dict]] | None = None) -> str:
    props = {p["id"]: p for p in prog.get("propositions", [])}
    out = [
        "---", f"updated: {today}", "source: program.json（自动生成，勿手改）", "generated_by: research-direction-selector/skills/research-gate/scripts/program_board.py", "---", "",
        f"# 研究树看板：{prog.get('program', '')}", "",
        "> 大问题拆成小问题，小的再拆，直到一张卡、一个证明或一个封闭工程任务就能解决；解决后逐层合回去。",
        "> 一条路线就是对某个节点的一种拆法，失败了就换一条。", "",
        "## 下一步（自动巡检）", "",
    ]
    focus = prog.get("focus") or {}
    in_focus = focus_set(prog)
    routes = {r["id"]: r for r in prog.get("routes", [])}
    if focus:
        out[-2:-2] = [f"> [!note] 当前焦点（{focus.get('since', '')} 起）：{focus.get('note', '')}", ""]
    if agenda.get("north_star_review_due"):
        out += ["> [!warning] 北极星该复审了：距上次版本或复审已超过设定间隔。", ""]
    any_item = False
    parked: list[str] = []
    for key, title, hint in AGENDA_ZH:
        ids = agenda.get(key) or []
        if in_focus is not None and key != "reanchor":
            keep = [i for i in ids if _touches_focus(i, props, routes, in_focus)]
            parked += [i for i in ids if i not in keep and i not in parked]
            ids = keep
        if not ids:
            continue
        any_item = True
        shown = [i if key == "reanchor" else f"`{i}`" + (f" {_label(props[i]['statement'], 40)}" if i in props else "") for i in ids]
        out.append(f"- **{title}**（{hint}）：" + "；".join(shown))
    if not any_item:
        out.append("- 焦点内暂无待办")
    if parked:
        out.append("- 搁置中（不排进待办）：" + "、".join(f"`{i}`" for i in sorted(parked)))
    out += ["", *paper_gaps(prog)]
    if kb_entries and kb_entries[0]:
        out += ["", *knowledge_radar(prog, agenda, kb_entries[0], kb_entries[1])]
    out += ["", "## 拆分树", "", "### 表格视图（完整问题与层级）", "", *table_tree(prog), "",
            "### 流程图（Mermaid）", "", mermaid_tree(prog), "",
            "实线是进行中或暂停的拆法，虚线是已退役或已合并的拆法；“✓可解”表示该节点已拆到可以直接解决。", "",
            "## 路线", "", "| 路线 | 状态 | 攻的节点 | 证伪线 | 预算/截止 | 卡片 |", "|---|---|---|---|---|---|"]
    for r in prog.get("routes", []):
        kill = r.get("kill", {})
        budget = " / ".join(x for x in (f"{kill['budget_gpu_hours']} GPU·h" if "budget_gpu_hours" in kill else "", kill.get("deadline", "")) if x)
        out.append(f"| `{r['id']}` | {r.get('status', '')} | {', '.join(r.get('targets', []))} | {_label(kill.get('falsifier', ''), 40)} | {budget} | {', '.join(r.get('cards', []))} |")
    out += ["", "## 命题与证据", "", "| 命题 | 状态 | 上级 | 成立的主张 |", "|---|---|---|---|"]
    claims = {c["id"]: c for c in prog.get("claims", [])}
    for p in props.values():
        ok = [c for c in p.get("claims", []) if claims.get(c, {}).get("status") == "supported"]
        out.append(f"| `{p['id']}` | {STATUS_ZH.get(p.get('status'), p.get('status'))} | {p.get('parent', '—')} | {', '.join(ok) or '—'} |")
    out += ["", "## 论文", "", "每篇论文一位负责人；其他人除非主动提出，不参与写作。", "", "| 论文 | 负责人 | 状态 | 主贡献主张 |", "|---|---|---|---|"]
    for paper in prog.get("papers", []):
        out.append(f"| {paper['id']} | {paper.get('owner', '未定')} | {paper.get('status', '')} | {', '.join(paper.get('primary_claims', [])) or '—'} |")
    return "\n".join(out) + "\n"


def build(program_path: Path, today: str | None = None, knowledge_dir: Path | None = None) -> str:
    mod = _program()
    prog = mod.load(program_path)
    today = today or _dt.date.today().isoformat()
    kb_entries = _load_kb(knowledge_dir) if knowledge_dir is not None else None
    return render_board(prog, mod.review(prog, today=today), today, kb_entries=kb_entries)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--today")
    ap.add_argument("--knowledge", type=Path, default=None, help="Obsidian 知识库/_数据 directory for North Star <-> KB radar")
    args = ap.parse_args(argv)
    text = build(args.program, args.today, knowledge_dir=args.knowledge)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"BOARD_OK chars={len(text)} output={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
