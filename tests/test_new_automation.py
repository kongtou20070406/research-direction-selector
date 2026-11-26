import importlib.util
import json
from pathlib import Path

import fitz
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_results_to_latex_renders_mean_std_bold_and_bindings(tmp_path):
    import subprocess
    import sys

    res_json = tmp_path / "RESULTS.json"
    res_json.write_text(json.dumps({
        "evaluations": {
            "Baseline": {"rms": [0.40, 0.42], "success": 0.50},
            "Ours": {"rms": [0.20, 0.22], "success": 0.85},
        }
    }), encoding="utf-8")
    out_tex = tmp_path / "auto_table.tex"
    bindings = tmp_path / "bindings.json"
    proc = subprocess.run([
        sys.executable, "-E", "-s", "-X", "utf8", "-B",
        str(ROOT / "skills/paper-write/scripts/verify_manuscript_formal.py"),
        "--results-to-tex", str(res_json), "--out-tex", str(out_tex),
        "--bindings-out", str(bindings), "--lower-better", "rms", "--digits", "2",
    ], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "RESULTS_TO_TEX OK" in proc.stdout
    assert out_tex.is_file() and bindings.is_file()


def test_results_to_latex_direct_api(tmp_path):
    vmf = _load("vmf_api", "skills/paper-write/scripts/verify_manuscript_formal.py")
    res_json = tmp_path / "RESULTS.json"
    res_json.write_text(json.dumps({
        "evaluations": {
            "Baseline": {"rms": [0.40, 0.42], "success": 0.50},
            "Ours": {"rms": [0.20, 0.22], "success": 0.85},
        }
    }), encoding="utf-8")
    out_tex = tmp_path / "auto_table.tex"
    bindings = tmp_path / "bindings.json"
    info = vmf.render_results_latex(res_json, out_tex, lower_better={"rms"}, digits=2, bindings_out=bindings)
    tex = out_tex.read_text(encoding="utf-8")
    assert info["ok"] and info["rows"] == 2
    assert r"$\mathbf{0.21 \pm 0.01}$" in tex and r"\textbf{0.85}" in tex
    assert json.loads(bindings.read_text(encoding="utf-8"))["auto_table"]["sha256"] == info["sha256"]


def test_paper_gate_layout_arxiv_vs_venue_and_png_preview(tmp_path):
    pg = _load("pg_mod", "skills/paper-write/scripts/paper_gate.py")
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Page {i + 1} body text")
    doc.save(str(tmp_path / "main.pdf"))
    doc.close()
    (tmp_path / "main.log").write_text("Overfull \\hbox (15.2pt too wide) in paragraph\n", encoding="utf-8")

    rep_arxiv = pg.Report()
    pg.check_layout(tmp_path, {"main_tex": "main.tex", "venue": "arxiv"}, {}, rep_arxiv, render_pages=False)
    assert rep_arxiv.errors == [] and any("Overfull" in w for w in rep_arxiv.warnings)

    rep_icml = pg.Report()
    pg.check_layout(tmp_path, {"main_tex": "main.tex", "venue": "icml", "page_limit": 1}, {}, rep_icml, render_pages=True)
    assert any("exceeding icml limit 1" in e for e in rep_icml.errors)
    assert any("Overfull" in e for e in rep_icml.errors)
    assert (tmp_path / ".gate_cache/layout_preview/page_01.png").is_file()


def test_bootstrap_init_project_paper_and_shared_repo(tmp_path):
    bs = _load("bs_mod", "scripts/bootstrap.py")
    pgw = _load("pgw_mod", "skills/paper-write/scripts/paper_gate_watch.py")
    vault = tmp_path / "vault"
    proj = tmp_path / "my_paper"
    shared = tmp_path / "shared"
    (proj / "paper_gate.json").parent.mkdir(parents=True, exist_ok=True)
    (proj / "paper_gate.json").write_text("{}", encoding="utf-8")

    res = bs.init_project(proj, name="demo", mode="paper", vault=vault, shared_repo=shared)
    assert res["ok"] and (proj / ".agents/brief.json").is_file()
    assert (shared / "tools/contrib.py").is_file()
    assert pgw.discover_papers(vault, []) == [proj.resolve()]


def test_program_board_with_knowledge_radar(tmp_path):
    pb = _load("pb_mod", "skills/research-gate/scripts/program_board.py")
    prog_path = tmp_path / "program.json"
    (tmp_path / "ev.md").write_text("ok\n", encoding="utf-8")
    prog_path.write_text(json.dumps({
        "schema": "research-program/v1",
        "program": "demo",
        "north_star": {"current": "N@1", "versions": [{"id": "N@1", "date": "2026-11-26", "source": "GOAL", "statement": "s"}]},
        "propositions": [
            {"id": "P1", "statement": "world model rollout", "anchored_to": "N@1", "status": "open", "claims": ["C1"]},
            {"id": "P1.1", "statement": "world model rollout depth", "parent": "P1", "route": "R1", "anchored_to": "N@1", "status": "supported", "claims": ["C1"], "tractable": True},
        ],
        "claims": [{"id": "C1", "statement": "rollout stable", "propositions": ["P1.1"], "route": "R1", "status": "supported", "strength": "observed", "evidence": ["ev.md"]}],
        "routes": [{"id": "R1", "question": "q", "targets": ["P1"], "status": "active", "kill": {"falsifier": "f"}}],
        "papers": [],
        "contributors": [],
    }, ensure_ascii=False), encoding="utf-8")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "跨项目.jsonl").write_text("\n".join([
        json.dumps({"id": "K-001", "kind": "primitive", "statement": "uniform depth sampling stabilizes world model rollout", "scope": "world model", "status": "active", "evidence": ["ev.md"], "source": "demo", "date": "2026-11-26"}, ensure_ascii=False),
        json.dumps({"id": "X-001", "kind": "asset", "statement": "rollout budget sweep template", "scope": "cross-domain", "status": "active", "evidence": ["ev.md"], "source": "demo", "date": "2026-11-26"}, ensure_ascii=False),
        json.dumps({"id": "K-002", "kind": "dead_end", "statement": "fixed shallow rollout depth fails", "scope": "world model", "status": "active", "evidence": ["ev.md"], "source": "demo", "date": "2026-11-26"}, ensure_ascii=False),
    ]) + "\n", encoding="utf-8")
    board = pb.build(prog_path, today="2026-11-26", knowledge_dir=kb)
    assert "北极星产出雷达（知识库联动）" in board
    assert "可产出节点 `P1`" in board and "X-001" in board and "K-002" in board


