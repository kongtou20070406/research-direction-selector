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

import hashlib
import json
import os
import re
import runpy
import sys
import tempfile
import time
import uuid
from functools import cache
from pathlib import Path

sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "skills"
AGY_ROOT = Path.home() / ".gemini" / "config" / "skills"
BRIEF_COOLDOWN_TURNS = 10
BRIEF_COOLDOWN_SECS = 1800.0
WAKEUP_DEDUP_SECS = 600.0
SKILL_COOLDOWN_TURNS = 4
_KB_LINE_RE = re.compile(r"^- [A-Z]+-\d+\b")
def _is_dynamic_header(line: str) -> bool:
    return line.startswith(("知识库（", "研究者的决定", "过往反馈")) or line.endswith("的决定")


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
        lines.append(f"- {m['skill']} — Suggested reference: {files}. {m['then'] or ''} (matched: {', '.join(m['hits'])})".rstrip())
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


def _state_path() -> Path:
    override = os.environ.get("SKILL_ROUTER_STATE", "").strip()
    return Path(override) if override else (Path(tempfile.gettempdir()) / "rds_skill_router_state.json")


def _session_id(payload: dict) -> str:
    for key in ("conversationId", "conversation_id", "session_id", "sessionId", "transcriptPath", "transcript_path"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _safe_float(val: object, default: float = 0.0) -> float:
    try:
        return default if val is None else float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _load_state(path: Path, now: float) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: v
        for k, v in data.items()
        if isinstance(v, dict) and now - _safe_float(v.get("last_seen_at"), 0.0) < 86400.0
    }


def _save_state(path: Path, state: dict[str, dict], *, session_id: str = "", now: float | None = None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        deadline = time.monotonic() + 1.0
        acquired = False
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 15.0:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        try:
            merged = _load_state(path, time.time() if now is None else now) if session_id else {}
            if session_id and session_id in state:
                merged[session_id] = state[session_id]
            else:
                merged = state
            tmp = path.parent / f".router.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
            tmp.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            for attempt in range(4):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 3:
                        break
                    time.sleep(0.015)
            if tmp.exists():
                tmp.unlink()
        finally:
            if acquired:
                lock.unlink(missing_ok=True)
    except OSError:
        pass


def _extract_dynamic_lines(brief_text: str) -> tuple[str, list[str], list[str]]:
    static_blocks: list[str] = []
    kb_lines: list[str] = []
    conflict_lines: list[str] = []
    for block in brief_text.split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        if _is_dynamic_header(lines[0]):
            for ln in lines[1:]:
                if ln.startswith("- "):
                    kb_lines.append(ln)
            continue
        kept = []
        for ln in lines:
            if "⚠ CONFLICT:" in ln:
                conflict_lines.append(ln)
            else:
                kept.append(re.sub(r"\|\s*(?:just now|\d+[mh] ago)\s*\|", "| <age> |", ln))
        if kept:
            static_blocks.append("\n".join(kept))
    static_sha = hashlib.sha256("\n\n".join(static_blocks).encode("utf-8")).hexdigest()[:16]
    return static_sha, kb_lines, conflict_lines


def _turn_context_for_session(
    prompt: str,
    cwd: str,
    read_root: Path | None = None,
    session_id: str = "",
    *,
    now: float | None = None,
    delta_header: str = "[Context Delta]",
) -> str:
    if not session_id:
        return _turn_context(prompt, cwd, read_root)
    if AUTOMATED.search(prompt or ""):
        return ""

    cur_now = time.time() if now is None else now
    state_file = _state_path()
    state = _load_state(state_file, cur_now)
    rec = dict(state.get(session_id) or {})

    prompt_sha = hashlib.sha256((prompt or "").strip().encode("utf-8")).hexdigest()[:16]
    if prompt_sha == rec.get("last_prompt_sha") and (cur_now - _safe_float(rec.get("last_seen_at"), 0.0)) < WAKEUP_DEDUP_SECS:
        try:
            dedup_brief = _brief_builder()(prompt, cwd)
        except Exception:
            dedup_brief = ""
        if dedup_brief:
            _, _, dedup_conflicts = _extract_dynamic_lines(dedup_brief)
            seen_conflicts = list(rec.get("seen_conflicts") or [])
            new_conflicts = [ln for ln in dedup_conflicts if ln not in seen_conflicts]
            if new_conflicts:
                rec["seen_conflicts"] = seen_conflicts + new_conflicts
                state[session_id] = rec
                _save_state(state_file, state, session_id=session_id, now=cur_now)
                return "\n".join([delta_header, *new_conflicts])
        return ""

    turn = int(rec.get("turn", 0)) + 1
    skill_turns = dict(rec.get("skill_turns") or {})
    try:
        matches = route(prompt, load_triggers(roots_from_env()), cwd)
    except Exception:
        matches = []
    fresh_matches = [m for m in matches if (turn - int(skill_turns.get(m["skill"], -100))) >= SKILL_COOLDOWN_TURNS]
    for m in fresh_matches:
        skill_turns[m["skill"]] = turn
    skill_text = message(fresh_matches, read_root) if fresh_matches else ""

    brief_text = ""
    try:
        full_brief = _brief_builder()(prompt, cwd)
    except Exception:
        full_brief = ""
    if full_brief:
        static_sha, kb_lines, conflict_lines = _extract_dynamic_lines(full_brief)
        last_brief_at = _safe_float(rec.get("last_brief_at"), 0.0)
        last_brief_turn = int(_safe_float(rec.get("last_brief_turn"), 0))
        need_full = (
            last_brief_at == 0.0
            or (turn - last_brief_turn) >= BRIEF_COOLDOWN_TURNS
            or (cur_now - last_brief_at) >= BRIEF_COOLDOWN_SECS
            or static_sha != rec.get("brief_static_sha")
        )
        if need_full:
            brief_text = full_brief
            rec["last_brief_at"] = cur_now
            rec["last_brief_turn"] = turn
            rec["brief_static_sha"] = static_sha
            rec["seen_kb"] = list(kb_lines)
            rec["seen_conflicts"] = list(conflict_lines)
        else:
            seen_kb = list(rec.get("seen_kb") or [])
            seen_conflicts = list(rec.get("seen_conflicts") or [])
            new_kb = [ln for ln in kb_lines if ln not in seen_kb]
            new_conflicts = [ln for ln in conflict_lines if ln not in seen_conflicts]
            if new_kb or new_conflicts:
                brief_text = "\n".join([delta_header, *new_conflicts, *new_kb])
                rec["seen_kb"] = seen_kb + new_kb
                rec["seen_conflicts"] = seen_conflicts + new_conflicts

    rec["turn"] = turn
    rec["last_prompt_sha"] = prompt_sha
    rec["last_seen_at"] = cur_now
    rec["skill_turns"] = skill_turns
    state[session_id] = rec
    _save_state(state_file, state, session_id=session_id, now=cur_now)
    return "\n\n".join(filter(None, (skill_text, brief_text)))


def claude_hook(payload: dict) -> dict:
    text = _turn_context_for_session(str(payload.get("prompt", "")), str(payload.get("cwd") or ""), None, _session_id(payload))
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
    text = _turn_context_for_session(str(payload.get("lastUserInput", "")), str(cwd or ""), read_root, _session_id(payload))
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
