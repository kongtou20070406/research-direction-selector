"""Build a project-scoped, deterministic brief for the current user turn."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import runpy
from functools import cache
from pathlib import Path

MAX_CHARS = 1200
TITLE = "[Project Context Brief]"
REMINDER = "Use this project context to build on prior evidence; if a better path calls for revising an earlier constraint, explain the tradeoff candidly."
KNOWLEDGE_SCRIPT = Path(__file__).resolve().parents[1] / "skills/research-gate/scripts/knowledge.py"
TYPES = {"dead_end": "禁区", "primitive": "原语", "asset": "跨项目资产", "practice": "最佳实践"}
MODES = {
    "probe": "灵感探针（probe）｜直奔核心直觉，用极简原生 PyTorch 快速验证关键假说",
    "paper": "专题成文（paper）｜把一个清晰发现打磨成自包含、可读可复现的高质量论文",
    "program": "长期科学探索（program）｜锚定开放性核心难题，以简洁机制、扎实理论与高信息量实验步步推进",
}
FILLER = frozenset(
    "的 是 了 我 你 他 我们 你们 自己 这个 那个 一些 如何 怎么 可以 一下 问题 工作 提升 做吧 看看 希望 还有 需要".split()
)


@cache
def _knowledge():
    spec = importlib.util.spec_from_file_location("brief_knowledge", KNOWLEDGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def _automated() -> re.Pattern[str]:
    if __package__:
        from .skill_router import AUTOMATED

        return AUTOMATED
    return runpy.run_path(str(Path(__file__).with_name("skill_router.py")))["AUTOMATED"]


@cache
def _sessions_brief():
    if __package__:
        from .sessions import brief_lines

        return brief_lines
    return runpy.run_path(str(Path(__file__).with_name("sessions.py")))["brief_lines"]


def _session_rows(root: Path) -> list[str]:
    try:
        return _sessions_brief()(root)
    except Exception:
        return []


def find_config(cwd: str | Path | None) -> Path | None:
    if not cwd:
        return None
    directory = Path(cwd).resolve()
    for parent in (directory, *directory.parents):
        path = parent / ".agents" / "brief.json"
        if path.is_file():
            return path
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def _load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(cfg, dict):
        raise ValueError("expected a JSON object")
    return cfg


def _status_block(text: str, start: str, end: str) -> str | None:
    _, found_start, remainder = text.partition(start)
    block, found_end, _ = remainder.partition(end)
    return block if found_start and found_end else None


def _status(cfg: dict, root: Path) -> str:
    if not all(cfg.get(key) for key in ("file", "start", "end")):
        return ""
    block = _status_block(_read(root / cfg["file"]), cfg["start"], cfg["end"])
    if block is None:
        return ""
    return "\n".join(line for line in block.splitlines()[1:] if line.startswith("- "))


def _knowledge_rows(paths: list[str], root: Path, prompt: str) -> list[str]:
    knowledge = _knowledge()
    entries = []
    for path in paths:
        try:
            entries.extend(knowledge.load(root / path))
        except (OSError, UnicodeError, SystemExit):
            continue  # The registry CLI raises SystemExit on malformed JSONL.
    # A whole Chinese run can reintroduce a discarded bigram when search tokenizes it.
    filtered = " ".join(token for token in sorted(knowledge._tokens(prompt))
                        if not (len(token) == 1 and token.isdigit())
                        and knowledge._tokens(token).isdisjoint(FILLER))
    hits = knowledge.search(entries, filtered, limit=6)
    hits.sort(key=lambda hit: hit[1]["kind"] != "dead_end")
    return [f"- {entry['id']}［{TYPES[entry['kind']]}］{entry['statement']}（{entry['scope']}）"
            for _, entry in hits]


def _dated_decisions(text: str):
    date_col, decision_col = 0, 1
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if "日期" in cells:
            date_col = cells.index("日期")
            # the meaning column says what was decided; the quote column alone is often just the user's prompt
            decision_col = next((cells.index(label) for label in ("含义", "决定", "研究者原话") if label in cells), 1)
            continue
        if len(cells) <= max(date_col, decision_col):
            continue
        date, decision = cells[date_col], cells[decision_col]
        if not re.match(r"\d{4}-\d{2}-\d{2}\b", date) or not decision:
            continue
        try:
            dt.date.fromisoformat(date[:10])
        except ValueError:
            continue
        yield date, decision.replace(r"\|", "|"), " ".join(cell for i, cell in enumerate(cells) if i != date_col)


def _decision_rows(cfg: dict, root: Path, query: set[str]) -> list[str]:
    if not cfg.get("file"):
        return []
    tokenize = _knowledge()._tokens
    rows = [(date, decision, len(query & tokenize(text)))
            for date, decision, text in _dated_decisions(_read(root / cfg["file"]))]
    rows.reverse()  # The table is append-only. Later rows win ties on the same date.
    rows.sort(key=lambda row: row[0][:10], reverse=True)
    unique = {}
    for date, decision, score in rows:
        key = (date, decision)
        unique[key] = max(unique.get(key, 0), score)
    recent = list(unique)[:max(0, cfg.get("recent", 0))]
    related = sorted((key for key, score in unique.items() if score and key not in recent),
                     key=unique.__getitem__, reverse=True)[:max(0, cfg.get("match", 0))]
    return [f"- {date[:160]}｜{decision[:160]}" for date, decision in recent + related]


def _feedback_rows(directory: Path, query: set[str]) -> list[str]:
    tokenize = _knowledge()._tokens
    hits = []
    for path in sorted(directory.glob("*.md")):
        text = _read(path)
        front = re.match(r"---[^\S\n]*\n(.*?)^---[^\S\n]*(?:\n|$)", text, re.M | re.S)
        if not front:
            continue
        fields = dict(re.findall(r"^ *([\w-]+):[^\S\n]*([^\n]+)", front[1], re.M))
        fields = {key: value.strip().strip("\"'") for key, value in fields.items()}
        if fields.get("type") != "feedback":
            continue
        name, description = fields.get("name"), fields.get("description")
        if name and description:
            score = len(query & tokenize(f"{description}\n{text[front.end():]}"))
            if score:
                hits.append((score, f"- {name}｜{description}"))
    hits.sort(key=lambda hit: -hit[0])
    return [line for _, line in hits[:3]]


def _fit(
    checklist: str,
    status: str,
    knowledge: list[str],
    decisions: list[str],
    feedback: list[str],
    sessions: list[str] | tuple[str, ...] = (),
) -> str:
    conflict_lines = [ln for ln in sessions if "⚠ CONFLICT:" in ln]
    normal_sessions = [ln for ln in sessions if "⚠ CONFLICT:" not in ln]

    def render() -> str:
        sections = [TITLE, checklist]
        session_lines = [*conflict_lines, *normal_sessions]
        for label, lines in (
            ("STATUS 自动区", status.splitlines()),
            ("活跃并行会话（勿撞文件/GPU/Git）", session_lines),
            ("知识库（与本轮相关的禁区不要重复）", knowledge),
            ("研究者的决定", decisions),
            ("过往反馈", feedback),
        ):
            if lines:
                sections.append("\n".join([label, *lines]))
        return "\n\n".join(filter(None, [*sections, REMINDER]))

    result = render()
    for lines in (decisions, feedback, knowledge, normal_sessions):
        while len(result) > MAX_CHARS and lines:
            lines.pop()
            result = render()
    if len(result) > MAX_CHARS:
        # A project can supply more fixed text than the entire budget; always preserve active conflict alerts.
        conflict_section = "\n".join(["活跃并行会话（勿撞文件/GPU/Git）", *conflict_lines]) if conflict_lines else ""
        status_section = f"STATUS 自动区\n{status}" if status else ""
        room = MAX_CHARS - len(TITLE) - len(REMINDER) - len(conflict_section) - 2 * (1 + bool(checklist) + bool(status) + bool(conflict_section))
        room = max(0, room)
        checklist = checklist[:room - min(len(status_section), room // 2)].rstrip()
        status_section = status_section[:max(0, room - len(checklist))].rstrip()
        result = "\n\n".join(filter(None, [TITLE, checklist, status_section, conflict_section, REMINDER]))
    return result


def _focus(cfg: dict, root: Path) -> str:
    focus = cfg.get("focus")
    if isinstance(focus, str):
        return focus.strip()
    if isinstance(focus, dict):
        prog = focus.get("program")
        if isinstance(prog, str) and prog.strip():
            prog_path = Path(prog) if Path(prog).is_absolute() else root / prog
            try:
                data = json.loads(prog_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return ""
            prog_focus = data.get("focus")
            if isinstance(prog_focus, str):
                return prog_focus.strip()
            if isinstance(prog_focus, dict):
                note = prog_focus.get("note")
                if isinstance(note, str):
                    return note.strip()
    return ""


def build(prompt: str, cwd: str | Path | None) -> str:
    if _automated().search(prompt):
        return ""
    path = find_config(cwd)
    if path is None:
        return ""
    try:
        cfg = _load_config(path)
    except (OSError, ValueError):
        return ""
    root = path.parent.parent
    query = _knowledge()._tokens(prompt)
    memory_dir = cfg.get("memory_dir")
    raw_checklist = cfg.get("checklist", "")
    if isinstance(raw_checklist, list):
        checklist = "\n".join(str(item).strip() for item in raw_checklist if str(item).strip())
    elif isinstance(raw_checklist, str):
        checklist = raw_checklist.strip()
    else:
        checklist = ""
    header_lines: list[str] = []
    mode = cfg.get("mode")
    if isinstance(mode, str) and mode in MODES:
        header_lines.append(f"项目模式：{MODES[mode]}")
    focus_text = _focus(cfg, root)
    if focus_text:
        header_lines.append(f"当前焦点：{focus_text}")
    if header_lines:
        header = "\n".join(header_lines)
        checklist = f"{header}\n{checklist}".strip()
    return _fit(
        checklist,
        _status(cfg.get("status", {}), root),
        _knowledge_rows(cfg.get("knowledge", []), root, prompt),
        _decision_rows(cfg.get("decisions", {}), root, query),
        _feedback_rows(root / memory_dir, query) if memory_dir else [],
        _session_rows(root),
    )


def check_config(cwd: str | Path | None) -> tuple[bool, list[str]]:
    """Validate the inputs configured for a project's brief."""
    path = find_config(cwd if cwd is not None else Path.cwd())
    if path is None:
        return False, [f".agents/brief.json not found from {cwd or Path.cwd()}"]
    try:
        cfg = _load_config(path)
    except (OSError, ValueError) as error:
        return False, [f"{path}: {error}"]

    root = path.parent.parent
    issues = []
    if "checklist" in cfg:
        cl = cfg["checklist"]
        if not (isinstance(cl, str) or (isinstance(cl, list) and all(isinstance(x, str) for x in cl))):
            issues.append("checklist: expected string or list of strings")
    if "mode" in cfg:
        mode = cfg["mode"]
        if not isinstance(mode, str) or mode not in MODES:
            issues.append(f"mode: expected one of {', '.join(MODES)}")
    if "focus" in cfg:
        focus = cfg["focus"]
        if isinstance(focus, str):
            if not focus.strip():
                issues.append("focus: empty string")
        elif isinstance(focus, dict) and isinstance(focus.get("program"), str) and focus["program"].strip():
            prog_path = Path(focus["program"]) if Path(focus["program"]).is_absolute() else root / focus["program"]
            if not prog_path.is_file():
                issues.append(f"focus.program not found: {focus['program']}")
            else:
                try:
                    json.loads(prog_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    issues.append(f"focus.program invalid JSON ({focus['program']}): {exc}")
        else:
            issues.append("focus: expected non-empty string or {'program': '<path>'}")
    for section in ("status", "decisions"):
        settings = cfg.get(section, {})
        if not isinstance(settings, dict):
            issues.append(f"{section}: expected a JSON object")
            continue
        if "file" not in settings:
            continue
        filename = settings["file"]
        if not isinstance(filename, str) or not filename.strip():
            issues.append(f"{section}.file: expected a path")
            continue
        source = root / filename
        try:
            text = source.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            issues.append(f"{section}.file {source}: {error}")
            continue
        if section == "status":
            start, end = settings.get("start"), settings.get("end")
            if not all(isinstance(marker, str) and marker for marker in (start, end)):
                issues.append("status.start and status.end must be non-empty markers")
            elif _status_block(text, start, end) is None:
                issues.append(f"{source}: missing STATUS markers in start/end order")
        elif next(_dated_decisions(text), None) is None:
            issues.append(f"{source}: no valid dated decision table row")

    paths = cfg.get("knowledge", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() for p in paths):
        issues.append("knowledge: expected a list of paths")
    else:
        knowledge = _knowledge()
        for filename in paths:
            source = root / filename
            if not source.is_file():
                issues.append(f"knowledge file not found: {source}")
                continue
            try:
                issues.extend(f"{source}: {issue}" for issue in knowledge.check(knowledge.load(source)))
            except (OSError, UnicodeError, SystemExit) as error:
                issues.append(str(error))

    if "memory_dir" in cfg:
        directory = cfg["memory_dir"]
        if not isinstance(directory, str) or not directory.strip() or not (root / directory).is_dir():
            issues.append(f"memory_dir is not a directory: {directory}")
    return not issues, issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True, help="validate the project's brief inputs")
    parser.add_argument("cwd", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    ok, issues = check_config(args.cwd)
    if ok:
        print(f"BRIEF CHECK OK ({find_config(args.cwd)})")
    else:
        print("BRIEF CHECK FAIL")
        for issue in issues:
            print(f"  - {issue}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
