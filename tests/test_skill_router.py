import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("skill_router", ROOT / "scripts" / "skill_router.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)
TRIG = sr.load_triggers([ROOT / "skills"])


def skills(prompt, cwd=""):
    return [m["skill"] for m in sr.route(prompt, TRIG, cwd)]


def test_every_workflow_skill_has_triggers_and_files_exist():
    assert {"paper-write", "paper-figure-craft", "research-gate"} <= set(TRIG)
    for name, cfg in TRIG.items():
        for rel in cfg["must_read"]:
            assert (cfg["_dir"] / rel).is_file(), (name, rel)


def test_unnamed_writing_prompts_route_to_paper_write():
    for p in ("把 method_notes.md 整理成这篇论文的中文摘要", "Write the opening paragraph of the introduction",
              "帮我改一下 latex/sections_en/00_abstract.tex", "回复一下审稿意见", "润色一下第三节"):
        assert "paper-write" in skills(p), p


def test_other_skills_route():
    assert skills("给方法部分画一张架构图") == ["paper-figure-craft"]
    assert "research-gate" in skills("下一张实验卡怎么设计，消融怎么做")
    assert "research-gate" in skills("看看 cards/048_x.md")


def test_unrelated_prompts_route_nowhere():
    for p in ("今天天气怎么样", "帮我装个 Obsidian 插件", "git push 一下", "列出计划任务"):
        assert skills(p) == [], p


def test_message_lists_must_read_and_exemption():
    text = sr.message(sr.route("改一下摘要", TRIG))
    assert "top-venue-writing-rules.md" in text and "exemplars/index.md" in text
    assert "[RDS Context Hint]" in text and "Suggested reference:" in text and "skip freely" in text


def test_hook_adapters():
    out = sr.claude_hook({"prompt": "把这段引言改短"})
    assert "paper-write" in out["hookSpecificOutput"]["additionalContext"]
    assert sr.claude_hook({"prompt": "hello"}) == {}
    agy = sr.agy_hook({"lastUserInput": "改一下摘要", "invocationNum": "0"})
    assert "paper-write" in agy["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert sr.agy_hook({"lastUserInput": "改一下摘要", "invocationNum": "3"}) == {}


def test_broken_trigger_file_is_skipped(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "triggers.json").write_text("{not json", encoding="utf-8")
    good = tmp_path / "y"
    good.mkdir()
    (good / "triggers.json").write_text(json.dumps({"keywords": ["foo"], "must_read": ["SKILL.md"]}), encoding="utf-8")
    trig = sr.load_triggers([tmp_path])
    assert list(trig) == ["y"] and sr.route("a foo b", trig)[0]["skill"] == "y"


def test_automated_notifications_are_ignored():
    note = "<system-reminder>[SYSTEM NOTIFICATION - NOT USER INPUT] <task-notification>Run baseline eval</task-notification>"
    assert skills(note) == []


def test_paper_gate_talk_does_not_trigger_research_gate():
    assert skills("LaTeX 草稿被新门禁拦下，应该允许加 sorry") == ["paper-write"]
    assert "research-gate" in skills("这张卡过不了关口 A，怎么办")


def test_reference_engine_fused_kernel_auto_triggers():
    for prompt in (
        "给 RTX 5090 写个 Triton 融合算子加速",
        "帮我用 torch.compile 优化 engine/ 下这个循环层",
        "先写原生 PyTorch 参考实现，再按 L4 写 CUDA kernel",
        "帮我跑个实验试试这个想法",
        "上卡开训 Push-T",
        "run an experiment on L4",
    ):
        assert "research-gate" in skills(prompt), prompt
    msg = sr.message(sr.route("帮我跑个实验试试这个想法", TRIG))
    assert "reference/" in msg and "engine/" in msg and "数值等价" in msg
    assert "research-gate" not in skills("只估一下这个模型的理论最低时延")


def test_workflow_modification_auto_triggers():
    for prompt in (
        "帮我优化一下工作流，顺便改条规则",
        "帮我审视工作流哪里太重",
    ):
        assert "research-gate" in skills(prompt), prompt
    msg = sr.message(sr.route("帮我优化一下工作流，顺便改条规则", TRIG))
    assert "workflow-evolution-rules.md" in msg


def test_secondary_root_auto_triggers_companion_skill(tmp_path):
    companion = tmp_path / "perf-ruler"
    companion.mkdir()
    (companion / "triggers.json").write_text(
        json.dumps(
            {
                "keywords": ["理论(最低)?时延|proofline|perf[-_ ]?ruler"],
                "must_read": ["SKILL.md"],
                "then": "先零 GPU 测理论下界。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = sr.load_triggers([sr.SOURCE_ROOT, tmp_path])
    matched = [r["skill"] for r in sr.route("只估一下这个模型的理论最低时延", loaded)]
    assert matched == ["perf-ruler"]
