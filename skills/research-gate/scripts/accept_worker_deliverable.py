"""Automated acceptance gate and experience recorder for delegated coding workers.

Two-step use (recommended):

1. Before dispatch, snapshot the workspace and lock the files the worker must not touch:
       accept_worker_deliverable.py --snapshot-out <scratch>/base.json --protect tests
2. After the worker reports, accept against that snapshot:
       accept_worker_deliverable.py --baseline <scratch>/base.json --task-id T --allow src/x.py \
           --protect tests --worker-claim PASS --ledger ledger/gpt_experience.jsonl

Scope is strict by default: any changed path outside ``--allow`` rejects the deliverable.
With a baseline, only changes made after the snapshot count, so a dirty tree still works.
A worker's own PASS is recorded as a claim; the gate re-runs every check itself.
"""

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True

CACHE_DIR_NAMES = ("__pycache__", ".hypothesis", ".pytest_cache")
HYGIENE_IGNORED = {".git", "external"}

# Failure taxonomy for the experience ledger. The first five are derived from failed checks;
# the rest are recorded by the lead agent after reading the deliverable.
FAILURE_MODES = (
    "scope_creep",
    "test_tampering",
    "spec_violation",
    "verification_failure",
    "hygiene",
    "false_claim",
    "spec_misread",
    "special_casing",
    "other",
)
CHECK_TO_FAILURE = {
    "scope": "scope_creep",
    "protected": "test_tampering",
    "targets": "spec_violation",
    "pytest": "verification_failure",
    "holdout": "verification_failure",
    "hygiene": "hygiene",
}


def _norm_rel(path_str: str) -> str:
    norm = path_str.replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


