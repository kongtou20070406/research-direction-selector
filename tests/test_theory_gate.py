import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/paper-write/scripts"
sys.path.insert(0, str(SCRIPTS))
import theory_gate


@pytest.fixture
def paper(tmp_path):
    def put(name, text):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path
    return tmp_path, put


def issues(paper, cfg=None, **kwargs):
    return theory_gate.check_paper(paper[0], cfg, **kwargs)


def names(report):
    return [line.split()[0] for line in report["errors"]]


def cli(script, paper_dir, *args):
    return subprocess.run(
        [sys.executable, "-E", "-s", "-X", "utf8", "-B", str(SCRIPTS / script), str(paper_dir), *args],
        capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )


def test_intuition_checks_preceding_paragraph_and_skips_appendix(paper):
    paper[1]("sections_en/02_method.tex", r"""
\section{Method}
\begin{equation} x = 1 \end{equation}

An update has no effect after the state reaches equilibrium.

\begin{theorem}
The trajectory is bounded by $B$.
\end{theorem}

The expression $x + y + z + a + b + c + d + e + f$ holds.
\begin{proposition}
The state is nonnegative, $x\geq 0$.
\end{proposition}

% This commented sentence is not intuition.
\begin{theorem} We have $x=0$. \end{theorem}

\appendix
\begin{proposition} The expanded result is $x\geq 0$. \end{proposition}
""".lstrip())
    result = issues(paper)
    assert names(result) == ["intuition", "intuition"]
    assert "sections_en/02_method.tex:11:" in result["errors"][0]
    assert "sections_en/02_method.tex:16:" in result["errors"][1]


def test_symbol_budget_counts_active_symbols_and_nested_scripts(paper):
    paper[1]("main.tex", r"""
The recurrence approaches its fixed point.
\begin{theorem}
For $x_{i_{j_k}}$ and \(\alpha, x, y, \State\), use
\begin{equation*} \frac{y}{x} = \alpha + \text{when defined}. \end{equation*}
\end{theorem}
""".lstrip())
    result = issues(paper, {"symbols": {"x": "", "i": "", "j": "", "k": "", "alpha": "", "y": ""}})
    assert names(result) == ["symbol_budget"]
    error = result["errors"][0]
    assert "7 active symbols exceeds 5" in error and "3 levels" in error
    assert r"undeclared symbols: \State" in error and "frac" not in error


def test_symbols_at_limit_are_allowed_and_repeated_symbols_count_once(paper):
    paper[1]("main.tex", r"""
The recurrence settles under a fixed input.
\begin{proposition}
For $x_{i_j}$ and $\alpha$ we have \[ x = \sqrt{y + \alpha} + x. \]
\end{proposition}
""".lstrip())
    assert not issues(paper, {"symbols": dict.fromkeys(["x", "i", "j", r"\alpha", "y"])})["errors"]


def test_proof_sketch_counts_items_without_double_counting_and_display_math(paper):
    source = paper[1]("main.tex", r"""
\begin{proof}
\begin{enumerate}
\item Apply the bound, e.g. on the next iterate.
\item Derive the update.
\end{enumerate}
\[
x = y .
\]
\begin{align*} x &= y \\ y &= z \end{align*}
$$ x = z $$
\end{proof}
\appendix
\begin{proof}
One. Two. Three. Four. Five. Six.
\end{proof}
""".lstrip())
    cfg = {"max_proof_steps": 2}
    result = issues(paper, cfg)
    assert names(result) == ["proof_sketch"]
    assert "2 steps (max 2), 3 display environments (max 2)" in result["errors"][0]
    assert not issues(paper, {**cfg, "max_proof_equations": 3})["errors"]
    source.write_text(source.read_text(encoding="utf-8").replace("Derive the update.", "Derive the update. Explain it."),
                      encoding="utf-8")
    assert "3 steps (max 2)" in issues(paper, {**cfg, "max_proof_equations": 3})["errors"][0]


def test_method_equations_count_numbered_rows_in_method_only(paper):
    paper[1]("main.tex", r"""
\section{A Method for Updating States}
\begin{equation} x = 1 \end{equation}
\begin{equation*} x = 1 \end{equation*}
\[ x = 1 \]
\begin{align}
 x &= 1 \\
 y &= 2 \notag \\
 z &= \begin{aligned} 1 + 2 \\ + 3 \end{aligned} \\
 a &= 3
\end{align}
\begin{gather} a = 1 \end{gather}
\section{Results}
\begin{equation} y = 2 \end{equation}
\appendix
\section{Method Appendix}
\begin{equation} z = 3 \end{equation}
""".lstrip())
    assert names(issues(paper)) == ["method_equations"]
    assert "5 numbered equations exceeds 4" in issues(paper)["errors"][0]
    assert not issues(paper, {"max_method_equations": 5})["errors"]


