"""Behavioural tests for the worker acceptance gate, using throwaway git repos."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "research-gate" / "scripts" / "accept_worker_deliverable.py"
_spec = importlib.util.spec_from_file_location("accept_worker_deliverable", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "mod.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    (root / "src" / "other.py").write_text("Y = 1\n", encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text(
        "from src.mod import f\n\ndef test_f():\n    assert f(1) == 2\n", encoding="utf-8"
    )
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = gate.main(list(argv))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.lstrip().startswith("{") else {"raw": out})


def _snapshot(repo: Path, tmp_path: Path, capsys, *protect: str) -> Path:
    base = tmp_path / "base.json"
    code, _ = _run(capsys, "--repo-root", str(repo), "--snapshot-out", str(base), "--protect", *protect)
    assert code == 0
    return base


def test_baseline_ignores_preexisting_dirt(repo, tmp_path, capsys):
    (repo / "src" / "other.py").write_text("Y = 2  # lead's uncommitted work\n", encoding="utf-8")
    base = _snapshot(repo, tmp_path, capsys, "tests")
    (repo / "src" / "mod.py").write_text("def f(x):\n    return 1 + x\n", encoding="utf-8")
    code, res = _run(
        capsys, "--repo-root", str(repo), "--baseline", str(base), "--task-id", "t1",
        "--allow", "src/mod.py", "--skip-pytest",
    )
    assert code == 0, res
    assert res["checks"]["scope"]["changed"] == ["src/mod.py"]


def test_baseline_hygiene_ignores_preexisting_cache_but_checks_new_artifacts(repo, tmp_path, capsys):
    cache = repo / ".pytest_cache"
    cache.mkdir()
    (cache / "old.pyc").write_bytes(b"preexisting")
    base = _snapshot(repo, tmp_path, capsys)
    assert json.loads(base.read_text(encoding="utf-8"))["cache_dirs"] == [".pytest_cache"]
    args = ("--repo-root", str(repo), "--task-id", "cache", "--allow", "src/mod.py",
            "--worker-claim", "PASS", "--skip-pytest")
    code, result = _run(capsys, *args, "--baseline", str(base))
    assert code == 0
    assert result["checks"]["hygiene"]["preexisting_cache_dirs"] == [".pytest_cache"]
    assert result["checks"]["hygiene"]["ok"] and "false_claim" not in result["failure_modes"]

    assert _run(capsys, *args)[0] == 1  # Git-status mode still checks all hygiene.
    (cache / "new.pyc").write_bytes(b"new")
    (repo / ".hypothesis").mkdir()
    code, result = _run(capsys, *args, "--baseline", str(base))
    assert code == 1
    assert result["checks"]["hygiene"]["pyc_count"] == 1
    assert result["checks"]["hygiene"]["stray_cache_dirs"] == [".hypothesis"]


def test_out_of_scope_edit_rejects_by_default(repo, tmp_path, capsys):
    base = _snapshot(repo, tmp_path, capsys)
    (repo / "src" / "mod.py").write_text("def f(x):\n    return x + 1\n\n", encoding="utf-8")
    (repo / "src" / "other.py").write_text("Y = 3\n", encoding="utf-8")
    code, res = _run(
        capsys, "--repo-root", str(repo), "--baseline", str(base), "--task-id", "t2",
        "--allow", "src/mod.py", "--skip-pytest",
    )
    assert code == 1
    assert res["checks"]["scope"]["out_of_scope"] == ["src/other.py"]
    assert "scope_creep" in res["failure_modes"]


def test_exclude_covers_concurrent_worker_paths(repo, tmp_path, capsys):
    base = _snapshot(repo, tmp_path, capsys)
    (repo / "src" / "mod.py").write_text("def f(x):\n    return x + 1  # w1\n", encoding="utf-8")
    (repo / "src" / "other.py").write_text("Y = 4  # w2\n", encoding="utf-8")
    code, res = _run(
        capsys, "--repo-root", str(repo), "--baseline", str(base), "--task-id", "w1",
        "--allow", "src/mod.py", "--exclude", "src/other.py", "--skip-pytest",
    )
    assert code == 0, res


def test_new_conftest_under_protected_dir_is_tampering(repo, tmp_path, capsys):
    base = _snapshot(repo, tmp_path, capsys, "tests")
    (repo / "src" / "mod.py").write_text("def f(x):\n    return 0\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text(
        "def pytest_collection_modifyitems(items):\n    items.clear()\n", encoding="utf-8"
    )
    code, res = _run(
        capsys, "--repo-root", str(repo), "--baseline", str(base), "--task-id", "t3",
        "--allow", "src/mod.py", "tests/conftest.py", "--worker-claim", "PASS", "--skip-pytest",
    )
    assert code == 1
    assert res["checks"]["protected"]["touched"] == ["tests/conftest.py"]
    assert {"test_tampering", "false_claim"} <= set(res["failure_modes"])


def test_git_status_fallback_expands_untracked_dirs(repo, capsys):
    (repo / "newpkg").mkdir()
    (repo / "newpkg" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "newpkg" / "b.py").write_text("B = 1\n", encoding="utf-8")
    code, res = _run(
        capsys, "--repo-root", str(repo), "--task-id", "t4", "--allow", "newpkg/a.py", "--skip-pytest",
    )
    assert code == 1
    assert res["checks"]["scope"]["out_of_scope"] == ["newpkg/b.py"]


def test_gate_reruns_tests_and_holdout(repo, tmp_path, capsys):
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    (holdout / "test_hidden.py").write_text(
        "from src.mod import f\n\ndef test_negative():\n    assert f(-1) == 0\n\ndef test_big():\n    assert f(10**6) == 10**6 + 1\n",
        encoding="utf-8",
    )
    base = _snapshot(repo, tmp_path, capsys, "tests")
    # Special-cased fix: passes the visible test, fails the hidden ones.
    (repo / "src" / "mod.py").write_text("def f(x):\n    return 2 if x == 1 else 0\n", encoding="utf-8")
    code, res = _run(
        capsys, "--repo-root", str(repo), "--baseline", str(base), "--task-id", "t5",
        "--allow", "src/mod.py", "--holdout-tests", str(holdout), "--python", sys.executable,
    )
    assert res["checks"]["pytest"]["ok"] is True
    assert res["checks"]["holdout"]["ok"] is False
    assert code == 1
    assert "verification_failure" in res["failure_modes"]


def test_ledger_row_is_structured_and_ledger_path_is_not_scope_creep(repo, tmp_path, capsys):
    base = _snapshot(repo, tmp_path, capsys)
    (repo / "src" / "mod.py").write_text("def f(x):\n    return x + 1  # ok\n", encoding="utf-8")
    ledger = repo / "ledger" / "exp.jsonl"
    code, _ = _run(
        capsys, "--repo-root", str(repo), "--baseline", str(base), "--task-id", "t6", "--allow", "src/mod.py",
        "--skip-pytest", "--ledger", str(ledger), "--lesson", "kept scope", "--evidence", "diff@t6",
    )
    assert code == 0
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert row["verdict"] == "ACCEPTED" and row["failure_modes"] == [] and row["evidence"] == "diff@t6"
    # A second acceptance against the same baseline must not count the ledger file as out of scope.
    code2, res2 = _run(
        capsys, "--repo-root", str(repo), "--baseline", str(base), "--task-id", "t7", "--allow", "src/mod.py",
        "--skip-pytest", "--ledger", str(ledger),
    )
    assert code2 == 0, res2


def test_exclude_also_covers_protected_paths_owned_by_the_lead():
    changed = ["tests/test_new_contract.py", "tests/conftest.py"]
    res = gate.check_protected(changed, ["tests"], True, exclude=["tests/test_new_contract.py"])
    assert not res["ok"] and res["touched"] == ["tests/conftest.py"]
    assert gate.check_protected(["tests/test_new_contract.py"], ["tests"], True, exclude=["tests/test_new_contract.py"])["ok"]
