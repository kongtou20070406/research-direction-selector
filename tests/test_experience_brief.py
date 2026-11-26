import json
from pathlib import Path

import pytest

from scripts import experience_brief, skill_router


PROMPT = "alpha beta"
CHECKLIST = "- 开卡先查\n- 结卡沉淀"
STATUS = "- 当前状态"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def configure(root, cfg):
    write(root / ".agents/brief.json", json.dumps(cfg, ensure_ascii=False))


def write_status(root, extra=""):
    write(root / "STATUS.md", f"- 区外\n<!-- START extra -->\n{STATUS}{extra}\n正文\n<!-- END -->\n- 区外\n")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    cwd = root / "cards/active"
    cwd.mkdir(parents=True)
    cfg = {
        "checklist": CHECKLIST,
        "status": {"file": "STATUS.md", "start": "<!-- START", "end": "<!-- END"},
        "knowledge": ["knowledge.jsonl", str(tmp_path / "shared.jsonl")],
        "decisions": {"file": "DECISIONS.md", "recent": 2, "match": 1},
        "memory_dir": "memory",
    }
    configure(root, cfg)
    write_status(root)
    for name, eid, kind, statement in [
        (root / "knowledge.jsonl", "K-001", "dead_end", "alpha 旧路线"),
        (tmp_path / "shared.jsonl", "X-001", "asset", "alpha beta 新资产"),
    ]:
        write(name, json.dumps({"id": eid, "kind": kind, "statement": statement,
                                "scope": "试验范围", "status": "active", "evidence": ["card/001"],
                                "source": "test", "date": "2026-01-01"}))
    write(root / "DECISIONS.md", "\n".join([
        "| 日期 | 决定 | 解释 |",
        "| --- | --- | --- |",
        "| 2026-01-01 | 旧决定 | alpha beta 隐藏解释 |",
        "| 2026-01-02 | 无关决定 | 备注 |",
        "| 2026-01-03 | 倒数第二条 | 备注 |",
        "| 2026-01-04 | 最新 alpha beta " + "长" * 200 + " | 备注 |",
    ]))
    for name, kind, description, body in [
        ("a", "feedback", "只凭正文命中", "alpha beta 私有正文"),
        ("b", "feedback", "alpha 描述命中", "私有正文"),
        ("c", "feedback", "alpha 其次", ""),
        ("d", "feedback", "alpha 淘汰", ""),
        ("e", "project", "alpha beta 排除类型", ""),
    ]:
        write(root / f"memory/{name}.md",
              f"---\nname: {name}\ndescription: {description}\nmetadata:\n  type: {kind}\n---\n\n{body}")
    return root, cwd, cfg


def test_find_config_walks_up(project):
    root, cwd, _ = project
    assert experience_brief.find_config(cwd) == root / ".agents/brief.json"


def test_check_config_and_cli_use_project_config(project, monkeypatch, capsys):
    root, cwd, _ = project
    assert experience_brief.check_config(cwd) == (True, [])
    monkeypatch.chdir(cwd)
    assert experience_brief.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == f"BRIEF CHECK OK ({root / '.agents/brief.json'})"


@pytest.mark.parametrize(("failure", "message"), [
    ("missing_registry", "knowledge file not found"),
    ("bad_jsonl", "bad JSON"),
    ("wrong_jsonl_type", "expected a JSON object"),
    ("placeholder", "placeholder statement not allowed"),
    ("missing_status_marker", "missing STATUS markers"),
    ("missing_decision", "no valid dated decision table row"),
    ("missing_memory", "memory_dir is not a directory"),
    ("bad_config", "Expecting property name"),
])
def test_check_config_reports_issue(project, capsys, failure, message):
    root, cwd, cfg = project
    registry = root / "knowledge.jsonl"
    if failure == "missing_registry":
        registry.unlink()
    elif failure == "bad_jsonl":
        write(registry, "{unfinished\n")
    elif failure == "wrong_jsonl_type":
        write(registry, "null\n")
    elif failure == "placeholder":
        entry = json.loads(registry.read_text(encoding="utf-8"))
        write(registry, json.dumps({**entry, "statement": "（待结卡填）"}, ensure_ascii=False))
    elif failure == "missing_status_marker":
        write(root / "STATUS.md", "<!-- START -->\n- status\n")
    elif failure == "missing_decision":
        write(root / "DECISIONS.md", "| 日期 | 决定 |\n| 2026-02-30 | bad date |\n")
    elif failure == "missing_memory":
        configure(root, {**cfg, "memory_dir": "missing"})
    elif failure == "bad_config":
        write(root / ".agents/brief.json", "{unfinished\n")

    ok, issues = experience_brief.check_config(cwd)
    assert not ok and any(message in issue for issue in issues)
    assert experience_brief.main(["--check", str(cwd)]) == 1
    assert capsys.readouterr().out.startswith("BRIEF CHECK FAIL\n")