def test_configured_method_file_and_sources(paper):
    paper[1]("chapters/architecture.tex", r"""
\section{Architecture}
\begin{equation} x = 1 \end{equation}
\begin{equation} y = 2 \end{equation}
\section{Limitations}
We cannot conclude transfer to a new regime.
""".lstrip())
    paper[1]("unselected.tex", r"\begin{proof} One. Two. Three. Four. Five. Six. \end{proof}")
    cfg = {"sources": ["chapters/*.tex"], "method_files": ["chapters/architecture.tex"], "max_method_equations": 1}
    assert names(issues(paper, cfg)) == ["method_equations"]


@pytest.mark.parametrize(("figure", "heading"), [
    ("fig1_method_comparison.tex", r"\section{Method illustration}"),
    ("figures/overview_diagram.tex", r"\section{Method illustration}"),
    ("overview_diagram.tex", ""),
])
def test_method_equation_budget_excludes_figure_sources(paper, figure, heading):
    paper[1]("sections/03_method.tex", r"""
\section{Method}
\begin{equation} x = 1 \end{equation}
\begin{equation} y = 2 \end{equation}
""" + rf"\input{{{figure}}}")
    paper[1](f"sections/{figure}", heading + r"""
\begin{tikzpicture}
\node {\begin{equation} z = 3 \end{equation}};
\end{tikzpicture}
""")
    cfg = {"sources": ["sections/03_method.tex"], "max_method_equations": 2}
    assert names(issues(paper, cfg)).count("method_equations") == 0


def test_abstract_lead_supports_inputs_inline_formatting_and_chinese(paper):
    paper[1]("main.tex", r"\begin{abstract}\input{nested/summary}\end{abstract}")
    summary = paper[1]("nested/summary.tex",
                       r"The task uses 3.5 units, e.g. in one run. We compare updates. \emph{Power Loop} improves stability.")
    cfg = {"method_name": "Power Loop"}
    assert not issues(paper, cfg)["errors"]
    summary.write_text(r"The task uses 3.5 units. We compare updates. The state settles. Power Loop improves stability.",
                       encoding="utf-8")
    assert "nested/summary.tex:1:" in issues(paper, cfg)["errors"][0]
    summary.write_text("本研究分析递推过程。实验采用两个基线。我们提出循环模型。", encoding="utf-8")
    assert not issues(paper, {"method_name": "循环模型"})["errors"]


@pytest.mark.parametrize("gap", ["sorry", "axiom"])
def test_lean_bindings_verify_declarations_comments_gaps_and_evidence(paper, gap):
    paper[1]("main.tex", "\\label{thm:step}\n")
    lean = paper[1]("lean/Step.lean", """/- theorem fake (/- sorry -/) axiom -/
-- axiom fake : True
lemma step : True := by trivial
""")
    binding = {"tex_label": "thm:step", "lean_file": "lean/Step.lean", "lean_theorem": "step",
               "method_eq": "eq:update", "probe_evidence": "probe.jsonl#4"}
    assert not issues(paper, {"theorems": [binding]})["errors"]
    assert "lemma 'fake' is missing" in issues(paper, {"theorems": [{**binding, "lean_theorem": "fake"}]})["errors"][0]
    lean.write_text(lean.read_text(encoding="utf-8") + f"\n{gap}\n", encoding="utf-8")
    cfg = {"theorems": [{**binding, "method_eq": " ", "probe_evidence": ""}]}
    error, = issues(paper, cfg)["errors"]
    assert error.startswith("lean_bindings  main.tex:1:")
    assert f"contains {gap}" in error and "method_eq is empty" in error and "probe_evidence is empty" in error
    error, = issues(paper, {"theorems": [{**binding, "tex_label": "missing", "lean_file": "none.lean"}]})["errors"]
    assert "TeX label 'missing' is missing" in error and "Lean file 'none.lean' is missing" in error


def test_glossary_flags_only_chinese_source_and_ignores_comments(paper):
    paper[1]("sections_en/01_intro.tex", "An English section uses Alias.")
    paper[1]("sections_zh/01_intro.tex", "% Alias 短词\n用短词表示循环状态。\n")
    result = issues(paper, {"zh_glossary": {"递推状态": ["短词", "Alias"]}})
    error, = result["errors"]
    assert "zh_glossary  sections_zh/01_intro.tex:2:" in error
    assert "递推状态" in error and "短词" in error


