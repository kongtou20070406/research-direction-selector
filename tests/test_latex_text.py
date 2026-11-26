"""Exercise LaTeX extraction, source gates, and both read-only freeze prompts."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/paper-write/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


formal = load("verify_manuscript_formal")
clarity = load("check_sentence_clarity")
review = load("freeze_review")
latex = load("latex_text")


@pytest.fixture
def draft(tmp_path):
    for lang, heading, prose in (
        ("en", "Abstract", r"We report \textbf{a score of 0.125} and 85\%."),
        ("zh", "摘要", r"本文报告得分0.125以及85\%。"),
    ):
        directory = tmp_path / "latex" / f"sections_{lang}"
        directory.mkdir(parents=True)
        section = f"\\section{{{heading}}}\\label{{sec:result}}\n\n{prose}\n"
        (directory / "00_abstract.tex").write_text(section, encoding="utf-8")
        legacy = tmp_path / f"sections_{lang}"
        legacy.mkdir()
        (legacy / "00_abstract.md").write_text("TODO\n", encoding="utf-8")
    return tmp_path


def test_latex_text_preserves_numbers_and_math_and_flattens_tables():
    source = r"""
\section[Short]{Results for \emph{model 2}}
A \textbf{\emph{0.125} score} rose to 85\%. % dropped 97.77 \label{ignored}
Cite \citep*[see {the summary}][p. 2]{paper2024} and \eqref{eq:39.5}.
Follow \hyperref[sec:39.5]{the result of 0.375}.
\begin{align*}
  x &= \frac{1}{2} + 0.25 \label{eq:kept} \\
  y &= 45\%
