import json
import sys
import urllib.error
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paper_gate as gate
import paper_gate_semantic as semantic


def test_approved_aliases_have_the_same_preview_and_fix():
    cfg = {"terms": [["chunk context", "ACC"], ["global chunk context", "Adaptive Chunk Context"],
                     ["legacy exchange", "global chunk context"], ["short name", "Short Name"]],
           "protect_phrases": ["Short Name"]}
    prose = ("Global\nchunk context and legacy exchange. Adaptive Chunk Context. short name.\n"
             r"$chunk context$ \cite{chunk context} \texttt{chunk context} \verb|chunk context| "
             r"\(chunk context\) global $x$ context.")
    src = gate.Src(Path("method.tex"), "body", prose)
    preview = gate.Report()
    gate.fix_terms([src], cfg, preview, False)
    assert src.text == prose and len(preview.errors) == 3
    fixed = gate.Report()
    gate.fix_terms([src], cfg, fixed, True)
    assert fixed.fixes == preview.fixes
    assert src.text.splitlines()[0] == "Adaptive Chunk Context and Adaptive Chunk Context. Adaptive Chunk Context. Short Name."
    assert src.text.splitlines()[-1] == prose.splitlines()[-1]
    again = gate.Report()
    gate.fix_terms([src], cfg, again, True)
    assert not again.fixes


@pytest.mark.parametrize("cfg", [
    {"terms":[["old term","New A"],["Old Term","New B"]]},
    {"terms":[["left","right"],["right","left"]]},
    {"terms":[["old term","New A"]], "term_context":{"old term":{"canonical":"New A","note":"read in context"}}},
])
def test_conflicting_glossary_cannot_write(cfg):
    src = gate.Src(Path("method.tex"),"body","old term left")
    with pytest.raises(ValueError):
        gate.fix_terms([src], cfg, gate.Report(), True)
    assert src.text == "old term left"


def test_reviewed_descriptive_term_reports_context_and_preserves_qualifiers():
    cfg = {"term_context":{"synchronous 2D propagation":{"canonical":"SNP","note":"A property or a module."}}}
    text = "Prior work used synchronous 1D propagation.\nWe study synchronous 2D propagation on a grid."
    src = gate.Src(Path("analysis.tex"), "body", text)
    report = gate.Report()
    gate.fix_terms([src], cfg, report, True)
    assert src.text == text and not report.errors and not report.fixes
    assert len(report.infos) == 1 and "analysis.tex:2" in report.infos[0] and "2D" in report.infos[0]
    for dim in ("1D","2D","FP32","VIVNet-S","SS2D"):
        phrase = f"synchronous {dim} propagation"
        assert list(semantic._ngrams(phrase,3,3)) == [phrase]


def flat_vectors(texts):
    return np.ones((len(texts),1), dtype=np.float32)


def test_metric_mismatch_ranks_below_same_metric_with_equal_cosine():
    src = gate.Src(Path("results.tex"), "body", "")
    texts = ["We cut MACs substantially.", "At higher resolution we gain ten dB.", "We reduce MACs at the baseline budget."]
    pool = [(text, src, 0, i, False) for i,text in enumerate(texts)]
    report = gate.Report()
    semantic._restatements(pool, {"restate_metric_groups":{"cost":["MACs"], "quality":["dB"]}, "max_reports":1},
                           flat_vectors, report, gate.line_of)
    assert len(report.warnings) == 1
    assert texts[0] in report.warnings[0] and texts[2] in report.warnings[0]


def test_claim_needs_matching_measured_passage_even_at_cosine_one(tmp_path):
    setup = {"files":["results.tex"], "tasks":{"color Gaussian denoising":["RGB denoising"]},
             "datasets":{"Data-A":[], "Data-B":[]}, "metrics":{"PSNR":["dB"]}}
    text = ("We use the same encoder for image restoration that also improves color Gaussian denoising on Data-B by 2 dB.")
    src = gate.Src(tmp_path/"conclusion.tex","body",text)
    head = [(text, src, 0, 0, False)]
    wrong_data = "RGB denoising on Data-A improves PSNR by 2 dB."
    wrong_task = "On Data-B dehazing improves PSNR by 2 dB."
    right = "RGB denoising on Data-B improves PSNR by 2 dB."
    def row(file, sentence):
        return (sentence,gate.Src(tmp_path/file,"body",sentence),0,0,False)
    body = [row("results.tex",wrong_data),row("results.tex",wrong_task),row("related.tex",right)]
    report = gate.Report()
    semantic._condition_evidence(head,body,tmp_path,setup,flat_vectors,report,gate.line_of)
    assert any("color Gaussian denoising" in w and "no measured passage" in w for w in report.warnings)
    report = gate.Report()
    semantic._condition_evidence(head,body+[row("results.tex",right)],tmp_path,setup,flat_vectors,report,gate.line_of)
    assert not report.warnings and any("results.tex:1" in v for v in report.infos)


def test_optional_service_failure_after_cached_probe_only_warns(tmp_path, monkeypatch):
    class Offline:
        def __init__(self, paper,cfg):
            pass
        def __call__(self, texts):
            if texts != ["probe"]:
                raise urllib.error.URLError("disconnected")
            return flat_vectors(texts)
    monkeypatch.setattr(semantic,"Embedder",Offline)
    src = gate.Src(tmp_path/"method.tex","body","Here is a long enough sentence to need embedding.")
    report = gate.Report()
    semantic.run([src],{},tmp_path,report,gate.masked,gate.line_of)
    assert not report.errors and any("incomplete" in w for w in report.warnings)


