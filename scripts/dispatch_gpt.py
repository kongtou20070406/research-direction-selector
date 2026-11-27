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
SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
FALLBACK_SKILLS_DIR = Path.home() / ".gemini" / "config" / "skills"
LAYOUT_AUDIT = SKILLS_DIR / "latex-typeset" / "scripts" / "layout_audit.py"
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


def resolve_skill_files(skill_names: list[str]) -> list[tuple[str, list[Path]]]:
    resolved: list[tuple[str, list[Path]]] = []
    for raw_name in skill_names:
        name = raw_name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(f"invalid skill name (expected bare identifier): {raw_name!r}")
        sdir = next((d / name for d in (SKILLS_DIR, FALLBACK_SKILLS_DIR) if (d / name / "SKILL.md").is_file()), None)
        if sdir is None:
            raise ValueError(f"unknown skill: {name}")
        rels = ["SKILL.md"]
        trig = sdir / "triggers.json"
        if trig.is_file():
            try:
                must_read = json.loads(trig.read_text(encoding="utf-8-sig")).get("must_read", [])
                for rel in must_read:
                    if isinstance(rel, str) and rel not in rels:
                        rels.append(rel)
            except (OSError, ValueError):
                pass
        files = [sdir / rel for rel in rels if (sdir / rel).is_file()]
        resolved.append((name, files))
    return resolved