\end{align*}
Inline \(z = 1.25\) and \[z = 2.75\].
\begin{tabularx}{\linewidth}{ll}
\toprule
Name & Score \\[0.5ex]
\midrule
Model \& co & \textbf{12.5\%} \\
\multicolumn{2}{c}{A \& B score $\lvert x\rvert > 0.30$} \\
Nested & $\begin{aligned} x & = 0.9 \\ y &= 0.8 \end{aligned}$ \\
\bottomrule
\end{tabularx}
"""
    text = latex.latex_to_text(source)
    assert "Results for model 2" in text
    assert latex.section_labels(source) == {"eq:kept"}
    assert "A 0.125 score rose to 85%." in text
    assert "Follow the result of 0.375." in text
    assert all(s not in text for s in ("97.77", "paper2024", "39.5", "0.5ex", r"\label", r"\textbf"))
    assert r"$x &= \frac{1}{2} + 0.25" in text
    assert r"y &= 45\%$" in text
    assert "$z = 1.25$" in text and "$z = 2.75$" in text
    assert "Name | Score\nModel & co | 12.5%" in text
    assert r"A & B score $\lvert x\rvert > 0.30$" in text
    assert r"Nested | $\begin{aligned} x & = 0.9 \\ y &= 0.8 \end{aligned}$" in text


def test_verbatim_is_readable_in_brief_and_not_linted():
    source = "\\begin{Verbatim}[breaklines]\nx = 1 % 0.875\n\\label{verbatim}\n\\end{Verbatim}"
    assert latex.latex_to_text(source) == r"x = 1 % 0.875" + "\n" + r"\label{verbatim}"
    assert latex.section_labels(source) == set()
    assert clarity._clean_prose_lines(latex.latex_to_text(source, for_checks=True), join_wrapped=True) == []


def test_formal_defaults_to_tex_and_markdown_can_be_selected(draft):
    assert formal.verify_manuscript(draft)[0] == []
    assert formal.verify_manuscript(draft)[1]["sections"] == 1
    assert any("TODO" in e for e in formal.verify_manuscript(draft, fmt="md")[0])


def test_formal_accepts_analysis_heading_in_latex_theory_section(draft):
    en = draft / "latex/sections_en"
    for section in formal.STANDARD_METHOD_FIRST_SECTIONS:
        path = en / Path(section).with_suffix(".tex")
        if not path.exists():
            title = {"03_method.md": "Method", "04_theory.md": "Stability Analysis"}.get(section, section)
            path.write_text(f"\\section{{{title}}}\n", encoding="utf-8")
    assert formal.check_section_order_and_headings(en, "EN", fmt="tex")[0] == []


@pytest.mark.parametrize(("change", "expected"), [
    ("missing", "Section file mismatch"),
    ("label", "LaTeX label mismatch"),
    ("decimal", "LaTeX numeric multiset mismatch"),
    ("percentage", "LaTeX numeric multiset mismatch"),
    ("multiplicity", "LaTeX numeric multiset mismatch"),
])
def test_formal_reports_latex_parity_errors(draft, change, expected):
    zh = draft / "latex/sections_zh/00_abstract.tex"
    if change == "missing":
        zh.unlink()
    else:
        before = zh.read_text(encoding="utf-8")
        after = {
            "label": before.replace("sec:result", "sec:other"),
            "decimal": before.replace("0.125", "0.126"),
            "percentage": before.replace(r"85\%", r"86\%"),
            "multiplicity": before + "\n0.125",
        }[change]
        zh.write_text(after, encoding="utf-8")
    errors, _ = formal.verify_manuscript(draft)
    assert len(errors) == 1
    assert expected in errors[0]


def test_headline_bindings_use_both_latex_sections_and_legacy_paths(draft):
    errors, stats = formal.verify_manuscript(draft, require_headline_bindings=True)
    assert stats["headline_numbers"] == 4
    assert all("Unbound headline number" in error for error in errors)
    assert len(errors) == 4

    (draft / "measurements.json").write_text('{"score": 0.125, "accuracy": 0.85}', encoding="utf-8")
    rows = []
    for filename in ("sections_en/00_abstract.md", "latex/sections_zh/00_abstract.tex"):
        for key, display, digits, scale in (("score", "0.125", 3, 1), ("accuracy", "85%", 0, 100)):
            rows.append(dict(file=filename, display=display, ledger="measurements.json",
                             key=key, round=digits, scale=scale))
    bindings = draft / "bindings.json"
    bindings.write_text(json.dumps(rows), encoding="utf-8")
    errors, stats = formal.verify_manuscript(draft, bindings, require_headline_bindings=True)
    assert errors == []
    assert stats["bindings"] == stats["headline_numbers"] == 4


def test_latex_digest_binds_original_bytes_and_ignores_markdown(draft):
    original = formal.compute_manuscript_digest(draft)
    (draft / "sections_en/00_abstract.md").write_text("Legacy edit", encoding="utf-8")
    assert formal.compute_manuscript_digest(draft) == original
    rel = "latex/sections_en/00_abstract.tex"
    src = draft / rel
    raw = src.read_bytes()
    assert formal.section_digests(draft)[rel] == hashlib.sha256(raw).hexdigest()
    src.write_bytes(raw + b"% reviewer invisible\r\n")
    assert latex.latex_to_text(raw.decode()) == latex.read_section(src)
    assert formal.compute_manuscript_digest(draft) != original


def test_clarity_reads_both_latex_languages_and_checks_wrapped_sentences(draft):
    reports = clarity.audit_sections(draft, submission_mode=True)
    assert reports["EN"]["en_count"] == reports["ZH"]["zh_count"] == 1
    assert all(not r["violations"] for r in reports.values())
    zh = draft / "latex/sections_zh/00_abstract.tex"
    zh.write_text(r"\section{摘要}" + "\n" + ("这些结果表明方法可以稳定预测变化" * 3 + "\n") * 2 + "。", encoding="utf-8")
    reports = clarity.audit_sections(draft, submission_mode=True)
    assert reports["EN"]["violations"] == []
    assert reports["ZH"]["violations"][0]["reasons"][0].startswith("zh_len=")
    assert clarity.audit_file(zh)["violations"] == reports["ZH"]["violations"]


def test_freeze_latex_dry_run_and_fake_rereview(draft, tmp_path):
    digest = formal.compute_manuscript_digest(draft)
    reports = tmp_path / "reports"
    dry = reports / "dry"

    def never_call(*args):
        pytest.fail("Dry-run called a model")

    assert review.run(draft, digest, dry, runner=never_call, dry_run=True) == []
    for name in ("prompt_audit.md", "prompt_editorial.md"):
        prompt = (dry / name).read_text(encoding="utf-8")
        assert "Source: bilingual `.tex` sections" in prompt
        assert "We report a score of 0.125 and 85%." in prompt
        assert "本文报告得分0.125以及85%。" in prompt
        assert all(s not in prompt for s in ("TODO", r"\textbf", r"\label"))
    assert not (draft / ".review_store").exists()

    calls = []
    rel_en = "latex/sections_en/00_abstract.tex"
    rel_zh = "latex/sections_zh/00_abstract.tex"

    def fake_runner(prompt, model, cwd, out, effort, timeout):
        calls.append((model, prompt))
        items = [dict(rule="numbers", scope=scope, exact_quote="0.125", evidence_ref="result", verdict="PASS")
                 for scope in (rel_en, rel_zh)]
        return json.dumps({"verdict": "PASS", "items": items, "findings": []})

    first = reports / "first"
    assert review.run(draft, digest, first, runner=fake_runner) == []
    assert {model for model, _ in calls} == {review.AUDITOR, review.EDITOR}
    for name in ("audit.json", "editorial.json"):
        report = json.loads((first / name).read_text(encoding="utf-8"))
        assert report["manuscript_sha256"] == digest
        assert report["section_sha256"] == formal.section_digests(draft)

    src = draft / rel_zh
    src.write_text(src.read_text(encoding="utf-8").replace("本文报告", "我们报告"), encoding="utf-8")
    assert review.run(draft, digest, reports / "stale", runner=never_call)
    second = reports / "second"
    assert review.run(draft, formal.compute_manuscript_digest(draft), second,
                      prior_audit=first / "audit.json", runner=fake_runner) == []
    brief = (second / "brief.md").read_text(encoding="utf-8")
    assert f"{rel_zh} (diff)" in brief
    assert "-本文报告" in brief and "+我们报告" in brief
    assert "We report a score" not in brief