def test_check_config_reports_project_without_config(tmp_path):
    ok, issues = experience_brief.check_config(tmp_path)
    assert not ok and ".agents/brief.json not found" in issues[0]


def test_knowledge_denoising_preserves_technical_hits(project):
    root, cwd, _ = project
    rows = [
        ("K-001", "训练卡 1 和 2"),
        ("K-002", "提升 工作 问题"),
        ("K-010", "bs=128 × 3000 步导致欠训练"),
        ("K-011", "lam_inv 未越过 T64 门槛"),
        ("K-012", "采用 Uniform GDN 后收缩门控更稳定"),
    ]
    write(root / "knowledge.jsonl", "\n".join(json.dumps({
        "id": eid, "kind": "dead_end", "statement": statement, "scope": "试验",
        "status": "active", "evidence": ["card/001"], "source": "test", "date": "2026-01-01",
    }, ensure_ascii=False) for eid, statement in rows))

    casual = experience_brief.build("1,2去做吧 我希望提升工作流的自动化和舒适度", cwd)
    assert "知识库（" not in casual
    assert "K-011" in experience_brief.build("下一步把 lam_inv 再细扫一遍", cwd)
    assert "K-010" in experience_brief.build("改成 bs=128、3000 步", cwd)
    assert "K-012" in experience_brief.build("想看看收缩门控", cwd)