def build_skill_bundle(skill_names: list[str]) -> str:
    if not skill_names:
        return ""
    blocks = []
    for name, files in resolve_skill_files(skill_names):
        parts = [
            f"[MANDATORY SKILL CONTRACT: {name}]",
            "Follow every rule below strictly. Write verbose logs/diffs to disk and return a concise structured gist (<=15 lines: files changed, exact metrics/checks, blockers).",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8-sig").strip()
            if path.name != "SKILL.md" and len(text) > 6000:
                parts.append(f"--- {path.name} (read on demand: {path.as_posix()}, {len(text)} chars) ---")
            else:
                parts.append(f"--- {path.name} ---\n{text}")
        blocks.append("\n\n".join(parts))
    return "\n\n".join(blocks)


def _tex_targets(repo: Path, allow: list[str]) -> list[Path]:
    found: list[Path] = []
    for rel in allow:
        p = repo / rel
        if p.is_file() and p.suffix == ".tex":
            found.append(p)
        elif p.is_dir():
            found.extend(sorted(p.rglob("*.tex")))
    return list(dict.fromkeys(found))


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
    skill_names = [s.strip() for s in (getattr(args, "skill", None) or []) if isinstance(s, str) and s.strip()]
    skill_bundle = build_skill_bundle(skill_names)
    verify_cmd = getattr(args, "verify_cmd", None)
    venue = getattr(args, "venue", "arxiv")
    parent = scratch_parent(repo)
    scratch_dir = (nullcontext(parent / f"dispatch-gpt-{uuid4().hex}") if args.dry_run
                   else tempfile.TemporaryDirectory(prefix="dispatch-gpt-", dir=parent))
    with scratch_dir as tmp:
        scratch = Path(tmp)
        baseline = scratch / "baseline.json"
        prose_snap = scratch / "prose_snap.json"
        staged_report = scratch / "report.md"
        report = args.out_report.resolve() if args.out_report else scratch.with_suffix(".md")
        ledger = args.ledger.resolve() if args.ledger else None
        if report in (args.prompt_file.resolve(), ledger):
            raise ValueError("--out-report must differ from --prompt-file and --ledger")
        protected_paths = {(repo / p).resolve() for p in protect}
        if any(
            report == pp or report.is_relative_to(pp) or (report.exists() and pp.exists() and os.path.samefile(report, pp))
            for pp in protected_paths
        ):
            raise ValueError(f"--out-report cannot overwrite protected path: {report}")

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
                              "report": str(report), "skills": skill_names, "verify_cmd": verify_cmd},
                             ensure_ascii=False, indent=2))
            return 0

        prompt = args.prompt_file.read_text(encoding="utf-8-sig")
        if skill_bundle:
            prompt = f"{skill_bundle}\n\n{prompt}"
        if write and not args.no_sandbox_note:
            note = SANDBOX_NOTE
            if "latex-typeset" in skill_names:
                note = "\n".join(ln for ln in SANDBOX_NOTE.splitlines() if "py_compile" not in ln)
            prompt = f"{note}\n\n{prompt}"
        options = {"capture_output": True, "encoding": "utf-8", "errors": "replace",
                   "env": dict(os.environ, PYTHONDONTWRITEBYTECODE="1")}
        tex_files = _tex_targets(repo, allow) if (write and "latex-typeset" in skill_names and LAYOUT_AUDIT.is_file()) else []
        if write:
            saved = subprocess.run(snapshot, **options)
            if saved.returncode:
                raise RuntimeError(saved.stderr.strip() or saved.stdout.strip() or "snapshot failed")
            if tex_files:
                snap_cmd = [sys.executable, "-E", "-s", "-X", "utf8", "-B", str(LAYOUT_AUDIT),
                            str(repo), "--tex", *[str(p) for p in tex_files], "--snapshot-prose", str(prose_snap)]
                snapped = subprocess.run(snap_cmd, **options)
                if snapped.returncode:
                    raise RuntimeError(snapped.stderr.strip() or snapped.stdout.strip() or "prose snapshot failed")
        worker_ok = False
        publish_ok = False
        try:
            worker = runner(codex, input=prompt, timeout=args.timeout, **options)
            report_text = staged_report.read_text(encoding="utf-8-sig") if staged_report.is_file() else None
            if worker.returncode or report_text is None:
                detail = (worker.stderr or "").strip().splitlines()
                raise RuntimeError(f"Codex exit {worker.returncode}. " +
                                   (detail[-1] if detail else "No usable worker report"))
            worker_ok = True
            if not write:
                if verify_cmd:
                    verified = subprocess.run(verify_cmd, shell=True, cwd=repo, timeout=args.timeout, **options)
                    if verified.returncode:
                        print(f"Report: {report}")
                        print("VerifyCmd: failed")
                        return 1
                publish_ok = True
                print(f"Report: {report}")
                print("Summary: " + " ".join(report_text.split())[:240])
                return 0

            skill_gate_ok = True
            post_tex_files = sorted(set(tex_files) | set(_tex_targets(repo, allow))) if "latex-typeset" in skill_names else []
            if post_tex_files and LAYOUT_AUDIT.is_file():
                audit_cmd = [sys.executable, "-E", "-s", "-X", "utf8", "-B", str(LAYOUT_AUDIT),
                             str(repo), "--tex", *[str(p) for p in post_tex_files], "--venue", venue]
                if prose_snap.is_file():
                    audit_cmd += ["--verify-prose", str(prose_snap)]
                audited = subprocess.run(audit_cmd, timeout=args.timeout, **options)
                if audited.returncode:
                    skill_gate_ok = False
            verify_cmd_ok = True
            if verify_cmd:
                verified = subprocess.run(verify_cmd, shell=True, cwd=repo, timeout=args.timeout, **options)
                if verified.returncode:
                    verify_cmd_ok = False

            if not (skill_gate_ok and verify_cmd_ok) and "--ledger" in acceptance:
                idx = acceptance.index("--ledger")
                acceptance = acceptance[:idx] + acceptance[idx + 2:]

            claim = re.search(r"^\s*(?:(?:worker[- ]claim|verdict|status)\s*:\s*)?(PASS|DONE|SUPPORTED|FIXED|OK)\s*$",
                              report_text, re.IGNORECASE | re.MULTILINE)
            if claim:
                acceptance += ["--worker-claim", claim[1]]
            accepted = subprocess.run(acceptance, timeout=args.timeout, **options)
            try:
                result = json.loads(accepted.stdout)
            except ValueError as exc:
                raise RuntimeError(accepted.stderr.strip() or "acceptance returned no verdict") from exc
            checks = result["checks"]
            failure_modes = list(result["failure_modes"])
            if not skill_gate_ok:
                failure_modes.append("skill_gate_failed")
            if not verify_cmd_ok:
                failure_modes.append("verify_cmd_failed")
            overall_verdict = result["verdict"] if (skill_gate_ok and verify_cmd_ok) else "REJECTED"
            publish_ok = overall_verdict == "ACCEPTED"
            failures = ", ".join(dict.fromkeys(failure_modes))
            verdict = overall_verdict + (f" ({failures})" if failures else "")
            print_verdict(verdict, len(checks["scope"]["changed"]), checks, report)
            if not skill_gate_ok:
                print("SkillGate: failed")
            if not verify_cmd_ok:
                print("VerifyCmd: failed")
            return 0 if accepted.returncode == 0 and overall_verdict == "ACCEPTED" else 1
        finally:
            # Publish only after acceptance, keeping dispatcher writes outside the diff, and never clobber an existing report on failure/rejection.
            if staged_report.is_file() and ((worker_ok and publish_ok) or not report.exists()):
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
    parser.add_argument("--skill", nargs="*", default=[], help="inline skill contract(s) and enforce skill gates")
    parser.add_argument("--verify-cmd", default=None, help="host-side verification command run after acceptance")
    parser.add_argument("--venue", default="arxiv", help="venue passed to layout_audit when latex-typeset is active")
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