def test_hedges_are_bilingual_and_exclude_discussion_limitations_and_appendix(paper):
    paper[1]("sections_en/01.tex", r"""
\begin{abstract}
This is \emph{merely a testbed}.
\end{abstract}
\section{Introduction}
This is only a
diagnostic tool.
\section{Method}
We cannot conclude a narrow conclusion.
\begin{equation} x = 1 \end{equation}
\section{Discussion and Method Boundaries}
This is only a preliminary attempt.
\section{Limitations}
This is merely a testbed.
\appendix
\section{Method}
This is only a diagnostic tool.
""".lstrip())
    paper[1]("sections_zh/01.tex", r"""
\section{引言}
这只能视为初步尝试。
\section{方法}
尚不足以证明更多。
\begin{equation} x = 1 \end{equation}
\section{局限性}
仅作为测试工具。
""".lstrip())
    errors = issues(paper)["errors"]
    assert len(errors) == 6 and all(e.startswith("defensive_hedge") for e in errors)
    assert any("只能视为初步尝试" in e for e in errors)
    assert all("only a preliminary attempt" not in e and "仅作为测试工具" not in e for e in errors)


def test_gate_sorry_same_and_preceding_line_and_strict_cli(paper):
    root, put = paper
    put("main.tex", r"""
\section{Introduction}
Cannot conclude. % gate-sorry: defensive_hedge — Quoted reviewer text
% gate-sorry: symbol_budget, intuition — Retained for traceability
\begin{theorem} $a + b + c + d + e + f = 0$. \end{theorem}
""".lstrip())
    cfg = put("theory.json", '{"sources":["main.tex"]}')
    result = cli("theory_gate.py", root, "--config", str(cfg), "--json")
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["sorries"]) == 3
    result = cli("theory_gate.py", root, "--config", str(cfg), "--json", "--no-sorry")
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert len(report["errors"]) == 3 and not report["sorries"]
    assert all("sorry not allowed" in msg for msg in report["errors"])


def test_paper_gate_is_opt_in_and_merges_errors_and_sorries(paper):
    root, put = paper
    put("main.tex", r"\input{sections_en/01_intro}")
    source = put("sections_en/01_intro.tex", "\\section{Introduction}\nIt is merely a testbed.\n")
    cfg = put("paper_gate.json", '{"main_tex":"main.tex"}')
    result = cli("paper_gate.py", root)
    assert result.returncode == 0 and "defensive_hedge" not in result.stdout
    cfg.write_text('{"main_tex":"main.tex","theory_gate":{"sources":["sections_en/*.tex"]}}', encoding="utf-8")
    result = cli("paper_gate.py", root)
    assert result.returncode == 1 and "defensive_hedge" in result.stdout
    source.write_text("\\section{Introduction}\nIt is merely a testbed. TODO % gate-sorry: defensive_hedge, placeholder — Quoted text\n",
                      encoding="utf-8")
    result = cli("paper_gate.py", root)
    assert result.returncode == 0, result.stdout + result.stderr
    report = (root / "paper_gate_report.md").read_text(encoding="utf-8")
    assert "errors 0 | sorry 2" in report and "matches no error" not in report
    result = cli("paper_gate.py", root, "--no-sorry")
    assert result.returncode == 1 and "errors 2 | sorry 0" in result.stdout


def test_abstract_budget_and_intro_overlap(paper):
    values = "0.123, 0.3434, 1.323, 7.891"
    paper[1]("sections_en/00_abstract.tex",
             rf"The scores are {values}, 8\%, 20\%, and 90\%." + "\n% 99.999\n")
    paper[1]("sections_en/01_introduction.tex", r"\section{Introduction}" + "\nWe report " + values)
    report = issues(paper, {"max_abstract_words": 4})
    assert names(report) == ["abstract_budget"] * 3
    assert any("7 decimals/percentages" in error for error in report["errors"])
    assert any("Abstract and Introduction repeat:" in error for error in report["errors"])
    paper[1]("sections_en/01_introduction.tex", "0.123 0.123 0.3434 1.323")
    assert not issues(paper, {"max_abstract_decimals": 7})["errors"]
    paper[1]("sections_zh/00_abstract.tex", "我们比较四种状态。")
    assert any("ZH Han characters" in message for message in issues(
        paper, {"max_abstract_zh_chars": 3, "max_abstract_decimals": 7}
    )["errors"])


