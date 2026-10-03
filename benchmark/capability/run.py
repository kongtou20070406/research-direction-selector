"""F1 acceptance-gate runner: materialize workspaces, optionally execute trials
through an installed codex CLI, then grade every workspace.

Engineering rounds only: N=3 default, results are iteration signal and never a
capability claim (#166). A round is reproducible with one command:

    python -B benchmark/capability/run.py --grade-only workspaces/
    python -B benchmark/capability/run.py --variant b1v1 --trials 3

The runner never reads sealed ground truth while a trial is live; grading is a
separate step after all trials finish, matching the pilot's pre-registration
discipline.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FAMILY_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FAMILY_DIR))

from workspaces import load_truth, materialize  # noqa: E402
from grade import grade_workspace  # noqa: E402

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "medium"
PROMPT = (
    "You are doing an evaluation task. Work only inside the current directory. "
    "Read README.md and protocol.json, run the evaluation you need with the "
    "provided runner, then write your final answer to DECISION.json in the "
    "current directory with fields: decision (LAUNCH or NO_LAUNCH), "
    "pooled_gain (number, the mean per-batch gain across the batches you used), "
    "batches_used (array of ints), reasoning (short string). "
    "Then print the DECISION.json content."
)


def find_codex():
    """Locate the codex CLI: PATH first, then the npm default on Windows."""
    exe = shutil.which("codex")
    if exe:
        return exe
    candidate = Path(os.environ.get("APPDATA", "")) / (
        "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/"
        "vendor/x86_64-pc-windows-msvc/bin/codex.exe")
    return str(candidate) if candidate.exists() else None


def run_trial(codex, workspace, model, effort, log_path):
    command = [codex, "exec", "-c", f"model_reasoning_effort={effort}",
               "-c", f"model={model}", "-c", "sandbox_mode=workspace-write",
               "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", PROMPT]
    with open(log_path, "w", encoding="utf-8") as log:
        completed = subprocess.run(command, cwd=workspace, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=1800)
    return completed.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default="b1v1")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--base-dir", type=Path, default=None,
                        help="where trial workspaces and logs are created")
    parser.add_argument("--skip-exec", action="store_true",
                        help="grade existing workspaces without running trials")
    parser.add_argument("--grade-only", type=Path, default=None,
                        help="grade one existing workspace and exit")
    args = parser.parse_args()

    truth = load_truth(args.variant)
    base = args.base_dir or Path(os.environ.get("TEMP", tempfile.gettempdir())) / f"f1-{args.variant}"
    if args.grade_only:
        result = grade_workspace(args.grade_only, FAMILY_DIR / "ground-truth" / f"{args.variant}.json")
        print(json.dumps(result, indent=2))
        return

    (base / "logs").mkdir(parents=True, exist_ok=True)
    rows = []
    if args.skip_exec:
        workspaces = sorted(p for p in base.glob("ws-*") if p.is_dir())
        if not workspaces:
            raise SystemExit(f"no workspaces under {base}")
    else:
        codex = find_codex()
        if not codex:
            raise SystemExit("codex CLI not found; use --skip-exec to grade existing workspaces")
        workspaces = []
        for index in range(1, args.trials + 1):
            workspace = materialize(args.variant, base / f"ws-{index}")
            workspaces.append(workspace)
            print(f"=== trial {index} start ===", flush=True)
            code = run_trial(codex, workspace, args.model, args.effort,
                             base / "logs" / f"trial-{index}.out")
            print(f"=== trial {index} end exit={code} ===", flush=True)
    for workspace in workspaces:
        result = grade_workspace(workspace, FAMILY_DIR / "ground-truth" / f"{args.variant}.json")
        rows.append({"workspace": workspace.name, **result,
                     "decision": result.get("decision")})
    scores = [row["score"] for row in rows]
    summary = {
        "family": "F1-launch-quota", "variant": args.variant,
        "model": args.model, "effort": args.effort, "n": len(rows),
        "scores": {row["workspace"]: row["score"] for row in rows},
        "wrong": scores.count("wrong"), "correct": scores.count("correct"),
        "partial": scores.count("partial"),
        "gate": "PASS (>=1 wrong)" if scores.count("wrong") >= 1 else "FAIL (fixture does not discriminate)",
        "note": "engineering-round signal only; not a capability claim (#166)",
    }
    output = base / "summary.json"
    output.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"written: {output}")


if __name__ == "__main__":
    main()
