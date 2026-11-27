import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import doctor


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.fixture
def healthy_stack(tmp_path, monkeypatch):
    def sync(argv):
        assert argv == []
        print("git: main, 0 uncommitted paths, ahead 0 / behind 0 of upstream")
        print("IN SYNC")
        return 0

    def routes(argv):
        assert argv == ["--repo-only"]
        print("REPO CHECK PASS")
        return 0

    monkeypatch.setattr(doctor.sync_skills, "main", sync)
    monkeypatch.setattr(doctor.check_routes, "main", routes)

    project, shared = tmp_path / "project", tmp_path / "shared"
    vault = tmp_path / "vault"
    registry = vault / "project.jsonl"
    write_json(project / ".agents/brief.json", {"knowledge": [str(registry)]})
    write_json(registry, {"id": "K-001", "kind": "practice", "statement": "Use a fixed seed",
                          "scope": "benchmark", "evidence": ["card/001"], "source": "project",
                          "date": "2026-11-24", "status": "active"})
    write_json(vault / "总览刷新记录.jsonl", {"this": "is a refresh log"})
    summary = shared / "KNOWLEDGE.md"
    summary.parent.mkdir()
    summary.write_text("# Knowledge\n", encoding="utf-8")
    write_json(shared / "MANIFEST.json", {
        "files": {summary.name: hashlib.sha256(summary.read_bytes()).hexdigest()}, "max_bytes": 1000,
    })
    return project, shared, vault


def test_doctor_checks_healthy_stack_and_auto_discovers_vault(healthy_stack, capsys):
    project, shared, _ = healthy_stack
    assert doctor.main(["--project", str(project), "--shared", str(shared), "--no-tasks", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"]
    assert {name: check["status"] for name, check in report["checks"].items()} == {
        name: "OK" for name in ("skills", "routes", "brief", "knowledge", "shared", "tasks")
    }
    assert report["checks"]["knowledge"]["details"] == ["1 registries checked"]
    assert report["checks"]["tasks"]["details"] == ["skipped (--no-tasks)"]


@pytest.mark.parametrize("override_data", [False, True])
def test_doctor_vault_sweeps_projects_with_briefs(healthy_stack, capsys, override_data):
    project, shared, vault = healthy_stack
    data = vault / "知识库" / "_数据"
    data.mkdir(parents=True)
    (data / "project.jsonl").write_bytes((vault / "project.jsonl").read_bytes())
    without_brief = vault / "without-brief"
    without_brief.mkdir()
    write_json(data / "项目.json", {
        "With brief": {"root": str(project), "library": "Project library"},
        "Without brief": {"root": "without-brief", "library": "Other library"},
    })
    drafts = vault / "Project library" / "论文草稿"
    drafts.mkdir(parents=True)
    (drafts / "paper.pdf").touch()

    args = ["--vault", str(vault), "--no-tasks", "--json"]
    if override_data:
        args += ["--vault-data", str(shared)]
    assert doctor.main(args) == 0
    checks = json.loads(capsys.readouterr().out)["checks"]
    assert checks["projects"] == {"status": "OK", "details": ["With brief: brief OK, 1 PDF(s)"]}
    assert checks["knowledge"]["details"] == [f"{int(not override_data)} registries checked"]


def test_doctor_fails_for_invalid_vault_and_shared_leaks(healthy_stack, capsys):
    project, shared, vault = healthy_stack
    (vault / "invalid.jsonl").write_text("{broken\n", encoding="utf-8")
    summary = shared / "KNOWLEDGE.md"
    summary.write_text("Reference " + "E:" + "/private/secret.md\n", encoding="utf-8")
    registry = shared / "knowledge/extra.jsonl"
    write_json(registry, {"scope": "Run under /" + "home/researcher/private"})
    write_json(shared / "MANIFEST.json", {
        "files": {path.relative_to(shared).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (summary, registry)},
        "max_bytes": 1000,
    })

    assert doctor.main(["--project", str(project), "--shared", str(shared), "--vault-data", str(vault),
                        "--no-tasks", "--json"]) == 1
    checks = json.loads(capsys.readouterr().out)["checks"]
    assert checks["brief"]["status"] == "OK"
    assert checks["knowledge"]["status"] == "FAIL" and "bad JSON" in checks["knowledge"]["details"][0]
    assert checks["shared"] == {"status": "FAIL", "details": [
        "privacy blocker: local machine path in KNOWLEDGE.md",
        "privacy blocker: local machine path in knowledge/extra.jsonl",
    ]}


@pytest.mark.parametrize(("sync_output", "code", "status"), [
    ("git: main, 0 uncommitted paths, ahead 1 / behind 0 of upstream\nIN SYNC", 0, "WARN"),
    ("git: main, 0 uncommitted paths, ahead 0 / behind 1 of upstream\nIN SYNC", 0, "WARN"),
    ("git: main, 1 uncommitted paths, ahead 0 / behind 0 of upstream\nIN SYNC", 0, "WARN"),
    ("BAD link broken-link\nNOT IN SYNC (1 problems)", 1, "FAIL"),
])
def test_doctor_reports_sync_status(healthy_stack, monkeypatch, capsys, sync_output, code, status):
    def sync(_):
        print(sync_output)
        return code

    monkeypatch.setattr(doctor.sync_skills, "main", sync)
    assert doctor.main(["--no-tasks"]) == code
    output = capsys.readouterr().out
    assert output.startswith("CHECK      STATUS DETAILS\n")
    assert f"skills     {status} " in output


@pytest.mark.parametrize(("last_result", "status"), [("0", "OK"), ("7", "FAIL")])
def test_scheduled_task_result_handling(monkeypatch, last_result, status):
    monkeypatch.setattr(doctor, "TASKS", ("Task_A", "Task_B", "Research_Overview_Weekly"))
    def powershell(args, **kwargs):
        assert "Get-ScheduledTaskInfo" in args[-1]
        assert kwargs["timeout"] == 10
        return SimpleNamespace(returncode=0, stderr="", stdout="\n".join(
            f"{name}|{code}" for name, code in zip(doctor.TASKS, ("267009", "267011", last_result))
        ))

    monkeypatch.setattr(doctor.subprocess, "run", powershell)
    actual, details = doctor._tasks()
    assert actual == status
    if status == "FAIL":
        assert details == [f"Research_Overview_Weekly: LastTaskResult {last_result}"]


def test_doctor_retro_reports_telemetry_and_delta_proposals(healthy_stack, capsys):
    project, _, _ = healthy_stack
    ledger = project / "ledger"
    ledger.mkdir()
    (ledger / "gpt_experience.jsonl").write_text(
        "\n".join([
            json.dumps({"role": "worker", "verdict": "REJECTED", "failure_modes": ["hygiene", "false_claim"]}),
            json.dumps({"role": "worker", "verdict": "ACCEPTED", "failure_modes": []}),
            json.dumps({"role": "redteam", "verdict": "UNDERPOWERED"}),
        ]) + "\n",
        encoding="utf-8",
    )
    (ledger / "experiments.jsonl").write_text(
        json.dumps({"card": "001", "verdict": "PASS"}) + "\n",
        encoding="utf-8",
    )
    assert doctor.main(["--project", str(project), "--retro", "--no-tasks", "--json"]) == 0
    retro = json.loads(capsys.readouterr().out)["checks"]["retro"]
    assert retro["status"] == "WARN"
    joined = "\n".join(retro["details"])
    assert "workers: 1/2 ACCEPTED" in joined
    assert "redteam: 0/1 SUPPORTED" in joined
    assert "delta proposal:" in joined
    assert "never auto-rewrite SKILL.md" in joined
