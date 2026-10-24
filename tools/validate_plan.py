"""Map a diff to the risk class of the change, and to the evidence that class needs.

Risk is about **what** you touched, not how much you wrote. A large new feature is
R2 and only needs a mapped test; a one-line edit to a verifier is R0 and needs a
pre-registered protocol plus an independent recompute. Line counts are still
reported, but as advice, never as a gate.

Read-only. Does not import the inspected code and does not run tests.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

R0, R1, R2, R3 = "R0", "R1", "R2", "R3"
SEVERITY = {R0: 0, R1: 1, R2: 2, R3: 3}

# Certification assets. A mistake here invalidates every downstream conclusion.
R0_MODULES = {
    "rds_cas", "rds_tms_store", "rds_result_record", "rds_quick_ledger",
    "rds_project", "rds_verify", "rds_verify_registry", "rds_builtin_verifiers",
    "rds_formal_kernel", "rds_lean_verify", "rds_scalar_verify", "rds_proof_cache",
    "rds_checkpoints", "rds_costs", "rds_receipt", "rds_run_records",
}
R0_PREFIXES = ("rds_verify", "rds_*_verify")
# Decision logic: changes here shift which route is chosen.
R1_MODULES = {
    "rds_compass", "rds_compass_model", "rds_compass_declaration",
    "rds_compass_store", "rds_compass_drive", "rds_compass_rsi",
    "rds_advisor", "rds_autonomy", "rds_autonomy_admission", "rds_discrimination",
    "rds_adversary", "rds_feasibility", "rds_frontier_proposals", "rds_global_planning",
}

EVIDENCE = {
    R0: ["pre-registered judgement protocol, written before the run",
         "reverse test: the new behaviour disappears when the new logic is disabled",
         "independent recompute of the affected hash / receipt / budget"],
    R1: ["a comparison arm and a holdout assertion",
         "the same command before and after, with both outputs retained"],
    R2: ["a test module mapped to every changed script",
         "import_graph upward/cycles must not increase; a new module must be classified"],
    R3: [],
}

TEST_IMPORT = re.compile(r"^\s*(?:from|import)\s+(rds_\w+)", re.MULTILINE)


def git(root, *args):
    out = subprocess.run(["git", "-C", str(root), *args],
                         capture_output=True, text=True, encoding="utf-8")
    return out.stdout if out.returncode == 0 else ""


def untracked(root):
    """A new module is exactly the case that must not slip through, so untracked
    files are first-class here. `git diff` alone would miss them."""
    out = git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    return [p.replace("\\", "/") for p in out if p.strip()]


def _count_lines(root, path):
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


def diff_stats(root, base):
    rows, per_file = [], {}
    for line in git(root, "diff", "--name-status", base).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        rows.append((parts[0][:1], parts[-1].replace("\\", "/")))
    seen = {p for _, p in rows}
    for path in untracked(root):
        if path not in seen:
            rows.append(("A", path))
            seen.add(path)
    for line in git(root, "diff", "--numstat", base).splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        try:
            per_file[cols[2].replace("\\", "/")] = (
                0 if cols[0] == "-" else int(cols[0]), 0 if cols[1] == "-" else int(cols[1]))
        except ValueError:
            per_file[cols[2]] = (0, 0)
    for path in seen:
        if path not in per_file:
            per_file[path] = (_count_lines(root, path), 0)
    return rows, per_file


def test_map(root):
    mapping = {}
    for path in sorted((root / "tests").glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names = set(TEST_IMPORT.findall(text))
        mapping[path.name] = names
    return mapping


def risk_of(path):
    """One path -> one risk class. Judged by what the file is, not its size."""
    name = Path(path).name
    stem = name[:-3] if name.endswith(".py") else name
    if path.startswith("docs/") and name.endswith(".py"):
        return R3, "one-off analysis"
    if name.endswith(".md"):
        return R3, "prose"
    if path.startswith(("tools/", ".github/", "examples/", "formal/")):
        return R3, "tooling or fixture"
    if path.startswith("tests/"):
        return R1, "test contract may be weakened"
    if path.startswith("scripts/"):
        if stem in R0_MODULES or any(stem.startswith(p.rstrip("*")) for p in R0_PREFIXES):
            return R0, "certification asset"
        if stem in R1_MODULES:
            return R1, "decision logic"
        return R2, "capability"
    if path.startswith("benchmark/"):
        return R2, "benchmark harness"
    if name.endswith((".py", ".rs")):
        return R2, "capability"
    return R3, "other"


def plan(root, base, evidence):
    rows, per_file = diff_stats(root, base)
    mapping = test_map(root)
    levels, files, notes, tests_needed = [], [], [], set()
    added = deleted = 0
    for status, path in rows:
        a, d = per_file.get(path, (0, 0))
        added, deleted = added + a, deleted + d
        level, why = risk_of(path)
        levels.append(level)
        files.append({"path": path, "risk": level, "why": why, "added": a, "deleted": d,
                      "status": "deleted" if status == "D" else "changed"})
        name = Path(path).name
        if level in (R0, R1, R2) and path.startswith("scripts/") and name.endswith(".py"):
            module = name[:-3]
            hits = {t for t, names in mapping.items() if module in names}
            if hits:
                tests_needed |= hits
            elif level == R2:
                notes.append(f"NO_TEST_MAPPED: scripts/{name} — R2 requires one")
            else:
                notes.append(f"no test mapped: scripts/{name} — {level} needs a named check")
    overall = min(levels, key=lambda x: SEVERITY[x]) if levels else R3
    required = EVIDENCE[overall]
    missing = []
    if overall in (R0, R1):
        if not evidence:
            missing.append(f"{overall} needs an evidence pointer (--evidence <path or PR section>)")
        elif not Path(root / evidence).exists() and not evidence.startswith("PR:"):
            missing.append(f"evidence path not found: {evidence}")
    if any(n.startswith("NO_TEST_MAPPED") for n in notes):
        missing.append("a changed script has no mapped test")
    verdict = "ready" if not missing else "evidence_missing"
    return {
        "base": base, "overall": overall, "required_evidence": required,
        "files": files, "tests_to_run": sorted(tests_needed),
        "added": added, "deleted": deleted, "net_loc": added - deleted,
        "advice": {"net_loc_hint": 250, "note": "advisory only; R2 has no LOC gate"},
        "notes": notes, "missing": missing, "verdict": verdict,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--evidence", default="", help="path to a protocol/receipt, or PR:<section>")
    parser.add_argument("--strict", action="store_true",
                        help="fail on missing evidence; use this in CI, not locally")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(args.root.resolve(), args.base, args.evidence)
    except (OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f"plan failed: {error}\n")
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(f"base      : {result['base']}")
        print(f"overall   : {result['overall']}   (R0 most severe .. R3 no behaviour)")
        print(f"size      : {len(result['files'])} files, {result['net_loc']:+d} net "
              f"(+{result['added']}/-{result['deleted']}) -- advisory only")
        for item in result["files"]:
            print(f"  {item['risk']}  {item['path']:<46} {item['why']}")
        if result["required_evidence"]:
            print("required evidence:")
            for line in result["required_evidence"]:
                print(f"  - {line}")
        if result["tests_to_run"]:
            print("tests to run:")
            for name in result["tests_to_run"]:
                print(f"  - {name}")
        if result["notes"]:
            print("notes:")
            for line in result["notes"]:
                print(f"  - {line}")
        if result["missing"]:
            print("missing:")
            for line in result["missing"]:
                print(f"  - {line}")
        print(f"verdict   : {result['verdict']}")
    if args.strict and result["verdict"] != "ready":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