def test_build_includes_ranked_sections(project):
    _, cwd, _ = project
    brief = experience_brief.build(PROMPT, cwd)
    assert brief == experience_brief.build(PROMPT, cwd)
    markers = [CHECKLIST, STATUS, "与本轮相关的禁区不要重复", "K-001［禁区］alpha 旧路线（试验范围）",
               "X-001［跨项目资产］alpha beta 新资产（试验范围）", "2026-01-04｜最新", "2026-01-03｜倒数第二条",
               "2026-01-01｜旧决定", "a｜只凭正文命中", "b｜alpha 描述命中", "c｜alpha 其次"]
    positions = [brief.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert brief.count("2026-01-04") == 1
    decision = next(line.split("｜", 1)[1] for line in brief.splitlines() if line.startswith("- 2026-01-04"))
    assert len(decision) == 160
    for excluded in ("区外", "正文\n", "隐藏解释", "无关决定", "淘汰", "排除类型", "私有正文"):
        assert excluded not in brief
    assert brief.endswith(experience_brief.REMINDER)


@pytest.mark.parametrize(("omitted", "trimmed", "preserved"), [
    ((), "2026-01-01", ("a｜只凭正文命中", "c｜alpha 其次", "K-001", "X-001")),
    (("decisions",), "c｜alpha 其次", ("a｜只凭正文命中", "K-001", "X-001")),
    (("decisions", "memory_dir"), "X-001", ("K-001",)),
])
def test_length_and_trimming_order(project, omitted, trimmed, preserved):
    root, cwd, cfg = project
    configure(root, {key: value for key, value in cfg.items() if key not in omitted})
    baseline = experience_brief.build(PROMPT, cwd)
    extra = "状" * (1201 - len(baseline))
    write_status(root, extra)
    configure(root, cfg)
    brief = experience_brief.build(PROMPT, cwd)
    assert len(brief) <= 1200
    assert CHECKLIST in brief and STATUS + extra in brief
    assert brief.endswith(experience_brief.REMINDER)
    assert trimmed not in brief
    for expected in preserved:
        assert expected in brief
    if "decisions" in omitted:
        assert "研究者的决定" not in brief
    if "memory_dir" in omitted:
        assert "过往反馈" not in brief


def test_oversized_fixed_sections_still_fit(project):
    root, cwd, cfg = project
    configure(root, {**cfg, "checklist": CHECKLIST + "规" * 1200})
    write_status(root, "状" * 1200)
    brief = experience_brief.build(PROMPT, cwd)
    assert len(brief) <= 1200
    assert CHECKLIST in brief and STATUS in brief
    assert brief.endswith(experience_brief.REMINDER)


def test_automated_or_unconfigured_has_no_brief(project, tmp_path):
    _, cwd, _ = project
    assert experience_brief.build(f"{PROMPT} <task-notification>", cwd) == ""
    assert experience_brief.build(PROMPT, tmp_path / "other") == ""


@pytest.mark.parametrize("mode", ["claude", "agy_cwd", "agy_workspace"])
def test_hooks_combine_independent_context(project, tmp_path, monkeypatch, mode):
    _, cwd, _ = project
    triggers = tmp_path / "skills/sample/triggers.json"
    write(triggers, '{"keywords": ["alpha"]}')
    monkeypatch.setenv("SKILL_ROUTER_ROOTS", str(triggers.parent.parent))

    def run_hook(prompt, directory):
        if mode == "claude":
            result = skill_router.claude_hook({"prompt": prompt, "cwd": str(directory)})
            return result["hookSpecificOutput"]["additionalContext"]
        payload = {"lastUserInput": prompt, "invocationNum": 0, "workspacePaths": [str(directory)]}
        if mode == "agy_cwd":
            payload.update(cwd=str(directory), workspacePaths=[str(tmp_path)])
        assert skill_router.agy_hook({**payload, "invocationNum": 1}) == {}
        result = skill_router.agy_hook(payload)
        return result["injectSteps"][0]["systemMessage"]["systemMessage"]

    combined = run_hook(PROMPT, cwd)
    assert combined.index("[RDS Context Hint]") < combined.index(experience_brief.TITLE)
    assert combined.endswith(experience_brief.build(PROMPT, cwd))
    assert run_hook("无匹配", cwd) == experience_brief.build("无匹配", cwd)
    skill_only = run_hook(PROMPT, tmp_path)
    assert "[RDS Context Hint]" in skill_only and experience_brief.TITLE not in skill_only

    def fail():
        raise RuntimeError("brief unavailable")

    monkeypatch.setattr(skill_router, "_brief_builder", fail)
    assert run_hook(PROMPT, cwd) == skill_only


def test_mode_header_and_validation(project):
    root, cwd, cfg = project
    configure(root, {**cfg, "mode": "paper"})
    assert experience_brief.check_config(cwd) == (True, [])
    brief = experience_brief.build(PROMPT, cwd)
    assert "项目模式：专题成文（paper）" in brief

    configure(root, {**cfg, "mode": "invalid"})
    ok, issues = experience_brief.check_config(cwd)
    assert not ok and any("mode" in issue for issue in issues)


def test_focus_string_and_program_json(project):
    root, cwd, cfg = project
    configure(root, {**cfg, "mode": "program", "focus": "本周主攻方法节"})
    assert experience_brief.check_config(cwd) == (True, [])
    brief = experience_brief.build(PROMPT, cwd)
    assert "项目模式：长期科学探索（program）" in brief
    assert "当前焦点：本周主攻方法节" in brief

    (root / "program.json").write_text(
        json.dumps({"focus": {"note": "当前主攻主论文；W1-W7 暂搁置"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    configure(root, {**cfg, "mode": "program", "focus": {"program": "program.json"}})
    assert experience_brief.check_config(cwd) == (True, [])
    brief = experience_brief.build(PROMPT, cwd)
    assert "当前焦点：当前主攻主论文；W1-W7 暂搁置" in brief

    configure(root, {**cfg, "mode": "program", "focus": {"program": "missing.json"}})
    ok, issues = experience_brief.check_config(cwd)
    assert not ok and any("focus.program" in issue for issue in issues)
