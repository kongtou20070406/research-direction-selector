"""Incremental Tier 2 review: section binding, changed-section coverage, and the diff-only brief."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


formal = _load("verify_manuscript_formal", "skills/paper-write/scripts/verify_manuscript_formal.py")
brief_mod = _load("build_review_brief", "skills/paper-write/scripts/build_review_brief.py")

BODY = "\n".join(f"Sentence {i} of a long section that the reviewer already read." for i in range(200)) + "\n"


def _draft(tmp_path: Path) -> Path:
    d = tmp_path / "draft"
    for sub in ("sections_en", "sections_zh"):
        (d / sub).mkdir(parents=True)
        for name in ("00_abstract.md", "01_introduction.md"):
            (d / sub / name).write_text(f"# {name}\n" + BODY, encoding="utf-8")
    return d


def _item(scope):
    return {"rule": "R", "scope": scope, "exact_quote": "q", "evidence_ref": "e", "verdict": "PASS"}


def test_rereview_brief_sends_only_the_diff_and_open_findings(tmp_path):
    d = _draft(tmp_path)
    first, s1 = brief_mod.build_brief(d)
    assert s1["changed"] == 4 and "First freeze" in first
    prior = {"manuscript_sha256": "a" * 64, "section_sha256": formal.section_digests(d),
             "findings": [{"id": "F1", "severity": "major", "status": "unresolved",
                           "evidence": "intro para 2", "resolution": "bind the 12.3% claim"}]}
    intro = d / "sections_en" / "01_introduction.md"
    intro.write_text(intro.read_text(encoding="utf-8").replace("Sentence 7 of", "Sentence seven of"), encoding="utf-8")
    text, s2 = brief_mod.build_brief(d, prior)
    assert s2["changed"] == 1 and s2["brief_to_full"] < 0.1
    assert "## sections_en/01_introduction.md (diff)" in text
    assert "-Sentence 7 of" in text and "+Sentence seven of" in text
    assert "Sentence 150 of" not in text  # unchanged lines stay out of the brief
    assert "`F1` (major)" in text
    assert "sections_en/00_abstract.md" in text.split("## Unchanged")[1]


def test_rereview_audit_must_cover_every_changed_section(tmp_path):
    d = _draft(tmp_path)
    prior = {"section_sha256": formal.section_digests(d)}
    (d / "sections_zh" / "01_introduction.md").write_text("# 引言\n已修改\n", encoding="utf-8")
    now = formal.section_digests(d)

    ok = {"section_sha256": now, "items": [_item("01_introduction (EN/ZH)")]}
    assert formal.check_review_coverage(ok, prior, d) == []

    missed = {"section_sha256": now, "items": [_item("00_abstract")]}
    assert formal.check_review_coverage(missed, prior, d) == [
        "Changed section sections_zh/01_introduction.md has no audit item whose 'scope' names it"]

    stale = {"section_sha256": prior["section_sha256"], "items": [_item("01_introduction")]}
    assert any("does not match the staged sections" in e for e in formal.check_review_coverage(stale, prior, d))

    unbound = {"items": [_item("01_introduction")]}
    assert formal.check_review_coverage(unbound, prior, d) == [
        "Re-review audit must record 'section_sha256' (copy it from build_review_brief.py)"]
