"""Use RDS to execute and inspect a development iteration; CPU, no API calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
PATTERNS = ["test_rds_artifacts.py", "test_rds_costs.py", "test_rds_experiments.py",
            "test_rds_rsi.py", "test_rds_project.py", "test_rds_checkpoints.py", "test_rds_development_cli.py",
            "test_rds_frontier.py", "test_rds_frontier_cli.py", "test_rds_frontier_proposals.py",
            "test_rds_frontier_history.py", "test_rds_advancement.py", "test_rds_advancement_cli.py",
            "test_rds_research_note.py", "test_rds_research_note_cli.py", "test_rds_history_preflight.py"]

# Only versioned public inputs enter the development copy. Never copy the
# checkout's Git metadata, local host configuration or private research state.
PUBLIC_DIRECTORIES = {"scripts", "tests", "references", "examples", "benchmark",
                      "docs", "formal", "native", "integrations", "agents", ".github"}
PUBLIC_ROOT_FILES = {"SKILL.md", "LICENSE", "README.md", "README.zh-CN.md", "README.ja-JP.md",
                     "CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md", "requirements-formal.txt"}
EXCLUDED_PARTS = {".rds", ".git", ".lake", "target", "__pycache__", ".venv", "node_modules",
                  ".codex", ".agents", ".env", "dist"}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(source, *arguments, input=None):
    # Do not let inherited Git variables redirect the source/index or inject a
    # repository configuration. The independent index has no copied remotes.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "--literal-pathspecs", "-C", str(source), *arguments],
                          input=input, env=env, check=True, capture_output=True, timeout=30)


def freeze_public_source(source, root):
    """Copy current bytes of tracked public files, with a fresh package index."""
    entries = []
    for record in git(source, "ls-files", "--stage", "-z").stdout.decode("utf-8").split("\0"):
        if not record:
            continue
        metadata, name = record.split("\t", 1)
        mode, blob, stage = metadata.split()
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("Invalid tracked source path: " + name)
        if relative.parts[0] not in PUBLIC_DIRECTORIES and name not in PUBLIC_ROOT_FILES:
            continue
        if (EXCLUDED_PARTS.intersection(relative.parts) or relative.name.startswith(".env")
                or relative.suffix in {".pyc", ".pyo"}):
            continue
        if stage != "0" or mode not in {"100644", "100755"}:
            raise ValueError("Unmerged or non-regular public source: " + name)
        original = source / relative
        if (any(source.joinpath(*relative.parts[:end]).is_symlink()
                for end in range(1, len(relative.parts) + 1))
                or not original.is_file() or not original.resolve().is_relative_to(source.resolve())):
            raise ValueError("Missing, linked or escaping public source: " + name)
        entries.append({"path": relative.as_posix(), "index_blob": blob, "mode": mode,
                        "sha256": file_sha(original)})
    if not entries:
        raise ValueError("No tracked public source files")
    for entry in entries:
        target = root / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / entry["path"], target)
        if file_sha(target) != entry["sha256"]:
            raise ValueError("Public source changed while copying: " + entry["path"])
    try:
        head = git(source, "rev-parse", "--verify", "HEAD").stdout.decode("ascii").strip()
    except subprocess.CalledProcessError:
        head = None  # An index-only development source has no accepted commit.
    snapshot = {"schema": "rds-development-source-v1", "source_kind": "git-index-working-tree",
                "source_head": head, "files": entries,
                "git_metadata": "new local index; original metadata/configuration not copied"}
    write(root / "source-snapshot.json", snapshot)
    git(root, "init", "--quiet", "--template=")
    paths = b"\0".join(entry["path"].encode("utf-8") for entry in entries) + b"\0"
    git(root, "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "add", "--force",
        "--pathspec-from-file=-", "--pathspec-file-nul", input=paths)
    return entries


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="New empty development workspace; original source is read-only")
    parser.add_argument("--all-tests", action="store_true", help="Run every public test module, including benchmark-backed checks")
    parser.add_argument("--test-pattern", action="append", help="Run a named public test module; repeat for a bounded repair iteration")
    args = parser.parse_args()
    if args.all_tests and args.test_pattern:
        parser.error("Choose --all-tests or --test-pattern")
    patterns = (args.test_pattern or (sorted(p.name for p in (REPO / "tests").glob("test_*.py"))
                                     if args.all_tests else PATTERNS))
    if any(Path(name).name != name or not name.startswith("test_") or not name.endswith(".py")
           or not (REPO / "tests" / name).is_file() for name in patterns):
        parser.error("Each --test-pattern must name an existing public test_*.py module")
    patterns = list(dict.fromkeys(patterns))
    root = Path(args.workspace).resolve()
    if root == REPO or root.is_relative_to(REPO):
        parser.error("Choose a workspace outside this checkout to avoid recursive source copying")
    if root.exists() and any(root.iterdir()):
        parser.error("Use a new empty workspace; existing research state is never overwritten")
    root.mkdir(parents=True, exist_ok=True)
    sources = freeze_public_source(REPO, root)
    missing = [name for name in patterns if not (root / 'tests' / name).is_file()]
    if missing:
        parser.error('Selected test module is absent from frozen Git source; stage it before running: ' + ', '.join(missing))
    shutil.copyfile(root / "examples/self-development/test_driver.py", root / "test_driver.py")
    config = {"test_patterns": patterns, "goal": "Validate actual RDS development behavior", "scientific_claim": None}
    write(root / "development-config.json", config)
    # Dynamic fixtures, native locks, documents and package inputs all belong
    # to this workload's identity, regardless of how a test imports them.
    bindings = [{"path": item["path"], "sha256": item["sha256"],
                 "role": "code" if Path(item["path"]).suffix in {".py", ".mjs", ".lean", ".rs"} else "data"}
                for item in sources]
    bindings.extend({"path": path, "sha256": file_sha(root / path), "role": "data"}
                    for path in ("source-snapshot.json", ".git/index", ".git/config"))
    bindings.extend({"path": path, "sha256": file_sha(root / path), "role": role}
                    for path, role in (("development-config.json", "config"), ("test_driver.py", "evaluator")))
    # Use the runner's documented role-hash convention, from the exact source copy.
    sys.path.insert(0, str(root / "scripts"))
    from rds_project import ProjectStore
    protocol = {role + "_sha256": ProjectStore._role_sha({"bindings": bindings}, role) for role in ("code", "config", "data")}
    protocol.update(data_split="development-regression", init="fresh-process", seed="not-applicable",
                    checkpoint="none", schedule="one-regression-pass", sample_work=patterns,
                    numeric_protocol="python-unittest; counts", metric={"definition": "unittest failure and error count", "reduction": "count"})
    write(root / "development-protocol.json", protocol)
    bindings.append({"path": "development-protocol.json", "sha256": file_sha(root / "development-protocol.json"), "role": "protocol"})
    argv = [sys.executable, "-B", "test_driver.py"]
    contract = {"schema": 1, "description": "Use RDS to validate RDS development; no scientific quality score",
                "bindings": bindings, "allowed_commands": [argv], "output_roots": ["out"], "budget": {"wall_seconds": 120}}
    write(root / "contract.json", contract)
    manifest = {"schema": 1, "id": "development-check", "arm": "tool", "argv": argv,
                "protocol": {"path": "development-protocol.json", "sha256": file_sha(root / "development-protocol.json")},
                "outpaths": ["out/test-results.json"], "timeout_seconds": 90, "resource_estimates": {"wall_seconds": 90}}
    write(root / "run.json", manifest)
    decisions = {"question": "Can this iteration be integrated or must a concrete regression be repaired?",
                 "goal": config["goal"], "candidate": "integrate only after recorded regression acceptance",
                 "pending_evidence": ["raw test output", "rule replay acceptance"], "source_kind": "DEVELOPMENT_INPUT"}
    write(root / "decision.json", decisions)
    cli = root / "scripts" / "rds_cli.py"
    def run(label, *arguments, allow_failure=False):
        completed = subprocess.run([sys.executable, "-B", str(cli), "--root", str(root), *arguments],
                                   cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=120)
        write(root / "out" / (label + ".json"), {"argv": list(arguments), "exit_code": completed.returncode,
                                                 "stdout": completed.stdout, "stderr": completed.stderr})
        if completed.returncode and not allow_failure:
            raise RuntimeError(completed.stderr or completed.stdout)
        return json.loads(completed.stdout)
    run("01-init", "project", "init", "--mode", "quick", "--contract", "contract.json")
    run("02-register", "project", "create", "--manifest", "run.json")
    run("03-checkpoint", "checkpoint", "save", "--id", "before-check", "--decision", "decision.json")
    receipt = run("04-execution", "project", "execute", "--id", "development-check", allow_failure=True)
    write(root / "out/receipt.json", receipt)
    run("05-costs", "project", "costs")
    run("06-restoration", "checkpoint", "restore", "--id", "before-check")
    binding = {"run_id": receipt["run_id"], **protocol}
    imports = {"schema": "rds-artifact-manifest-v1", "decision": "next-development-step", "sources": [
        {"id": "actual-test-counts", "kind": "metric", "path": "out/test-results.json",
         "expected_sha256": file_sha(root / "out/test-results.json"), "binding": binding,
         "facts": [{"id": "failure_count", "pointer": "/failure_count"}, {"id": "case_count", "pointer": "/case_count"}]},
        {"id": "actual-execution-receipt", "kind": "receipt", "path": "out/receipt.json",
         "expected_sha256": file_sha(root / "out/receipt.json"), "binding": binding, "facts": []}],
        "cost_bindings": [{"action_id": "review-next-change", "run_id": receipt["run_id"],
                           "resource": "wall_seconds", "comparison_group": "same-development-protocol"}]}
    write(root / "imports.json", imports)
    action = {"id": "review-next-change", "kind": "READ_ONLY_REVIEW", "description": "Read the next scoped change and its acceptance evidence",
              "competing_explanations": ["recorded software checks pass", "an untested behavior still requires evidence"],
              "required_observables": ["original regression output", "independent rule replay"],
              "outcomes": [{"observation": "bound independent cases accept the change", "next_decision": "integrate the scoped change"},
                           {"observation": "a missing or failing case is found", "next_decision": "repair the concrete gap"}]}
    failed_action = {**action, "id": "inspect-test-failures", "description": "Read the exact failing case and traceback before changing code"}
    graph = {"schema": 1, "nodes": [{"id": "development-regression", "sources": ["out/test-results.json; development-only"],
             "executable": {"decisions": ["next-development-step"],
                 "preconditions": [{"fact": "failure_count", "op": "eq", "value": 0, "on_false": failed_action}], "action": action}}], "edges": []}
    write(root / "development-graph.json", graph)
    imported = run("07-import", "artifacts", "import", "--manifest", "imports.json")
    advice = run("08-advice", "advise", "--artifacts", "imports.json", "--graph", "development-graph.json")
    # The rule proposal is a finite software regression example, never a sealed
    # scientific benchmark. Keep it on a separate graph with an explicit rollback.
    rsi_dir = root / "examples/rsi"
    isolated = root / "isolated-rules.json"
    shutil.copyfile(rsi_dir / "base-graph.json", isolated)
    evaluation = run("09-rule-replay", "meta", "evaluate-rule", "--rule", str(rsi_dir / "candidate-rule.json"),
                     "--graph", str(isolated), "--cases", str(rsi_dir / "cases.json"), "--output", "out/evaluation.json", allow_failure=True)
    adoption = None
    if receipt["run_status"] == "SUCCEEDED" and evaluation["adoption_eligible"]:
        adoption = run("10-isolated-adoption", "meta", "apply-rule", "--rule", str(rsi_dir / "candidate-rule.json"),
                       "--graph", str(isolated), "--cases", str(rsi_dir / "cases.json"), "--evaluation", "out/evaluation.json", "--force")
        record = adoption.get("record_path", adoption.get("record"))
        if not isinstance(record, str):
            raise RuntimeError("Adoption did not provide an explicit rollback record path")
        run("11-rollback", "meta", "rollback-rule", "--record", record, "--graph", str(isolated))
    observations = json.loads((root / "out/test-results.json").read_text(encoding="utf-8"))
    test_summary = {key: observations[key] for key in ("case_count", "failure_count", "skipped_count")}
    test_summary["failing_cases"] = [failure["case_id"] for failure in observations["failures"]]
    print(json.dumps({"workspace": str(root), "run_status": receipt["run_status"],
                      "assessment": receipt["assessment"], "test_observations": test_summary,
                      "artifact_status": imported["status"], "rule_replay_status": evaluation["status"],
                      "rule_cases_evaluated": evaluation["cases_evaluated"], "rule_case_counts": evaluation.get("counts"),
                      "isolated_adoption": {key: adoption[key] for key in ("status", "record_path", "backup_path")} if adoption else None,
                      "advice_record": "out/08-advice.json", "research_policy_gain_measured": False},
                     ensure_ascii=False, indent=2))
    return 0 if receipt["run_status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
