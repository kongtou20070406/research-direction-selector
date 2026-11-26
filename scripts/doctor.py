"""Check the local research stack without changing files or using an LLM."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter
from contextlib import redirect_stdout
from functools import cache
from pathlib import Path

sys.dont_write_bytecode = True

if __package__:
    from . import check_routes, experience_brief, sync_skills
else:
    import check_routes
    import experience_brief
    import sync_skills

GATE_SCRIPTS = Path(__file__).resolve().parents[1] / "skills/research-gate/scripts"
TASKS = ("LoopAGI_Obsidian_Merge", "PaperGate_Watch", "Research_Overview_Weekly")


@cache
def _gate_module(name: str):
    spec = importlib.util.spec_from_file_location(f"doctor_{name}", GATE_SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_command(main, argv: list[str]) -> tuple[int, list[str]]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(argv)
    return code, output.getvalue().splitlines()


def _skills() -> tuple[str, list[str]]:
    is_link = sync_skills.is_link
    try:
        if os.name == "nt" and not hasattr(os.path, "isjunction"):
            # Reuse the 3.11 junction fallback for the sync check.
            sync_skills.is_link = _gate_module("share")._linked
        code, lines = _check_command(sync_skills.main, [])
    finally:
        sync_skills.is_link = is_link
    git = next((line for line in lines if line.startswith("git:")), "")
    if code or any(line.startswith(("BAD ", "NOT IN SYNC")) for line in lines):
        return "FAIL", [line for line in lines if line.startswith(("BAD ", "NOT IN SYNC"))] or lines
    if re.search(r"\b(?:[1-9]\d* uncommitted paths|(?:ahead|behind) [1-9]\d*)\b", git):
        return "WARN", [git]
    return "OK", ["installed links and copies match"]


def _routes() -> tuple[str, list[str]]:
    code, lines = _check_command(check_routes.main, ["--repo-only"])
    return ("FAIL", lines) if code else ("OK", ["repo link and path checks passed"])


def _brief(project: Path) -> tuple[str, list[str]]:
    ok, issues = experience_brief.check_config(project)
    return ("OK", [str(experience_brief.find_config(project))]) if ok else ("FAIL", issues)


def _vault_dirs(project: Path | None, explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [explicit]
    config = experience_brief.find_config(project)
    if config is None:
        return []
    try:
        paths = experience_brief._load_config(config).get("knowledge", [])
    except (OSError, ValueError):
        return []  # The brief check reports the config error.
    if not isinstance(paths, list):
        return []
    return sorted({(config.parent.parent / path).resolve().parent
                   for path in paths if isinstance(path, str) and path.strip()})


def _knowledge(project: Path | None, explicit: Path | None) -> tuple[str, list[str]]:
    directories = _vault_dirs(project, explicit)
    if not directories:
        return "OK", ["skipped (no vault data configured)"]
    vault = _gate_module("knowledge_vault")
    issues = []
    count = 0
    for directory in directories:
        if not directory.is_dir():
            issues.append(f"vault data directory not found: {directory}")
            continue
        for path in sorted(directory.glob("*.jsonl")):
            if path.name in vault.IGNORED_JSONL or not path.is_file():
                continue
            count += 1
            try:
                issues.extend(f"{path}: {issue}" for issue in vault.check_entries(vault.kn.load(path)))
            except (OSError, UnicodeError, SystemExit) as error:
                issues.append(str(error))
    return ("FAIL", issues) if issues else ("OK", [f"{count} registries checked"])


def _projects(vault: Path, data: Path) -> tuple[str, list[str]]:
    registry = data / _gate_module("knowledge_vault").PROJECTS_FILE
    projects = json.loads(registry.read_text(encoding="utf-8-sig"))
    if not isinstance(projects, dict):
        raise ValueError(f"{registry}: expected a JSON object")

    status = "OK"
    details = []
    for name, config in projects.items():
        root = config.get("root") if isinstance(config, dict) else None
        if not isinstance(root, str) or not root.strip():
            continue
        project = vault / root
        if not (project / ".agents/brief.json").is_file():
            continue

        result = _run(_brief, project)
        brief_status = result["status"]
        library = config.get("library")
        pdfs = 0
        if isinstance(library, str) and library.strip():
            drafts = vault / library / "论文草稿"
            pdfs = sum(pdf.is_file() for pdf in drafts.rglob("*.pdf"))
        details.append(f"{name}: brief {brief_status}, {pdfs} PDF(s)")
        if brief_status == "FAIL":
            status = "FAIL"
            details.extend(f"{name}: {issue}" for issue in result["details"])
    return status, details or ["no registered projects with .agents/brief.json"]


def _shared(directory: Path) -> tuple[str, list[str]]:
    share = _gate_module("share")
    publisher = _gate_module("publish_shared")
    issues = share.check_shared(directory) + publisher.check_knowledge_privacy(directory)
    return ("FAIL", issues) if issues else ("OK", ["manifest and knowledge privacy checks passed"])


def _tasks() -> tuple[str, list[str]]:
    names = ", ".join(f"'{name}'" for name in TASKS)
    command = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n" + f"""
