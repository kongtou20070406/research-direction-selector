"""Attach the right skill to a turn without the user naming it (0 LLM tokens).

Each skill may keep a ``triggers.json`` beside its SKILL.md:

    {"keywords": [regex, ...], "paths": [regex on paths mentioned in the prompt or the cwd],
     "exclude": [regex], "must_read": ["SKILL.md", "references/x.md"], "then": "one extra instruction"}

``route(prompt, cwd)`` returns the matching skills. The hook adapters turn that into an instruction the
model sees before it starts: which skill applies, which files to read in full first (exempt from any
"read at most N uncertain files" budget), and the skill's ``then`` line.

    python skill_router.py claude   # Claude Code UserPromptSubmit hook (stdin JSON -> additionalContext)
    python skill_router.py agy      # Antigravity PreInvocation hook (first model call of a turn only)
    python skill_router.py test "把 method_notes.md 整理成摘要"   # print what would be injected

Skill roots: ``--roots`` or env SKILL_ROUTER_ROOTS (";"-separated). The agy adapter points the model at
its own copies under ~/.gemini/config/skills so the reads stay inside paths it may open.
"""

from __future__ import annotations

import json
import os
import re
import runpy
import sys
from functools import cache
from pathlib import Path

sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "skills"
AGY_ROOT = Path.home() / ".gemini" / "config" / "skills"


def load_triggers(roots: list[Path]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for root in roots:
        for f in sorted(root.glob("*/triggers.json")):
            name = f.parent.name
            if name in out:
                continue
            try:
                cfg = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            cfg["_dir"] = f.parent
            out[name] = cfg
    return out


def _hits(patterns: list[str], text: str) -> list[str]:
    hits = []
    for pat in patterns:
        try:
            m = re.search(pat, text, re.I)
        except re.error:
            continue
        if m:
            hits.append(m.group(0))
    return hits


AUTOMATED = re.compile(r"<task-notification>|SYSTEM NOTIFICATION|<side-sessions-event>|<ci-monitor-event>")


def route(prompt: str, triggers: dict[str, dict], cwd: str = "") -> list[dict]:
    text = prompt or ""
    if AUTOMATED.search(text):
        return []  # background-task and CI notices reach the hook too; they are not the user's request
    found = []
    for name, cfg in triggers.items():
        if _hits(cfg.get("exclude", []), text):
            continue
        kw = _hits(cfg.get("keywords", []), text)
        paths = _hits(cfg.get("paths", []), text + "\n" + cwd.replace("\\", "/"))
        if kw or paths:
            found.append({"skill": name, "hits": list(dict.fromkeys(kw + paths))[:4],
                          "must_read": cfg.get("must_read", ["SKILL.md"]), "then": cfg.get("then", ""),
                          "dir": cfg["_dir"]})
    found.sort(key=lambda r: -len(r["hits"]))
    return found


def message(matches: list[dict], read_root: Path | None = None) -> str:
    if not matches:
        return ""
    lines = ["[RDS Context Hint] Relevant workflow skill(s) for this turn (advisory):"]
    for m in matches:
        base = (read_root / m["skill"]) if read_root else m["dir"]
        files = "; ".join(str(base / rel).replace("\\", "/") for rel in m["must_read"])
        lines.append(f"- {m['skill']} (matched: {', '.join(m['hits'])}). Suggested reference: {files}. {m['then'] or ''}".rstrip())
    lines.append("Use what fits the current step; skip freely for quick questions.")
    return "\n".join(lines)


def roots_from_env() -> list[Path]:
    env = os.environ.get("SKILL_ROUTER_ROOTS", "")
    roots = [Path(p) for p in env.split(";") if p.strip()]
    return roots or [p for p in (SOURCE_ROOT, AGY_ROOT) if p.is_dir()]


@cache
def _brief_builder():
    if __package__:
        from .experience_brief import build

        return build
    return runpy.run_path(str(Path(__file__).with_name("experience_brief.py")))["build"]


def _turn_context(prompt: str, cwd: str, read_root: Path | None = None) -> str:
    try:
        skill_text = message(route(prompt, load_triggers(roots_from_env()), cwd), read_root)
    except Exception:
        skill_text = ""
    try:
        brief_text = _brief_builder()(prompt, cwd)
    except Exception:
        brief_text = ""
    return "\n\n".join(filter(None, (skill_text, brief_text)))


def claude_hook(payload: dict) -> dict:
    text = _turn_context(str(payload.get("prompt", "")), str(payload.get("cwd") or ""))
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}} if text else {}


def agy_hook(payload: dict) -> dict:
    if str(payload.get("invocationNum", "0")) not in ("0", ""):
        return {}  # once per user turn, on the first model call
    cwd = payload.get("cwd")
    paths = payload.get("workspacePaths")
    if not cwd and isinstance(paths, list) and paths:
        cwd = paths[0]
    try:
        read_root = AGY_ROOT if AGY_ROOT.is_dir() else None
    except OSError:
        read_root = None
    text = _turn_context(str(payload.get("lastUserInput", "")), str(cwd or ""), read_root)
    return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]} if text else {}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else "test"
    if mode == "test":
        print(message(route(" ".join(argv[1:]), load_triggers(roots_from_env()))) or "(no skill matched)")
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        out = claude_hook(payload) if mode == "claude" else agy_hook(payload)
    except Exception:  # a router failure must never block the agent
        out = {}
    if out:
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