def test_dataset_scope_ends_at_paragraph_or_change_of_study(tmp_path):
    setup = {"files": ["analysis.tex"], "datasets": {"Set-A": [], "Set-B": []}, "metrics": {"PSNR": ["dB"]}}
    conclusion = gate.Src(tmp_path / "conclusion.tex", "body", "OurMethod reaches 26.74 dB on Set-A.")
    heads = [(conclusion.text, conclusion, 0, 0, False)]
    topic = "We evaluate cross-resolution transfer on Set-A."
    score = "OurMethod reaches 26.74 dB at 1024."
    def check(text, score_paragraph):
        src = gate.Src(tmp_path / "analysis.tex", "body", text)
        rows = [(topic, src, 0, 0, False)]
        if "Set-B" in text:
            rows.append(("We switch to Set-B.", src, text.index("We switch"), 0, False))
        rows.append((score, src, text.index(score), score_paragraph, False))
        report = gate.Report()
        semantic._condition_evidence(heads, rows, tmp_path, setup, flat_vectors, report, gate.line_of)
        return report
    assert not check(f"{topic} {score}", 0).warnings
    assert check(f"{topic}\n\n{score}", 1).warnings
    assert check(f"{topic} We switch to Set-B. {score}", 0).warnings


def test_writer_receipt_tracks_final_passages_and_survives_a_clean_retry(tmp_path, monkeypatch, capsys):
    sections = tmp_path / "sections"
    sections.mkdir()
    (tmp_path / "main.tex").write_text(r"\input{sections/method}", encoding="utf-8")
    src = sections / "method.tex"
    src.write_text("The global\nchunk context is active. A spare name appears here.", encoding="utf-8")
    cfg = {"main_tex":"main.tex", "terms":[["global chunk context","ACC"],["spare name","other name"]],
           "acronyms":{"ACC":"Adaptive Chunk Context"}}
    (tmp_path / "paper_gate.json").write_text(json.dumps(cfg), encoding="utf-8")
    def invoke(*flags):
        monkeypatch.setattr(sys, "argv", ["paper_gate",str(tmp_path),*flags])
        return gate.main(), capsys.readouterr().out
    code, preview = invoke()
    assert code == 1 and "PAPER_GATE_AUTOFIX_APPLIED" not in preview
    code, fixed = invoke("--fix")
    assert code == 0 and fixed.startswith("PAPER_GATE_AUTOFIX_APPLIED")
    assert "撰稿模型请复核本次自动替换" in (tmp_path/"paper_gate_report.md").read_text(encoding="utf-8")
    receipt, = (tmp_path/".gate_backup").glob("*/writer_review.json")
    saved = receipt.read_bytes()
    payload = json.loads(saved)
    assert payload["event"] == "PAPER_GATE_AUTOFIX_APPLIED"
    assert {e["kind"] for e in payload["edits"]} == {"terms","acronym"}
    term = next(e for e in payload["edits"] if e["before"] == "global\nchunk context")
    assert (term["after"],term["file"],term["line"],term["column"]) == ("ACC","sections/method.tex",1,5)
    assert term["context_after"] == "The Adaptive Chunk Context (ACC) is active."
    last = next(e for e in payload["edits"] if e["before"] == "spare name")
    assert src.read_text(encoding="utf-8")[last["column"]-1:].startswith("other name")
    assert last["context_after"] == "A other name appears here."
    code, retry = invoke("--fix")
    assert code == 0 and "PAPER_GATE_AUTOFIX_APPLIED" not in retry and receipt.read_bytes() == saved
    # A subsequent run with an unrelated compile failure must still tell the writer about committed edits.
    src.write_text("One spare name appears here.", encoding="utf-8")
    def fail_compile(paper,cfg,report):
        report.errors.append("compile failed in test")
        return False
    monkeypatch.setattr(gate,"compile_paper",fail_compile)
    code, failed = invoke("--fix","--compile")
    assert code == 1 and failed.startswith("PAPER_GATE_AUTOFIX_APPLIED")
    assert len(list((tmp_path/".gate_backup").glob("*/writer_review.json"))) == 2


def test_pair_ignore_keeps_other_canonical_candidates(tmp_path):
    class Vectors:
        cache = {}
        model = "test"
        __call__ = staticmethod(flat_vectors)
        def save(self):
            pass
    cfg = {"acronyms":{"RDR":"radial divisive readout","TR":"temporal readout"},
           "semantic":{"term_ignore":[["incremental readout","radial divisive readout"]], "term_threshold":0.9}}
    src = gate.Src(tmp_path/"method.tex","body","We measure incremental readout in this controlled study.")
    report = gate.Report()
    semantic._run([src],cfg,tmp_path,report,gate.masked,gate.line_of,Vectors())
    radial = [w for w in report.warnings if w.startswith("terms?  near 'radial divisive readout'")]
    temporal = [w for w in report.warnings if w.startswith("terms?  near 'temporal readout'")]
    assert all("'incremental readout' 1.00" not in w for w in radial)
    assert any("'incremental readout' 1.00" in w for w in temporal)