def test_code_quality_ast_catches_reference_pollution_and_silent_except(tmp_path):
    awd = _load("awd_mod", "skills/research-gate/scripts/accept_worker_deliverable.py")
    (tmp_path / "reference").mkdir()
    (tmp_path / "engine").mkdir()
    (tmp_path / "reference" / "clean.py").write_text("def forward(x):\n    return x * 2\n", encoding="utf-8")
    (tmp_path / "engine" / "fast.py").write_text("import triton\ndef run(x):\n    return x\n", encoding="utf-8")
    assert awd.check_code_quality(tmp_path, ["reference", "engine"])["ok"] is True

    (tmp_path / "reference" / "bad.py").write_text(
        "import triton\ndef forward(x):\n    try:\n        return x.cuda()\n    except Exception:\n        pass\n",
        encoding="utf-8",
    )
    res = awd.check_code_quality(tmp_path, ["reference"])
    assert res["ok"] is False
    assert any("reference_pollution" in e for e in res["errors"])
    assert any("silent_except" in e for e in res["errors"])


def test_torch_module_quality_catches_dead_param():
    torch = pytest.importorskip("torch")
    awd = _load("awd_torch", "skills/research-gate/scripts/accept_worker_deliverable.py")

    good = torch.nn.Linear(4, 2)
    assert awd.check_torch_module_quality(good, torch.randn(3, 4), torch.randn(5, 4))["ok"] is True

    class DeadParam(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.w = torch.nn.Linear(4, 2)
            self.unused = torch.nn.Parameter(torch.ones(2))

        def forward(self, x):
            return self.w(x)

    bad = awd.check_torch_module_quality(DeadParam(), torch.randn(3, 4))
    assert bad["ok"] is False and any("dead_params" in i for i in bad["issues"])


def test_theory_gate_code_symbol_binding(tmp_path):
    tg = _load("tg_mod", "skills/paper-write/scripts/theory_gate.py")
    (tmp_path / "sec.tex").write_text(
        "Intuitively, each step contracts toward the fixed point.\n\n"
        "\\begin{theorem}\\label{thm:main}\n$a = b$.\n\\end{theorem}\n",
        encoding="utf-8",
    )
    (tmp_path / "Proof.lean").write_text("theorem main_ok : True := by trivial\n", encoding="utf-8")
    (tmp_path / "ref.py").write_text("def step_gdn(x):\n    return x\n", encoding="utf-8")

    ok_cfg = {"theorems": [{"tex_label": "thm:main", "lean_file": "Proof.lean", "lean_theorem": "main_ok",
                            "method_eq": "eq:1", "probe_evidence": "probe.json",
                            "code_ref": "ref.py", "code_symbols": ["step_gdn"]}]}
    assert tg.check_paper(tmp_path, ok_cfg)["errors"] == []

    bad_cfg = {"theorems": [{"tex_label": "thm:main", "lean_file": "Proof.lean", "lean_theorem": "main_ok",
                             "method_eq": "eq:1", "probe_evidence": "probe.json",
                             "code_ref": "ref.py", "code_symbols": ["missing_sym"]}]}
    errs = tg.check_paper(tmp_path, bad_cfg)["errors"]
    assert any("missing_sym" in e for e in errs)
