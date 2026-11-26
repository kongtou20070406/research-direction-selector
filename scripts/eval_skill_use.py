"""Does an agent pick up a skill, and read its key references, when the task does not name it?

Runs each case through Antigravity's CLI (``agy -p ... --output-format stream-json``) in a fresh copy of
the case's workspace, then reads the tool-call trace and the final answer:

  skill_md     the agent opened the skill's SKILL.md
  refs         which of the case's required reference files it opened (substring match on the path)
  output       checks on the final answer: ``forbid`` substrings must be absent, ``require_any`` needs one hit,
               each ``require_groups`` entry {name: [regex, ...]} needs one regex to match
``copy_from`` {"root": dir, "globs": [...]} (case or top level) runs in a temp copy of those files.
A case with ``cwd`` runs inside that existing folder (pair it with ``agy_args`` like ["--mode", "plan"]).

Cases live in a JSON file:

    {"skill_dir_hint": "paper-write",
     "required_refs": ["top-venue-writing-rules.md", "exemplars/index.md"],
     "cases": [{"id": "abstract-zh", "prompt": "...", "files": {"notes.md": "..."},
                "forbid": ["card 012"], "require_any": ["TideNet"]}]}

    python eval_skill_use.py cases.json --model lead-model --repeat 2 --out results.jsonl

Each run costs one agent turn; keep cases few. The summary prints per-case rates; compare before and
after a skill change with the same cases and model.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True


def read_paths(events: list[dict]) -> list[str]:
    paths = []
    for e in events:
        step = (e.get("step_update") or {})
        info = step.get("tool_info") or {}
        params = info.get("parameters") or {}
        if step.get("state") == "DONE":
            for key in ("Path", "AbsolutePath", "path", "File", "DirectoryPath"):
                if isinstance(params.get(key), str):
                    paths.append(params[key].replace("\\", "/"))
    return paths


def final_answer(events: list[dict]) -> str:
    for e in reversed(events):
        if e.get("event") == "result":
            return str((e.get("result") or {}).get("response", ""))
    return ""


def score(case: dict, cfg: dict, events: list[dict]) -> dict:
    paths = read_paths(events)
    hint = cfg.get("skill_dir_hint", "")
    skill_md = any(p.endswith("/SKILL.md") and f"/{hint}/" in p for p in paths) if hint else True
    refs = {r: any(r in p for p in paths) for r in case.get("required_refs", cfg.get("required_refs", []))}
    answer = final_answer(events)
    # when the answer carries the deliverable in a fenced block, judge that block, not the agent's notes about it
    blocks = re.findall(r"```[^\n]*\n(.*?)```", answer, re.S)
    judged = "\n".join(blocks) if blocks else answer
    forbid_hits = [f for f in case.get("forbid", []) if f.lower() in judged.lower()]
    req = case.get("require_any", [])
    groups = {name: any(re.search(pat, answer, re.I) for pat in pats)
              for name, pats in case.get("require_groups", {}).items()}
    return {"skill_md": skill_md, "refs": refs, "all_refs": all(refs.values()) if refs else True,
            "forbid_hits": forbid_hits, "require_ok": (not req) or any(r in answer for r in req),
            "groups": groups, "all_groups": all(groups.values()) if groups else True,
            "answer_chars": len(answer), "files_opened": len(paths)}


def run_case(case: dict, cfg: dict, model: str, timeout: int, keep: Path | None) -> tuple[dict, list[dict]]:
    real = case.get("cwd")  # run inside an existing project (use agy_args such as --mode plan to stay read-only)
    work = Path(real) if real else Path(tempfile.mkdtemp(prefix=f"skilleval_{case['id']}_"))
    copy = case.get("copy_from") or cfg.get("copy_from")
    if copy and not real:  # a throwaway copy of selected project files, so the agent can act without touching the project
        src = Path(copy["root"])
        for pattern in copy.get("globs", []):
            for f in src.glob(pattern):
                if f.is_file():
                    dst = work / f.relative_to(src)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
    for rel, text in case.get("files", {}).items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text, encoding="utf-8")
    cmd = [shutil.which("agy") or "agy", "-p", case["prompt"], "--model", model,
           "--output-format", "stream-json", "--print-timeout", f"{timeout}s"] + list(cfg.get("agy_args", []))
    try:
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout + 60)
        out = p.stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
    events = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
        (keep / f"{case['id']}_{work.name}.jsonl").write_text(out, encoding="utf-8")
    if not real:
        shutil.rmtree(work, ignore_errors=True)
    return score(case, cfg, events), events


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", type=Path)
    ap.add_argument("--model", default="lead-model")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--only")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep-traces", type=Path)
    a = ap.parse_args(argv)
    cfg = json.loads(a.cases.read_text(encoding="utf-8"))
    rows = []
    for case in cfg["cases"]:
        if a.only and case["id"] != a.only:
            continue
        for i in range(a.repeat):
            res, _ = run_case(case, cfg, a.model, a.timeout, a.keep_traces)
            row = {"label": a.label, "case": case["id"], "run": i, "model": a.model, **res}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if a.out:
                with a.out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    n = len(rows) or 1
    print(f"SKILL_USE {a.label} n={len(rows)} skill_md={sum(r['skill_md'] for r in rows)}/{n} "
          f"all_refs={sum(r['all_refs'] for r in rows)}/{n} clean_output={sum(not r['forbid_hits'] for r in rows)}/{n} "
          f"all_groups={sum(r['all_groups'] for r in rows)}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