def _under(path: str, prefixes: list[str]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def list_workspace_files(repo_root: Path) -> list[str]:
    """Tracked plus untracked-but-not-ignored files, one entry per file (no directory collapsing)."""
    proc = _git(repo_root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git ls-files failed")
    files = {_norm_rel(p) for p in proc.stdout.split("\0") if p}
    return sorted(f for f in files if not any(part in CACHE_DIR_NAMES for part in f.split("/")))


def take_snapshot(repo_root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for rel in list_workspace_files(repo_root):
        p = repo_root / rel
        if p.is_file():
            snap[rel] = _sha256(p)
    return snap


def changed_since(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def changed_from_git_status(repo_root: Path) -> list[str]:
    """Fallback without a baseline: every dirty path, with untracked directories expanded."""
    proc = _git(repo_root, "status", "--porcelain=v1", "-z", "-uall")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git status failed")
    entries = proc.stdout.split("\0")
    changed: list[str] = []
    i = 0
    while i < len(entries):
        raw = entries[i]
        i += 1
        if len(raw) < 4:
            continue
        status, path = raw[:2], raw[3:]
        changed.append(_norm_rel(path))
        if "R" in status or "C" in status:
            # -z puts the rename source in the next field; the source path also changed.
            if i < len(entries) and entries[i]:
                changed.append(_norm_rel(entries[i]))
            i += 1
    return sorted(set(changed))


def check_scope(changed: list[str], allowed: list[str], strict: bool, mode: str, exclude: list[str]) -> dict:
    out_of_scope = [c for c in changed if not _under(c, allowed) and not _under(c, exclude)]
    return {
        "ok": (not out_of_scope) if strict else True,
        "mode": mode,
        "strict": strict,
        "excluded": exclude,
        "changed_count": len(changed),
        "changed": changed[:50],
        "out_of_scope": out_of_scope[:50],
    }


def check_protected(changed: list[str], protected: list[str], baseline_known: bool, exclude: list[str] | None = None) -> dict:
    """Any add, edit or delete under a protected path counts, including a new conftest.py.

    Paths in ``exclude`` belong to someone else working at the same time (another worker or
    the lead) and are not the worker's tampering.
    """
    if not protected:
        return {"ok": True, "skipped": True}
    touched = [c for c in changed if _under(c, protected) and not _under(c, exclude or [])]
    return {"ok": not touched, "protected": protected, "touched": touched[:50], "baseline": baseline_known}


def check_targets(repo_root: Path, allowed: list[str]) -> dict:
    missing = []
    syntax_errors = []
    for rel in allowed:
        target = repo_root / rel
        if not target.exists():
            missing.append(rel)
            continue
        if target.is_file() and target.suffix == ".py":
            try:
                ast.parse(target.read_text(encoding="utf-8-sig"), filename=str(target))
            except SyntaxError as exc:
                syntax_errors.append(f"{rel}:{exc.lineno}:{exc.msg}")
    return {"ok": not missing and not syntax_errors, "missing": missing, "syntax_errors": syntax_errors}


def hygiene_paths(repo_root: Path) -> tuple[list[str], list[str]]:
    def _keep(p: Path) -> bool:
        return not any(part in HYGIENE_IGNORED for part in p.relative_to(repo_root).parts)

    pycs = [str(p.relative_to(repo_root)) for p in repo_root.rglob("*.pyc") if _keep(p)]
    caches = [
        str(p.relative_to(repo_root))
        for name in CACHE_DIR_NAMES
        for p in repo_root.rglob(name)
        if p.is_dir() and _keep(p)
    ]
    return pycs, caches


def check_hygiene(repo_root: Path, ignore_cache_dirs: set[str] | None = None,
                  ignore_pycs: set[str] | None = None) -> dict:
    pycs, caches = hygiene_paths(repo_root)
    if ignore_pycs is not None:
        pycs = [path for path in pycs if path not in ignore_pycs]
    if ignore_cache_dirs is not None:
        caches = [path for path in caches if path not in ignore_cache_dirs]
    result = {"ok": not pycs and not caches, "pyc_count": len(pycs), "stray_cache_dirs": caches[:10]}
    if ignore_cache_dirs is not None:
        result["preexisting_cache_dirs"] = sorted(ignore_cache_dirs)
    return result


def run_pytest(repo_root: Path, python_exe: str, target: str) -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with tempfile.TemporaryDirectory(prefix="worker_hyp_") as hyp_dir:
        env["HYPOTHESIS_STORAGE_DIRECTORY"] = hyp_dir
        proc = subprocess.run(
            [python_exe, "-E", "-s", "-X", "utf8", "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", target],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "target": target,
        "summary_tail": "\n".join(proc.stdout.strip().splitlines()[-4:]),
    }


def _max_if_depth(node: ast.AST, depth: int = 0) -> int:
    best = depth
    for child in ast.iter_child_nodes(node):
        best = max(best, _max_if_depth(child, depth + 1 if isinstance(child, ast.If) else depth))
    return best


def check_code_quality(
    repo_root: Path,
    targets: list[str],
    *,
    max_forward_lines: int = 80,
    max_if_depth: int = 3,
) -> dict:
    """Scan Python files for reference/ purity, silent excepts, and readability."""
    py_files: list[Path] = []
    seen: set[Path] = set()
    for raw in targets:
        p = Path(raw)
        cand = p if p.is_absolute() else (repo_root / p)
        items = [cand] if cand.is_file() else (sorted(cand.rglob("*.py")) if cand.is_dir() else [])
        for f in items:
            rf = f.resolve()
            if "__pycache__" not in rf.parts and rf not in seen:
                seen.add(rf)
                py_files.append(rf)

    errors, warnings = [], []
    for raw in targets:
        p = Path(raw)
        cand = p if p.is_absolute() else (repo_root / p)
        if not cand.exists():
            errors.append(f"missing_target  {raw}: target path does not exist")
    for path in py_files:
        try:
            rel = path.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = path.name
        in_ref = any(part.lower() == "reference" for part in path.parts)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"syntax  {rel}:{exc.lineno}:{exc.msg}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                is_broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"))
                is_empty = len(node.body) == 1 and (
                    isinstance(node.body[0], ast.Pass)
                    or (isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and node.body[0].value.value is Ellipsis)
                )
                if is_broad and is_empty:
                    errors.append(f"silent_except  {rel}:{node.lineno}: bare/broad except silently passes")

            if in_ref:
                if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "triton" for a in node.names):
                    errors.append(f"reference_pollution  {rel}:{node.lineno}: triton import belongs in engine/, not reference/")
                elif isinstance(node, ast.ImportFrom) and node.module and (
                    node.module.split(".")[0] == "triton" or "cpp_extension" in node.module
                ):
                    errors.append(f"reference_pollution  {rel}:{node.lineno}: {node.module} belongs in engine/, not reference/")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    attr = node.func.attr
                    val = node.func.value
                    if attr == "cuda" or (attr == "compile" and isinstance(val, ast.Name) and val.id == "torch"):
                        errors.append(f"reference_pollution  {rel}:{node.lineno}: .{attr}() belongs in engine/, not reference/")
                elif isinstance(node, ast.FunctionDef):
                    if node.name == "forward" and (node.end_lineno or node.lineno) - node.lineno > max_forward_lines:
                        warnings.append(f"long_forward  {rel}:{node.lineno}: forward() exceeds {max_forward_lines} lines")
                    if _max_if_depth(node) > max_if_depth:
                        warnings.append(f"deep_branch  {rel}:{node.lineno}: {node.name}() if-nesting > {max_if_depth}")

    return {"ok": not errors, "files": len(py_files), "errors": errors, "warnings": warnings}


def check_torch_module_quality(model, inputs_a, inputs_b=None) -> dict:
    """Lightweight PyTorch probe for dead parameters, non-finite grads, and eval determinism."""
    import torch

    issues: list[str] = []
    model.train()
    model.zero_grad(set_to_none=True)
    args_a = inputs_a if isinstance(inputs_a, tuple) else (inputs_a,)
    out_a = model(*args_a)
    tensors = [t for t in (out_a if isinstance(out_a, (tuple, list)) else [out_a]) if torch.is_tensor(t)]
    if not tensors or not all(torch.isfinite(t).all() for t in tensors):
        issues.append("nonfinite_output: forward output missing or non-finite")
    else:
        loss = sum(t.float().sum() for t in tensors)
        loss.backward()
        dead, nonfinite = [], []
        for name, p in model.named_parameters():
            if not p.requires_grad:
                continue
            if p.grad is None or float(p.grad.abs().max()) == 0.0:
                dead.append(name)
            elif not torch.isfinite(p.grad).all():
                nonfinite.append(name)
        if dead:
            issues.append(f"dead_params: {', '.join(dead[:8])}")
        if nonfinite:
            issues.append(f"nonfinite_grads: {', '.join(nonfinite[:8])}")

    if inputs_b is not None:
        args_b = inputs_b if isinstance(inputs_b, tuple) else (inputs_b,)
        out_b = model(*args_b)
        tb = [t for t in (out_b if isinstance(out_b, (tuple, list)) else [out_b]) if torch.is_tensor(t)]
        if not tb or not all(torch.isfinite(t).all() for t in tb):
            issues.append("shape_probe_failed: secondary input shape produced non-finite output")

    model.eval()
    with torch.no_grad():
        e1 = model(*args_a)
        e2 = model(*args_a)
        t1 = [t for t in (e1 if isinstance(e1, (tuple, list)) else [e1]) if torch.is_tensor(t)]
        t2 = [t for t in (e2 if isinstance(e2, (tuple, list)) else [e2]) if torch.is_tensor(t)]
        if len(t1) != len(t2) or any(not torch.allclose(a, b) for a, b in zip(t1, t2)):
            issues.append("nondeterministic_eval: repeated eval() forward outputs differ")
    return {"ok": not issues, "issues": issues}


def derive_failure_modes(checks: dict, manual: list[str], worker_claim: str, verdict: str) -> list[str]:
    modes = [CHECK_TO_FAILURE[name] for name, res in checks.items() if not res["ok"] and name in CHECK_TO_FAILURE]
    claimed_pass = worker_claim.strip().upper() in {"PASS", "DONE", "SUPPORTED", "FIXED", "OK"}
    if claimed_pass and verdict == "REJECTED":
        modes.append("false_claim")
    modes.extend(manual)
    return sorted(set(modes))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-root", type=Path, default=Path.cwd())
    p.add_argument("--snapshot-out", type=Path, default=None, help="Write a pre-dispatch snapshot and exit.")
    p.add_argument("--baseline", type=Path, default=None, help="Snapshot taken before dispatch.")
    p.add_argument("--task-id")
    p.add_argument("--model", default="engineering-worker")
    p.add_argument("--allow", nargs="+", default=[])
    p.add_argument("--protect", nargs="*", default=[], help="Paths the worker must not add, edit or delete.")
    p.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help="Paths owned by concurrent workers or the lead; skipped by the scope check, still protected.",
    )
    p.add_argument("--lenient-scope", action="store_true", help="Report out-of-scope edits without rejecting.")
    p.add_argument("--strict-scope", action="store_true", help=argparse.SUPPRESS)  # strict is now the default
    p.add_argument("--pytest-target", default="tests")
    p.add_argument("--skip-pytest", action="store_true")
    p.add_argument("--holdout-tests", default=None, help="Tests kept outside the worker's view; run by the gate only.")
    p.add_argument("--worker-claim", default="", help="The worker's self-reported status, e.g. PASS.")
    p.add_argument("--failure-mode", nargs="*", default=[], choices=FAILURE_MODES)
    p.add_argument("--evidence", default="", help="Pointer to the log, diff or run that supports the lesson.")
    p.add_argument("--ledger", type=Path, default=None)
    p.add_argument("--lesson", default="")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--code-quality", nargs="+", default=None, help="Run AST code purity & simplicity checks on paths and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.code_quality is not None:
        res = check_code_quality(repo_root, args.code_quality)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1
    protected = [_norm_rel(p) for p in args.protect]

    if args.snapshot_out is not None:
        preexisting_pycs, preexisting_cache_dirs = hygiene_paths(repo_root)
        snap = {
            "date": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "head": _git(repo_root, "rev-parse", "HEAD").stdout.strip(),
            "protected": protected,
            "files": take_snapshot(repo_root),
            "cache_dirs": sorted(preexisting_cache_dirs),
            "pycs": sorted(preexisting_pycs),
        }
        args.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot_out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"SNAPSHOT {len(snap['files'])} files -> {args.snapshot_out}")
        return 0

    if not args.task_id or not args.allow:
        print("error: --task-id and --allow are required unless --snapshot-out is given", file=sys.stderr)
        return 2

    allowed = [_norm_rel(p) for p in args.allow]
    exclude = [_norm_rel(p) for p in args.exclude]
    if args.ledger is not None:
        try:
            exclude.append(_norm_rel(str(args.ledger.resolve().relative_to(repo_root))))
        except ValueError:
            pass  # ledger lives outside the repo
    hygiene_options = {}
    if args.baseline is not None:
        base = json.loads(args.baseline.read_text(encoding="utf-8-sig"))
        hygiene_options = {"ignore_cache_dirs": set(base.get("cache_dirs", [])),
                           "ignore_pycs": set(base.get("pycs", []))}
        protected = sorted(set(protected) | set(base.get("protected", [])))
        changed = changed_since(base["files"], take_snapshot(repo_root))
        mode = "baseline"
    else:
        changed = changed_from_git_status(repo_root)
        mode = "git-status"

    checks = {
        "scope": check_scope(changed, allowed, strict=not args.lenient_scope, mode=mode, exclude=exclude),
        "protected": check_protected(changed, protected, baseline_known=args.baseline is not None, exclude=exclude),
        "targets": check_targets(repo_root, allowed),
        "pytest": {"ok": True, "skipped": True}
        if args.skip_pytest
        else run_pytest(repo_root, args.python, args.pytest_target),
    }
    if args.holdout_tests:
        checks["holdout"] = run_pytest(repo_root, args.python, str(Path(args.holdout_tests).resolve()))
    checks["hygiene"] = check_hygiene(repo_root, **hygiene_options)  # catch caches left by the checks above

    verdict = "ACCEPTED" if all(c["ok"] for c in checks.values()) else "REJECTED"
    result = {
        "date": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task_id": args.task_id,
        "model": args.model,
        "verdict": verdict,
        "worker_claim": args.worker_claim,
        "failure_modes": derive_failure_modes(checks, args.failure_mode, args.worker_claim, verdict),
        "allowed_files": allowed,
        "checks": checks,
        "lesson": args.lesson,
        "evidence": args.evidence,
    }
    if args.ledger is not None:
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        with args.ledger.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if verdict == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
