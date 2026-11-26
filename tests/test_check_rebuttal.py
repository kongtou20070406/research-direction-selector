"""Good/bad fixtures for skills/paper-write/scripts/check_rebuttal.py."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_rebuttal", ROOT / "skills/paper-write/scripts/check_rebuttal.py")
reb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reb)

REVIEW = "The latency claim is unclear.\nPlease report Haze4K results at 300K iterations, as 100K may be undertrained.\n"


def _folder(tmp_path: Path, reply: str, ledger_extra=None, bindings=True) -> Path:
    f = tmp_path / "rebuttal"
    (f / "reviews").mkdir(parents=True)
    (f / "replies").mkdir()
    (f / "paper").mkdir()
    (f / "reviews" / "R1.txt").write_text(REVIEW, encoding="utf-8")
    (f / "paper" / "main.tex").write_text("\\section{Experiments}\\label{sec:exp}\n", encoding="utf-8")
    (f / "results.json").write_text(json.dumps({"haze4k_300k": {"psnr": 31.4212}}), encoding="utf-8")
    ledger = [
        {"id": "R1-C1", "source": "R1", "quote": "The latency claim is unclear.", "concern": "latency",
         "status": "done", "action": {"kind": "clarify", "location": "paper/main.tex#\\label{sec:exp}"}},
        {"id": "R1-C2", "source": "R1", "quote": "Please report Haze4K results at 300K iterations,",
         "concern": "longer training", "status": "drafted",
         "action": {"kind": "experiment", "location": "paper/main.tex"}, "evidence": ["results.json:haze4k_300k"]},
    ] + (ledger_extra or [])
    (f / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    if bindings:
        (f / "bindings.json").write_text(json.dumps([
            {"file": "replies/R1.md", "display": "31.42", "ledger": "results.json", "key": "haze4k_300k.psnr", "round": 2}
        ]), encoding="utf-8")
    (f / "replies" / "R1.md").write_text(reply, encoding="utf-8")
    return f


GOOD = (
    "[R1-C1] Latency is measured end to end on one GPU; Section 4 now states the protocol.\n\n"
    "[R1-C2] At 300K iterations the method reaches 31.42 dB on Haze4K. We will add the full curve in the revision.\n"
)


def test_good_rebuttal_passes_and_reports_counts(tmp_path):
    errors, report = reb.check_rebuttal(_folder(tmp_path, GOOD))
    assert errors == []
    assert report["ledger_entries"] == 2 and report["unbound_numbers"] == 0
    assert report["reply_character_counts"]["replies/R1.md"] == len(GOOD.strip())


def test_bad_rebuttal_hits_every_mechanical_rule(tmp_path):
    extra = [{"id": "R1-C3", "source": "R1", "quote": "A sentence the reviewer never wrote.", "concern": "x",
              "status": "open", "action": {"kind": "no_change"}}]
    reply = (
        "[R1-C1] Latency is end to end.\n\n"
        "[R1-C9] Phantom tag. We report 12.5% gains on ImageNet.\n\n"
        "[R1-C3] We will run more seeds in the revision.\n"
    )
    folder = _folder(tmp_path, reply, extra, bindings=False)
    errors, _ = reb.check_rebuttal(folder, {"char_limit": 60, "forbid": ["ImageNet"]})
    text = "\n".join(errors)
    assert "R1-C3: quote is not verbatim" in text
    assert "R1-C2: no reply addresses it" in text
    assert "tag [R1-C9] has no ledger entry" in text
    assert "promise without a ledger action" in text  # R1-C3 is no_change
    assert "exceeds the limit of 60" in text
    assert "number 12.5% has no binding" in text
    assert "forbidden term 'ImageNet'" in text


def test_source_snapshot_flags_undeclared_changes(tmp_path):
    folder = _folder(tmp_path, GOOD)
    src = tmp_path / "paper_source"
    src.mkdir()
    (src / "main.tex").write_text("v1\n", encoding="utf-8")
    (src / "appendix.tex").write_text("v1\n", encoding="utf-8")
    reb.snapshot(src, folder / "manifest.json", allow=["main.tex"])
    (src / "main.tex").write_text("v2\n", encoding="utf-8")
    assert reb.check_rebuttal(folder)[0] == []
    (src / "appendix.tex").write_text("v2\n", encoding="utf-8")
    errors, report = reb.check_rebuttal(folder)
    assert errors == ["source: appendix.tex changed but is not in allowed_changes"]
    assert report["original_files_changed"] == ["appendix.tex", "main.tex"]
