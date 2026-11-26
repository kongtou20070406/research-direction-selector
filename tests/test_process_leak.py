"""Exercise the bilingual workflow detector and both submission checks."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/paper-write" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from process_leak import find_process_leaks


ZH = (
    "实验围绕核心科学命题组织，正式主表将采用定稿模型与统一配方重跑。"
    "早期混杂不同参数规模的探索数据仅用于设计诊断，不再进入正文主表。"
)
EN = (
    "All confirmatory comparisons … are specified to run in strict IEEE FP32 … "
    "over three seeds; these runs are completed before the submission freeze."
)
FUTURE = "Future work includes evaluating the model on color denoising."
RERUN = "We will rerun the evaluation before submission."


def test_examples_match_and_report_cues():
    plan, exclusion = find_process_leaks(ZH, "zh")
    assert plan["kind"] == "plan" and any(cue.endswith("重跑") for cue in plan["cues"])
    assert exclusion["kind"] == "exclusion"
    assert "仅用于设计诊断" in exclusion["cues"]
    assert plan["sentence"] + exclusion["sentence"] == ZH
    assert find_process_leaks(EN, "en") == [{
        "sentence": EN,
        "cues": ["are specified to run", "before the submission freeze"],
        "kind": "plan",
    }]
    assert find_process_leaks("Preliminary results use two seeds.", "en")[0]["kind"] == "stage"
    assert find_process_leaks("该模型仍为定稿版。", "zh")[0]["kind"] == "stage"


@pytest.mark.parametrize(("sentence", "lang"), [
    ("We evaluate on three seeds.", "en"),
    ("Table 3 reports the mean over three seeds.", "en"),
    ("Future work includes color denoising.", "en"),
    ("本文在三个种子上评测。", "zh"),
    ("We rerun each evaluation with the original settings.", "en"),
    ("本文重跑了全部实验。", "zh"),
    ("We intend to run fast.", "en"),
    ("The first phase of training optimizes the model.", "en"),
    ("Before submission. The data are public.", "en"),
    ("数据已公开。投稿前。", "zh"),
])
def test_ordinary_prose_and_isolated_terms_do_not_match(sentence, lang):
    assert find_process_leaks(sentence, lang) == []


def test_discussion_allowance_is_limited_to_future_research():
    text = f"## Discussion and Limitations\n### Extensions\n{FUTURE}\n{RERUN}\n## Results\n{FUTURE}"
    assert [hit["sentence"] for hit in find_process_leaks(text, "EN")] == [RERUN, FUTURE]
    assert find_process_leaks("## 总结\n未来工作将评测该模型。", "zh") == []


@pytest.mark.parametrize(("sentence", "kind"), [
    ("Rows 2–4 use H1, while row 5 uses H2.", "harness_split"),
    ("The recipe-ablation harness uses a unified runner.", "harness_split"),
    ("It used a different training harness.", "harness_split"),
    ("这里混用了不同训练脚本。", "harness_split"),
    ("Each subsection first identifies a gap and then explains its cause.", "meta_outline"),
    ("In this section we first define the terms, then explain the steps.", "meta_outline"),
    ("本小节首先指出难点，随后说明算法。", "meta_outline"),
])
def test_harness_and_meta_outline_are_direct_process_cues(sentence, kind):
    hit, = find_process_leaks(sentence, None)
    assert hit["kind"] == kind and hit["cues"]


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, "-E", "-s", "-X", "utf8", "-B", str(SCRIPTS / name), *map(str, args)],
        capture_output=True, encoding="utf-8", check=False,
    )


def test_clarity_only_reports_process_in_submission_mode(tmp_path):
    source = tmp_path / "draft.tex"
    source.write_text(
        f"\\section{{Discussion}}\n{FUTURE}\n"
        "\\section{Experiments}\nThe model will\nbe evaluated on three seeds.\n"
        f"\\caption{{{RERUN}}}\n",
        encoding="utf-8",
    )
    regular = run_script("check_sentence_clarity.py", source)
    assert regular.returncode == 0, regular.stdout + regular.stderr
    submission = run_script("check_sentence_clarity.py", "--submission-mode", source)
    assert submission.returncode == 1, submission.stdout + submission.stderr
    assert submission.stdout.count("(process_leak:plan)") == 2
    assert FUTURE not in submission.stdout


def test_paper_gate_checks_tex_prose_and_captions_with_source_lines(tmp_path):
    (tmp_path / "main.tex").write_text(r"\input{body}" + "\n", encoding="utf-8")
    config = tmp_path / "paper_gate.json"
    config.write_text(json.dumps({"main_tex": "main.tex"}), encoding="utf-8")
    (tmp_path / "body.tex").write_text(
        f"\\section{{Discussion}}\n{FUTURE}\n\\section{{Experiments}}\n"
        f"% {RERUN}\n"
        f"\\label{{{RERUN}}}\n"
        f"\\newcommand{{\\workflow}}[1]{{{RERUN}}}\n"
        f"\\caption[{RERUN}]{{Table 3 reports the mean over three seeds.}}\n"
        "\\caption{The model will % split across lines\n% wrapped caption\n"
        "be evaluated on three seeds.}\n"
        "The model will be evaluated on two seeds. % gate-sorry: process — planned pilot\n"
        "% gate-sorry: process — pending replication\n"
        f"{RERUN}\n",
        encoding="utf-8",
    )
    failed = run_script("paper_gate.py", tmp_path)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "errors 1 | sorry 2" in failed.stdout
    assert "process  body.tex:8: [plan] The model will be evaluated on three seeds." in failed.stdout
    assert "process  body.tex:11: [plan]" in failed.stdout
    assert "process  body.tex:13: [plan]" in failed.stdout
    assert "planned pilot" in failed.stdout and "pending replication" in failed.stdout
    assert FUTURE not in failed.stdout

    config.write_text(json.dumps({
        "main_tex": "main.tex", "process_leak_allow": [r"evaluated on three seeds\.$"],
    }), encoding="utf-8")
    allowed = run_script("paper_gate.py", tmp_path)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "errors 0 | sorry 2" in allowed.stdout
    assert "body.tex:8" not in allowed.stdout
    no_sorry = run_script("paper_gate.py", tmp_path, "--no-sorry")
    assert no_sorry.returncode == 1, no_sorry.stdout + no_sorry.stderr
    assert "errors 2 | sorry 0" in no_sorry.stdout
