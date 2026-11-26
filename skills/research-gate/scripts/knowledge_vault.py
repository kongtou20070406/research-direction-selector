"""Render every project's knowledge registry into one Obsidian library (0 LLM tokens).

All registries live in one data folder, beside the pages they produce:

    <vault>/知识库/_数据/proj-alpha.jsonl   one file per project (ids K-...)
    <vault>/知识库/_数据/跨项目.jsonl       assets shared across projects (ids X-...)
    <vault>/知识库/_数据/项目.json          optional: each project's repository and library folder,
                                            {"proj-alpha": {"root": "/path/to/proj-alpha", "library": "proj-alpha成果库"}}

Knowledge registries are read from *.jsonl, excluding the overview refresh log. Entries are
grouped by project, taken from each entry's
``source`` up to the first "/" (so "proj-alpha/card048" belongs to proj-alpha).

Output, under --out (wholly generated; the data folder is left alone):

    00-知识库总览.md          counts per project and kind, newest entries, how to search and add
    按项目/<project>.md       one page per project: primitives, dead ends, assets, practices
    按类型/<kind>.md          one page per kind across all projects
    条目/<project>/<id>.md    one note per entry, frontmatter (项目, 类型, 状态...) and tags
                              #知识库/<kind> #项目/<project>, so Obsidian search and the tag pane filter by project

Evidence such as "card/044" becomes a file link into the project's repository when ``root`` is given.

    python knowledge_vault.py --data <vault>/知识库/_数据 --out <vault>/知识库
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("knowledge_mod", Path(__file__).with_name("knowledge.py"))
kn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kn)

KIND_LABEL = kn.KIND_LABEL
KIND_SHORT = {"primitive": "原语", "dead_end": "禁区", "asset": "跨项目资产", "practice": "外部实践"}
GENERATED = ("按项目", "按类型", "条目")
HEADER = "> 本页由 `knowledge_vault.py` 自动生成（{today}），手改会被覆盖。改内容请改登记文件或用 `knowledge.py add`。\n"


def project_of(entry: dict) -> str:
    return (str(entry.get("source") or "未注明").split("/")[0].strip() or "未注明")


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "_", name).strip() or "_"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def evidence_link(ev: str, root: Path | None) -> str:
    if re.match(r"https?://", ev):
        return f"[{ev}]({ev})"
    target = None
    m = re.fullmatch(r"card/(\d{3})", ev)
    if m and root:
        hits = sorted((root / "cards").glob(f"{m.group(1)}_*.md"))
        target = hits[0] if hits else None
    elif root and (root / ev).exists():
        target = root / ev
    elif Path(ev).is_absolute() and Path(ev).exists():
        target = Path(ev)
    if target is None:
        return f"`{ev}`"
    return f"[{ev}](<file:///{target.resolve().as_posix()}>)"


def note_link(e: dict, label: str | None = None) -> str:
    return f"[[条目/{safe(project_of(e))}/{safe(e['id'])}|{label or e['id']}]]"


PROJECTS_FILE = "项目.json"
IGNORED_JSONL = {"总览刷新记录.jsonl"}


def load_data(data: Path) -> tuple[list[dict], dict]:
    entries = []
    for p in sorted(data.glob("*.jsonl")):
        if p.name in IGNORED_JSONL:
            continue
        for n, entry in enumerate(kn.load(p), 1):
            if not isinstance(entry, dict):
                raise ValueError(f"{p}: entry {n} must be a JSON object")
            entries.append(dict(entry, _registry=str(p)))
    meta = data / PROJECTS_FILE
    return entries, (json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {})


def check_entries(entries: list[dict]) -> list[str]:
    """Validate one registry before using its entries to render Markdown."""
    problems, records = [], []
    for n, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            problems.append(f"entry {n}: expected a JSON object")
        elif not isinstance(entry.get("id", ""), str):
            problems.append(f"entry {n}: id must be a string")
        else:
            records.append(entry)
            for field in ("evidence", "tags"):
                values = entry.get(field, [])
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    problems.append(f"entry {n}: {field} must be a list of strings")
    return problems + kn.check(records)


def _table(entries: list[dict], with_project: bool) -> list[str]:
    cols = ["编号", "内容", "适用范围"] + (["项目"] if with_project else []) + ["日期"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for e in entries:
        row = f"| {note_link(e).replace('|', chr(92) + '|')} | {_cell(e['statement'])} | {_cell(e['scope'])} |"
        if with_project:
            row += f" [[按项目/{safe(project_of(e))}\\|{project_of(e)}]] |"
        lines.append(row + f" {e.get('date', '')} |")
    return lines


def render(entries: list[dict], projects: dict, today: str) -> dict[str, str]:
    """Return {relative path: markdown} for every generated page."""
    active = [e for e in entries if e.get("status") == "active"]
    by_project: dict[str, list[dict]] = {}
    for e in active:
        by_project.setdefault(project_of(e), []).append(e)
    pages: dict[str, str] = {}
    hdr = HEADER.format(today=today)

    for proj, es in sorted(by_project.items()):
        info = projects.get(proj, {})
        lines = [f"# {proj}", "", hdr]
        if info.get("library"):
            lines.append(f"- 项目资料库：[[{info['library']}/00-总览（先看这页）|{info['library']}]]")
        if info.get("root"):
            lines.append(f"- 仓库：[{info['root']}](<file:///{Path(info['root']).as_posix()}>)")
        lines.append("")
        for kind in kn.KINDS:
            group = [e for e in es if e["kind"] == kind]
            if group:
                lines += [f"## {KIND_LABEL[kind]}（{len(group)}）", ""] + _table(group, False) + [""]
        pages[f"按项目/{safe(proj)}.md"] = "\n".join(lines)

    for kind in kn.KINDS:
        group = sorted((e for e in active if e["kind"] == kind), key=lambda e: (project_of(e), e["id"]))
        lines = [f"# {KIND_LABEL[kind]}", "", hdr] + (_table(group, True) if group else ["暂无。"])
        pages[f"按类型/{KIND_SHORT[kind]}.md"] = "\n".join(lines) + "\n"

    for e in entries:
        proj = project_of(e)
        root = Path(projects[proj]["root"]) if projects.get(proj, {}).get("root") else None
        tags = [f"知识库/{KIND_SHORT[e['kind']]}", f"项目/{safe(proj).replace(' ', '_')}"] + \
               [safe(t).replace(" ", "_") for t in e.get("tags", [])]
        fm = ["---", f"编号: {e['id']}", f"项目: {proj}", f"类型: {KIND_SHORT[e['kind']]}", f"状态: {e['status']}",
              f"日期: {e.get('date', '')}", "tags:"] + [f"  - {t}" for t in tags] + ["---"]
        body = [f"# {e['id']}（{KIND_SHORT[e['kind']]}）", "", e["statement"], "",
                f"- 适用范围：{e['scope']}",
                f"- 证据：" + "；".join(evidence_link(ev, root) for ev in e["evidence"]),
                f"- 项目：[[按项目/{safe(proj)}|{proj}]]",
                f"- 登记文件：`{e['_registry']}`"]
        if e.get("superseded_by"):
            body.append(f"- 已被取代：{e['superseded_by']}")
        pages[f"条目/{safe(proj)}/{safe(e['id'])}.md"] = "\n".join(fm + [""] + body) + "\n"

    lines = ["# 知识库总览", "", hdr,
             "跨项目的经验库：每个项目做成了什么（原语）、哪些路走不通（禁区）、哪些东西值得搬到别的项目（跨项目资产），以及外部最佳实践。"
             "设计新实验前先在这里查；按项目看点下表的项目名，按类型看下面四个链接，也可以在标签面板里点 `#项目/…` 或 `#知识库/…`。", "",
             "| 项目 | 原语 | 禁区 | 跨项目资产 | 外部实践 | 资料库 |", "|---|---|---|---|---|---|"]
    for proj, es in sorted(by_project.items()):
        n = {k: sum(1 for e in es if e["kind"] == k) for k in kn.KINDS}
        lib = projects.get(proj, {}).get("library")
        lines.append(f"| [[按项目/{safe(proj)}\\|{proj}]] | {n['primitive']} | {n['dead_end']} | {n['asset']} | {n['practice']} | "
                     + (f"[[{lib}/00-总览（先看这页）\\|{lib}]]" if lib else "—") + " |")
    lines += ["", "按类型：" + " · ".join(f"[[按类型/{KIND_SHORT[k]}|{KIND_SHORT[k]}]]" for k in kn.KINDS), "",
              "## 最近新增", ""]
    for e in sorted(active, key=lambda e: (e.get("date", ""), e["id"]), reverse=True)[:10]:
        lines.append(f"- {e.get('date', '')} {note_link(e)}（{project_of(e)}，{KIND_SHORT[e['kind']]}）{_cell(e['statement'])[:80]}")
    lines += ["", "## 怎么查、怎么加", "",
              "- 查：`python <rds-repo>/skills/research-gate/scripts/knowledge.py search <登记文件…> -q \"关键词\"`（项目的开卡关口会自动查）",
              "- 加：项目结卡时自动入库；手动用 `knowledge.py add <登记文件> --kind … --statement … --scope … --evidence …`",
              "- 新项目接入：在 `_数据/` 放一个 `<项目名>.jsonl`；想要仓库和资料库链接，就在 `_数据/项目.json` 加一行"]
    pages["00-知识库总览.md"] = "\n".join(lines) + "\n"
    return pages


def write(pages: dict[str, str], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    written = changed = 0
    for rel, text in pages.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        old = p.read_text(encoding="utf-8") if p.is_file() else None
        if old != text:
            p.write_text(text, encoding="utf-8", newline="\n")
            changed += 1
        written += 1
    keep = {(out / rel).resolve() for rel in pages}
    removed = 0
    for sub in GENERATED:
        for p in sorted((out / sub).rglob("*.md")) if (out / sub).is_dir() else []:
            if p.resolve() not in keep:
                p.unlink()
                removed += 1
    return {"pages": written, "changed": changed, "removed": removed}


def vault_prefix(out: Path) -> str:
    """Path of ``out`` inside its Obsidian vault (the nearest parent with .obsidian), so links resolve from the root."""
    out = out.resolve()
    for parent in [out, *out.parents]:
        if (parent / ".obsidian").is_dir():
            rel = out.relative_to(parent).as_posix()
            return "" if rel == "." else rel + "/"
    return ""


def build(data: Path, out: Path, today: str | None = None) -> dict:
    try:
        entries, projects = load_data(data)
    except (OSError, ValueError, SystemExit) as error:
        return {"pages": 0, "changed": 0, "removed": 0, "problems": [str(error)]}

    registries: dict[str, list[dict]] = {}
    seen, duplicates = set(), set()
    for entry in entries:
        registries.setdefault(entry["_registry"], []).append(entry)
        eid = entry.get("id")
        if isinstance(eid, str):
            key = (project_of(entry), eid)
            if key in seen:
                duplicates.add(eid)
            seen.add(key)
    problems = [problem for records in registries.values() for problem in check_entries(records)]
    if duplicates:
        problems.append(f"same id twice within a project: {sorted(duplicates)}")
    if problems:
        return {"pages": 0, "changed": 0, "removed": 0, "problems": problems}

    pages = render(entries, projects, today or _dt.date.today().isoformat())
    prefix = vault_prefix(out)
    if prefix:
        for rel in pages:
            for sub in GENERATED:
                pages[rel] = pages[rel].replace(f"[[{sub}/", f"[[{prefix}{sub}/")
    res = write(pages, out)
    res["entries"] = len(entries)
    res["problems"] = []
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    res = build(a.data, a.out)
    print("KNOWLEDGE_VAULT " + json.dumps(res, ensure_ascii=False))
    return 1 if res["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
