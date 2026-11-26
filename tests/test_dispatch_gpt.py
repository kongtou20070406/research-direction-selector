import json
from pathlib import Path
import subprocess
import tempfile

import pytest

from scripts import dispatch_gpt


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    (repo / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "protected.py").write_text("READ_ONLY = True\n", encoding="utf-8")
    (repo / "test_worker.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("机械修改 worker.py。\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo, prompt


def arguments(repo, prompt, *extra):
    return ["--repo", str(repo), "--task-id", "T14", "--allow", "worker.py",
            "--prompt-file", str(prompt), *extra]


@pytest.mark.parametrize("tamper", [False, True])
def test_workspace_write_snapshots_dispatches_accepts_and_publishes(workspace, capsys, tamper):
    repo, prompt = workspace
    (repo / ".pytest_cache").mkdir()
    baseline_paths = []
    report = repo / "receipts/report.md"
    ledger = repo / "receipts/workers.jsonl"

    def runner(command, *, input, encoding, timeout, env, **kwargs):
        assert input == f"{dispatch_gpt.SANDBOX_NOTE}\n\n{prompt.read_text(encoding='utf-8')}"
        assert encoding == "utf-8" and timeout == 900
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert "--ephemeral" not in command
        assert 'model_provider="magpie"' in command
        assert 'model_reasoning_effort="high"' in command
        assert command[command.index("-C") + 1] == str(repo)
        assert command[command.index("-s") + 1] == "workspace-write"
        assert command[-1] == "-"
        staged_report = Path(command[command.index("-o") + 1])
        baseline = staged_report.with_name("baseline.json")
        assert not baseline.is_relative_to(repo)
        baseline_paths.append(baseline)
        snapshot = json.loads(baseline.read_text(encoding="utf-8"))
        assert snapshot["protected"] == ["protected.py"]
        assert snapshot["cache_dirs"] == [".pytest_cache"]
        assert not report.exists()
        (repo / "worker.py").write_text("VALUE = 2\n", encoding="utf-8")
        if tamper:
            (repo / "protected.py").write_text("READ_ONLY = False\n", encoding="utf-8")
        staged_report.write_text("Verdict: PASS\n机械修改已完成。\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    rc = dispatch_gpt.main(arguments(repo, prompt, "--protect", "protected.py",
                                     "--pytest-target", "test_worker.py", "--ledger", str(ledger),
                                     "--out-report", str(report)), runner=runner)
    out = capsys.readouterr().out.splitlines()
    assert rc == int(tamper)
    assert len(out) == 5 and out[1] == f"Changed: {2 if tamper else 1}"
    assert out[2:4] == ["Pytest: ok", "Hygiene: ok"]
    assert out[4] == f"Report: {report}"
    assert all(not path.exists() for path in baseline_paths)
    assert "机械修改已完成" in report.read_text(encoding="utf-8")
    row = json.loads(ledger.read_text(encoding="utf-8"))
    assert row["verdict"] == ("REJECTED" if tamper else "ACCEPTED")
    assert row["failure_modes"] == (["false_claim", "scope_creep", "test_tampering"] if tamper else [])