foreach ($name in @({names})) {{
    try {{
        $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop
        "$name|$($info.LastTaskResult)"
    }} catch {{
        "$name|$($_.Exception.Message)"
    }}
}}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    if result.returncode:
        return "FAIL", [result.stderr.strip() or f"PowerShell exited {result.returncode}"]
    rows = dict(line.split("|", 1) for line in result.stdout.splitlines() if "|" in line)
    issues = [f"{name}: LastTaskResult {rows[name]}" if rows.get(name, "").isdigit()
              else f"{name}: {rows.get(name) or 'no task info returned'}"
              for name in TASKS if rows.get(name) not in {"0", "267009", "267011"}]
    return ("FAIL", issues) if issues else ("OK", [f"{len(TASKS)} scheduled tasks checked"])


def _retro(project: Path) -> tuple[str, list[str]]:
    """0-token workflow self-optimization telemetry: never auto-edits SKILL.md without human sign-off."""
    def load_jsonl(path: Path) -> list[dict]:
        if not path.is_file():
            return []
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]

    details, proposals = [], []
    gpt_rows = load_jsonl(project / "ledger" / "gpt_experience.jsonl")
    workers = [r for r in gpt_rows if r.get("role", "worker") == "worker"]
    redteam = [r for r in gpt_rows if r.get("role") == "redteam"]
    if workers:
        acc = sum(1 for r in workers if r.get("verdict") == "ACCEPTED")
        fmodes = Counter(m for r in workers for m in r.get("failure_modes", []))
        top_fm = ", ".join(f"{k}={v}" for k, v in fmodes.most_common(3)) or "none"
        details.append(f"workers: {acc}/{len(workers)} ACCEPTED (top failures: {top_fm})")
        if acc * 10 < len(workers) * 7:
            proposals.append(f"calibrate worker dispatch/acceptance ({acc}/{len(workers)} accepted; top: {top_fm})")
    if redteam:
        sup = sum(1 for r in redteam if r.get("verdict") == "SUPPORTED")
        details.append(f"redteam: {sup}/{len(redteam)} SUPPORTED")
        if sup * 5 < len(redteam):
            proposals.append(f"keep redteam off exploratory single-arm runs ({sup}/{len(redteam)} SUPPORTED)")

    exp_rows = load_jsonl(project / "ledger" / "experiments.jsonl")
    if exp_rows:
        vc = Counter(r.get("verdict", "?") for r in exp_rows)
        details.append("experiments: " + ", ".join(f"{k}={vc[k]}" for k in ("PASS", "PARTIAL", "FAIL") if vc[k]))

    py_files = [p for p in (project / "scripts").glob("*.py")] if (project / "scripts").is_dir() else []
    py_lines = sum(len(p.read_text(encoding="utf-8", errors="ignore").splitlines()) for p in py_files)
    if py_files:
        details.append(f"project glue scripts: {len(py_files)} files, {py_lines} lines")
        if len(py_files) > 15 or py_lines > 4000:
            proposals.append(f"prune project glue ({len(py_files)} scripts / {py_lines} lines -> declarative config)")

    cfg_path = experience_brief.find_config(project)
    if cfg_path is not None:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        mem_dir = cfg_path.parent.parent / cfg["memory_dir"] if cfg.get("memory_dir") else None
        if mem_dir and mem_dir.is_dir():
            fbs = sorted(mem_dir.glob("feedback-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
            if fbs:
                details.append("newest feedback: " + ", ".join(p.stem for p in fbs))

    for p in proposals[:3]:
        details.append(f"delta proposal: {p}")
    details.append("policy: propose <=3 delta cuts/calibrations; never auto-rewrite SKILL.md without human sign-off")
    return ("WARN" if proposals else "OK"), details


def _run(check, *args) -> dict:
    try:
        status, details = check(*args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, SystemExit) as error:
        status, details = "FAIL", [str(error)]
    return {"status": status, "details": details}


def _skip(reason: str) -> dict:
    return {"status": "OK", "details": [f"skipped ({reason})"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--shared", type=Path)
    parser.add_argument("--vault", type=Path, help="check registered projects in this vault")
    parser.add_argument("--vault-data", type=Path)
    parser.add_argument("--no-tasks", action="store_true")
    parser.add_argument("--retro", action="store_true", help="summarize workflow friction & <=3 delta proposals")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    args = parser.parse_args(argv)
    vault_data = args.vault / "知识库" / "_数据" if args.vault is not None else None

    checks = {
        "skills": _run(_skills),
        "routes": _run(_routes),
        "brief": _run(_brief, args.project) if args.project else _skip("no project"),
        "knowledge": _run(_knowledge, args.project, args.vault_data or vault_data),
        "shared": _run(_shared, args.shared) if args.shared else _skip("no shared directory"),
        "tasks": _skip("--no-tasks") if args.no_tasks else (
            _run(_tasks) if os.name == "nt" else _skip("Windows only")
        ),
    }
    if vault_data is not None:
        checks["projects"] = _run(_projects, args.vault, vault_data)
    if args.retro:
        checks["retro"] = _run(_retro, args.project) if args.project else _skip("no project")
    ok = all(check["status"] != "FAIL" for check in checks.values())
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False))
    else:
        print(f"{'CHECK':<10} {'STATUS':<6} DETAILS")
        for name, check in checks.items():
            first, *rest = check["details"] or [""]
            print(f"{name:<10} {check['status']:<6} {first}")
            for detail in rest:
                print(f"{'':<18}{detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
