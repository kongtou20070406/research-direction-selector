"""Dispatch one Codex worker and independently accept workspace edits."""

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4


GATE = Path(__file__).resolve().parents[1] / "skills/research-gate/scripts/accept_worker_deliverable.py"
SANDBOX_NOTE = (
    "Inside the Windows Codex sandbox, do not run full pytest or git clone (ACLs block temp dirs and Git signal pipes).\n"
    "Do not create temporary permission-probe directories inside the repo.\n"
    "Only verify syntax with python -m py_compile on the files you edit.\n"
    "The host dispatcher runs the full pytest suite outside the sandbox after you exit."
)


def codex_launcher() -> list[str]:
    exe = shutil.which("codex") or "codex"
    if os.name == "nt" and Path(exe).suffix.lower() in {"", ".cmd", ".bat"}:
        # Run the npm entry point directly so cmd.exe cannot reinterpret arguments.
        entry = Path(exe).parent / "node_modules/@openai/codex/bin/codex.js"
        if entry.is_file():
            local_node = entry.parents[4] / "node.exe"
            node = str(local_node) if local_node.is_file() else shutil.which("node") or "node"
            return [node, str(entry)]
    return [exe]


def scratch_parent(repo: Path) -> Path:
    for candidate in (Path(tempfile.gettempdir()).resolve(), repo.parent, Path.home().resolve()):
        if not candidate.is_relative_to(repo):
            return candidate
    raise ValueError("no temporary directory outside --repo")


def repo_relative(repo: Path, paths: list[str]) -> list[str]:
    result = []
    for name in paths:
        path = (repo / name).resolve()
        if path == repo or not path.is_relative_to(repo):
            raise ValueError(f"expected a file or subdirectory inside --repo: {name}")
        result.append(path.relative_to(repo).as_posix())
    return result


def print_verdict(verdict: str, changed: int | None, checks: dict, report: Path) -> None:
    def status(name: str) -> str:
        check = checks.get(name, {})
        return "skipped" if check.get("skipped") else {True: "ok", False: "failed"}.get(check.get("ok"), "not run")

    print(f"Verdict: {verdict}")
    print(f"Changed: {changed if changed is not None else 'not measured'}")
    print(f"Pytest: {status('pytest')}")
    print(f"Hygiene: {status('hygiene')}")
    print(f"Report: {report}")


def dispatch(args: argparse.Namespace, runner=subprocess.run) -> int:
    """Run the real acceptance gate. ``runner`` replaces only the Codex subprocess."""
    repo = args.repo.resolve(strict=True)
    allow, protect = repo_relative(repo, args.allow), repo_relative(repo, args.protect)
    write = args.sandbox == "workspace-write"
    parent = scratch_parent(repo)
    scratch_dir = (nullcontext(parent / f"dispatch-gpt-{uuid4().hex}") if args.dry_run
                   else tempfile.TemporaryDirectory(prefix="dispatch-gpt-", dir=parent))
    with scratch_dir as tmp:
        scratch = Path(tmp)
        baseline = scratch / "baseline.json"
        staged_report = scratch / "report.md"
        report = args.out_report.resolve() if args.out_report else scratch.with_suffix(".md")
        ledger = args.ledger.resolve() if args.ledger else None
        if report in (args.prompt_file.resolve(), ledger):
            raise ValueError("--out-report must differ from --prompt-file and --ledger")

        codex = [*codex_launcher(), "exec"]
        if not write:
            codex.append("--ephemeral")
        codex += ["--skip-git-repo-check", "-C", str(repo), "-m", args.model,
                  "-c", 'model_provider="magpie"', "-c", f'model_reasoning_effort="{args.effort}"',
                  "-s", args.sandbox, "-o", str(staged_report), "-"]
        gate = [sys.executable, "-E", "-s", "-X", "utf8", "-B", str(GATE), "--repo-root", str(repo)]
        snapshot = gate + ["--snapshot-out", str(baseline), "--protect", *protect]
        acceptance = gate + ["--baseline", str(baseline), "--task-id", args.task_id, "--model", args.model,
                             "--allow", *allow, "--pytest-target", args.pytest_target, "--evidence", str(report)]
        if args.skip_pytest:
            acceptance += ["--skip-pytest"]
        if ledger:
            acceptance += ["--ledger", str(ledger)]
        if args.dry_run:
            print(json.dumps({"codex": codex, "acceptance": acceptance if write else None,
                              "report": str(report)}, ensure_ascii=False, indent=2))
            return 0

        prompt = args.prompt_file.read_text(encoding="utf-8-sig")
        if write and not args.no_sandbox_note:
            prompt = f"{SANDBOX_NOTE}\n\n{prompt}"
        options = {"capture_output": True, "encoding": "utf-8", "errors": "replace",
                   "env": dict(os.environ, PYTHONDONTWRITEBYTECODE="1")}
        if write:
            saved = subprocess.run(snapshot, **options)
            if saved.returncode:
                raise RuntimeError(saved.stderr.strip() or saved.stdout.strip() or "snapshot failed")
        try:
            worker = runner(codex, input=prompt, timeout=args.timeout, **options)
            report_text = staged_report.read_text(encoding="utf-8-sig") if staged_report.is_file() else None
            if worker.returncode or report_text is None:
                detail = (worker.stderr or "").strip().splitlines()
                raise RuntimeError(f"Codex exit {worker.returncode}. " +
                                   (detail[-1] if detail else "No usable worker report"))
            if not write:
                print(f"Report: {report}")
                print("Summary: " + " ".join(report_text.split())[:240])
                return 0

            claim = re.search(r"^\s*(?:(?:worker[- ]claim|verdict|status)\s*:\s*)?(PASS|DONE|SUPPORTED|FIXED|OK)\s*$",
                              report_text, re.IGNORECASE | re.MULTILINE)
            if claim:
                acceptance += ["--worker-claim", claim[1]]
            accepted = subprocess.run(acceptance, **options)
            try:
                result = json.loads(accepted.stdout)
            except ValueError as exc:
                raise RuntimeError(accepted.stderr.strip() or "acceptance returned no verdict") from exc
            checks = result["checks"]
            failures = ", ".join(result["failure_modes"])
            verdict = result["verdict"] + (f" ({failures})" if failures else "")
            print_verdict(verdict, len(checks["scope"]["changed"]), checks, report)
            return 0 if accepted.returncode == 0 and result["verdict"] == "ACCEPTED" else 1
        finally:
            # Publish only after acceptance, keeping dispatcher writes outside the diff.
            if staged_report.is_file():
                report.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staged_report, report)


def main(argv: list[str] | None = None, *, runner=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--allow", nargs="+", required=True)
    parser.add_argument("--protect", nargs="*", default=[])
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--model", default="engineering-worker")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--sandbox", choices=("workspace-write", "read-only"), default="workspace-write")
    parser.add_argument("--no-sandbox-note", action="store_true", help="omit the Windows sandbox preamble")
    parser.add_argument("--pytest-target", default="tests")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--out-report", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return dispatch(args, runner)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"dispatch_gpt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