@pytest.mark.parametrize(("extra", "with_note"), [
    ([], True),
    (["--sandbox", "read-only"], False),
    (["--no-sandbox-note"], False),
])
def test_sandbox_note_only_changes_worker_input(tmp_path, monkeypatch, extra, with_note):
    prompt = tmp_path / "prompt.md"
    original = b"\xef\xbb\xbfReview the method.\r\n"
    prompt.write_bytes(original)

    def host_gate(command, **kwargs):
        result = {"checks": {"scope": {"changed": []}}, "verdict": "ACCEPTED", "failure_modes": []}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result))

    def runner(command, *, input, **kwargs):
        prefix = f"{dispatch_gpt.SANDBOX_NOTE}\n\n" if with_note else ""
        assert input == prefix + "Review the method.\n"
        Path(command[command.index("-o") + 1]).write_text("PASS\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(dispatch_gpt.subprocess, "run", host_gate)
    args = arguments(tmp_path, prompt, "--out-report", str(tmp_path / "report.md"), *extra)
    assert dispatch_gpt.main(args, runner=runner) == 0
    assert prompt.read_bytes() == original


def test_read_only_skips_git_snapshot_and_acceptance(tmp_path, capsys):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review only", encoding="utf-8")
    report = tmp_path / "report.md"

    def runner(command, **kwargs):
        assert "--ephemeral" in command and "read-only" in command
        output = Path(command[command.index("-o") + 1])
        assert not output.with_name("baseline.json").exists()
        output.write_text("Read-only findings.\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    args = arguments(tmp_path, prompt, "--sandbox", "read-only", "--out-report", str(report))
    assert dispatch_gpt.main(args, runner=runner) == 0
    assert "Summary: Read-only findings." in capsys.readouterr().out
    assert report.is_file()


def test_dry_run_resolves_commands_without_worker_or_acceptance(workspace, capsys, monkeypatch):
    repo, prompt = workspace
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(repo))

    def fail(*args, **kwargs):
        pytest.fail("dry-run must not launch a subprocess")

    monkeypatch.setattr(subprocess, "run", fail)
    args = arguments(repo, prompt, "--dry-run", "--skip-pytest", "--effort", "medium")
    assert dispatch_gpt.main(args, runner=fail) == 0
    document = json.loads(capsys.readouterr().out)
    assert 'model_reasoning_effort="medium"' in document["codex"]
    gate = document["acceptance"]
    assert "--skip-pytest" in gate and "accept_worker_deliverable.py" in gate[6]
    assert not Path(gate[gate.index("--baseline") + 1]).is_relative_to(repo)


def test_worker_failure_publishes_partial_report_and_exits_nonzero(workspace, capsys):
    repo, prompt = workspace
    report = repo / "partial.md"

    def runner(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text("Partial output", encoding="utf-8")
        raise subprocess.TimeoutExpired(command, 1)

    assert dispatch_gpt.main(arguments(repo, prompt, "--out-report", str(report)), runner=runner) == 1
    assert "timed out" in capsys.readouterr().err
    assert report.read_text(encoding="utf-8") == "Partial output"


@pytest.mark.parametrize("tamper_prose", [False, True])
def test_skill_latex_typeset_inlines_contract_and_blocks_prose_drift(tmp_path, capsys, tamper_prose):
    repo = tmp_path / "paper"
    repo.mkdir()
    (repo / "03_method.tex").write_text("We cut MSE from 0.364 to 0.255.\n", encoding="utf-8")
    (repo / "test_dummy.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Fix layout.", encoding="utf-8")

    def runner(command, *, input, **kwargs):
        assert "[MANDATORY SKILL CONTRACT: latex-typeset]" in input
        assert "venue-recipes.md" in input and "fix-cookbook.md" in input
        if tamper_prose:
            (repo / "03_method.tex").write_text("We cut MSE from 0.350 to 0.255.\n", encoding="utf-8")
        else:
            (repo / "03_method.tex").write_text("\\looseness=-1\nWe cut MSE from 0.364 to 0.255.\n", encoding="utf-8")
        Path(command[command.index("-o") + 1]).write_text("Verdict: PASS\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    rc = dispatch_gpt.main(
        [
            "--repo", str(repo),
            "--task-id", "T-LATEX",
            "--allow", "03_method.tex",
            "--prompt-file", str(prompt),
            "--skill", "latex-typeset",
            "--venue", "icml",
            "--skip-pytest",
            "--out-report", str(tmp_path / "rep.md"),
        ],
        runner=runner,
    )
    out = capsys.readouterr().out
    assert rc == int(tamper_prose)
    if tamper_prose:
        assert "skill_gate_failed" in out and "SkillGate: failed" in out


def test_skill_figure_inlines_contract_and_enforces_verify_cmd(workspace, capsys):
    repo, prompt = workspace

    def runner(command, *, input, **kwargs):
        assert "[MANDATORY SKILL CONTRACT: paper-figure-craft]" in input
        assert "taste-rubric-and-benchmarks.md" in input
        (repo / "worker.py").write_text("VALUE = 2\n", encoding="utf-8")
        Path(command[command.index("-o") + 1]).write_text("Verdict: PASS\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    ok_rc = dispatch_gpt.main(
        arguments(repo, prompt, "--skill", "paper-figure-craft", "--skip-pytest", "--verify-cmd", "python -c \"assert True\""),
        runner=runner,
    )
    assert ok_rc == 0
    capsys.readouterr()

    bad_rc = dispatch_gpt.main(
        arguments(repo, prompt, "--skill", "paper-figure-craft", "--skip-pytest", "--verify-cmd", "python -c \"raise SystemExit(2)\""),
        runner=runner,
    )
    assert bad_rc == 1
    assert "VerifyCmd: failed" in capsys.readouterr().out


def test_unknown_skill_fails_before_runner(workspace, capsys):
    repo, prompt = workspace

    def fail(*args, **kwargs):
        pytest.fail("unknown skill must fail before launching worker")

    rc = dispatch_gpt.main(arguments(repo, prompt, "--skill", "nonexistent-skill"), runner=fail)
    assert rc == 1
    assert "unknown skill: nonexistent-skill" in capsys.readouterr().err