def test_title_pillars_and_table_cell_budget(paper):
    paper[1]("main.tex", r"""
\section{Experiments}
\begin{table*}
\caption[Short form]{Results for \textbf{Sparse Inversion} and latency.}
\begin{tabular}{lrr}
Model & Error & Cost \\
Ours & $0.42 \pm 0.03$ & \textbf{10} \\
Base & 2 & 20
\end{tabular}
\end{table*}
\appendix
\begin{table}
\caption{Control.}
\begin{tabular}{rr}
1 & 2 \\
3 & 4
\end{tabular}
\end{table}
""")
    pillars = [
        {"name": "Inversion", "keywords": ["sparse inversion"]},
        {"name": "Control", "keywords": ["control"], "min_tables": 1},
        {"name": "Latency", "keywords": ["latency"], "min_tables": 2},
    ]
    report = issues(paper, {"title_pillars": pillars, "max_table_numeric_cells": 3})
    assert names(report) == ["title_alignment", "title_alignment", "table_cell_budget"]
    assert any("4 numeric cells" in error for error in report["errors"])
    assert not issues(paper, {"title_pillars": pillars[:1], "max_table_numeric_cells": 4})["errors"]


def test_section_separation_symbol_aliases_and_reserved_roles(paper):
    paper[1]("sections_en/02_setup.tex", r"""
\section{Setup}
\[ x = s_t \]
The quantity $s_t$ denotes the gate.
The mapping $\Pi_U$ acts on samples.
""")
    paper[1]("sections_en/03_method.tex", r"""
\section{Method}
\begin{equation} x = s_t \label{eq:state} \end{equation}
The quantity $s_t$ denotes the state.
The targets are $g_{b,i}$.
% $\Pi_U$ is absent from the rendered paper.
""")
    config = {
        "method_only_symbols": ["s_t"],
        "symbol_aliases": {"P": [r"\Pi_U"], r"y_{\mathrm{goal}}": ["g_{b,i}"]},
        "reserved_symbols": {"s_t": ["gate", "门控"]},
    }
    report = issues(paper, config)
    assert names(report).count("section_separation") == 2
    assert names(report).count("symbol_aliases") == 3
    assert any("reserved for" in error and "state" in error for error in report["errors"])


def test_shorthand_unnumbered_method_empty_theorem_and_verbatim(paper):
    paper[1]("main.tex", r"""
\section{Method}
\[ x = 1 \]
\begin{equation*} x = 2 \end{equation*}
We use mode A1 and mode B2. Latency mode (mode C3) has a fixed timeout.
\begin{lemma} There exists a finite bound. \end{lemma}
\begin{lemma} The bound is $x\geq 0$. \end{lemma}
\begin{algorithm}
\caption{State update}
\begin{Verbatim}
These commands are literal.
\end{Verbatim}
\end{algorithm}
\caption[Algorithm short form]{Algorithm 2: \textbf{Readout}}
\label{alg:readout}
\begin{verbatim}
literal
\end{verbatim}
\begin{verbatim}
This block is outside the algorithm.
\end{verbatim}
\begin{algorithm}
\caption{Caption supplied by the float}
\end{algorithm}
\begin{Verbatim}
This block immediately follows an algorithm caption.
\end{Verbatim}
""")
    report = issues(paper, {"allowed_shorthands": ["B2"]})
    assert names(report) == ["method_equations", "unexplained_shorthand", "empty_theorem_body",
                             "verbatim_algorithm", "verbatim_algorithm", "verbatim_algorithm"]
    assert "define mode A1" in report["errors"][1]


def test_competing_source_trees_in_both_gates(paper):
    root, put = paper
    main = put("latex/main.tex", r"\documentclass{article}" + "\n" + r"\input{body}")
    put("latex/body.tex", "The model reaches equilibrium.")
    put("latex/paper_gate.json", '{"main_tex":"main.tex"}')
    put("arxiv_en/main.tex", r"\documentclass{article}")
    put("archive/main.tex", r"\documentclass{article}")
    put("figures/main.tex", r"\documentclass{standalone}")
    put("prototype/main.tex", r"% \documentclass{article}")
    assert names(issues(paper, {"single_tex_source": True})) == ["single_tex_source"]
    result = cli("paper_gate.py", main.parent)
    assert result.returncode == 1 and "arxiv_en, latex" in result.stdout
    assert not issues(paper, {"single_tex_source": True, "allowed_tex_trees": ["arxiv_en"]})["errors"]
