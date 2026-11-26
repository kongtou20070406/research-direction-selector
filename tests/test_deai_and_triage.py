"""De-AI thresholds and the structured Lead review handoff."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/paper-write/scripts"
sys.path.insert(0, str(SCRIPTS))
from check_sentence_clarity import _audit_text
from review_triage import build_brief


def lint(raw, **kwargs):
    return _audit_text(Path("paper.md"), raw, **kwargs)


def cli(script, *args):
    return subprocess.run(
        [sys.executable, "-E", "-s", "-X", "utf8", "-B", str(SCRIPTS / script), *map(str, args)],
        capture_output=True, encoding="utf-8", check=False,
    )


def test_comma_budgets_and_overrides(tmp_path):
    source = tmp_path / "paper.md"
    raw = "We test A, B, C; retest D, and report E.\n\n我们对比甲，测量乙；报告丙，汇总丁。"
    result = lint(raw)
    assert result["en_soft_commas"] == result["zh_soft_commas"] == 1
    assert not result["violations"]
    assert len(lint(raw, max_commas=2)["violations"]) == 2
    assert len(lint(raw, en_max_commas=3)["violations"]) == 1
    assert len(lint(raw, zh_max_commas=2)["violations"]) == 1
    source.write_text(raw, encoding="utf-8")
    assert cli("check_sentence_clarity.py", source).returncode == 0
    assert cli("check_sentence_clarity.py", source, "--en-max-commas", 3).returncode == 1
    assert cli("check_sentence_clarity.py", source, "--zh-max-commas", 2).returncode == 1
    assert cli("check_sentence_clarity.py", source, "--max-commas", 2).returncode == 1


@pytest.mark.parametrize(("sentence", "reason"), [
    ("To stabilize the model, we track error.", "ai_taste"),
    ("We enforce exact invariants.", "ai_taste"),
    ("Both tasks iterate on the same state.", "ai_taste"),
    ("我们学得动态参数。", "translationese"),
    ("为稳定性，先构造距离则可得结论。", "translationese"),
    ("我们在状态空间上迭代求解。", "translationese"),
    ("约束保证每一步严格可行。", "translationese"),
    ("我们执行记分反事实查询。", "translationese"),
    ("另见对称排版。", "translationese"),
    ("模型使用零点归零。", "translationese"),
])
def test_new_patterns_are_advisory_until_promoted(sentence, reason):
    report = lint(sentence)
    assert not report["violations"]
    assert report["advisories"][0]["reasons"][0].startswith(reason)
    strict = lint(sentence, strict_deai=True)
    assert strict["violations"] == report["advisories"] and not strict["advisories"]


def test_rhythm_uses_paragraphs_and_strict_cli(tmp_path):
    source = tmp_path / "paper.md"
    en = "We measure the input.\nWe update the state.\nWe measure the output."
    zh = "我们测量输入。我们更新状态。我们测量输出。"
    raw = en + "\n\n" + zh
    assert [hit["lang"] for hit in lint(raw)["advisories"]] == ["EN", "ZH"]
    assert not lint(en.replace("\n", "\n\n"))["advisories"]
    source.write_text(raw, encoding="utf-8")
    assert cli("check_sentence_clarity.py", source).returncode == 0
    strict = cli("check_sentence_clarity.py", source, "--strict-deai")
    assert strict.returncode == 1 and "telegraphic_rhythm" in strict.stdout


def test_triage_projects_only_actionable_facts_and_limits(tmp_path):
    audit = {
        "verdict": "FAIL",
        "score": "4/10",
        "affirmative_core": "GPT-generated manuscript prose",
        "regime_boundary": "Only the matched regime has three seeds.",
        "findings": [
            {"id": "F1", "scope": "05_experiments.tex", "category": "number_mismatch", "severity": "major",
             "evidence": {"ref": "Table 1", "observed": 0.5, "expected": 0.6, "prose": "GPT rewrite"},
             "resolution": "Use the value in the ledger. Rewrite the abstract as follows: proposed prose."},
            {"id": "F2", "severity": "minor", "category": "scope_caveat", "evidence": "Only small grids tested."},
            {"id": "F3", "severity": "minor", "category": "missing_control", "evidence": "Weak reject."},
        ],
    }
    editorial = {"summary": "Reject this paper.", "findings": [
        {"id": "E1", "severity": "major", "type": "clarification", "resolution": "Dilute the abstract."},
        {"id": "E2", "severity": "critical", "type": "confirmed_flaw", "evidence": "Equation 2.\n4/10",
         "resolution": "Strong reject. Check Eq. 2."},
    ]}
    brief = build_brief(audit, editorial)
    assert [line for line in brief.splitlines() if line.startswith("## ")] == [
        "## MUST_FIX (Objective Defects Only)",
        "## LIMITATION_BOUNDARY (§7 Only — Do NOT Dilute Abstract/Intro)",
        "## QUARANTINED_TONE (Excluded From Author Context)",
    ]
    assert "| F1 | 05_experiments.tex | number_mismatch | ref=Table 1 / observed=0.5 / expected=0.6 |" in brief
    assert "| E2 | — | confirmed_flaw | Equation 2. | Check Eq. 2. |" in brief
    assert "Only small grids tested." in brief and "Only the matched regime has three seeds." in brief
    assert "Count: 2" in brief and "IDs: F3, E1" in brief
    for excluded in ("4/10", "reject", "GPT", "proposed prose", "Dilute the abstract."):
        assert excluded not in brief

    audit_file, editorial_file, output = [tmp_path / name for name in ("audit.json", "editorial.json", "brief.md")]
    audit_file.write_text(json.dumps(audit), encoding="utf-8")
    editorial_file.write_text(json.dumps(editorial), encoding="utf-8")
    result = cli("review_triage.py", "--audit", audit_file, "--editorial", editorial_file, "--out", output)
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == brief
