"""freeze_review.py: both reviews dispatched, stamped with what was sent, and checked mechanically."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("freeze_review", ROOT / "skills/paper-write/scripts/freeze_review.py")
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)


def _draft(tmp_path: Path) -> Path:
    d = tmp_path / "draft"
    for sub, text in (("sections_en", "# Abstract\nWe show X.\n"), ("sections_zh", "# 摘要\n我们证明 X。\n")):
        (d / sub).mkdir(parents=True)
        (d / sub / "00_abstract.md").write_text(text, encoding="utf-8")
    return d


AUDIT_OK = {"verdict": "PASS", "findings": [], "items": [
    {"rule": "claim entailment", "scope": "sections_en/00_abstract.md sections_zh/00_abstract.md",
     "exact_quote": "We show X.", "evidence_ref": "ledger row 1", "verdict": "PASS"}]}
EDIT_OK = {"verdict": "PASS", "summary": "fine", "findings": []}


def _runner(replies: dict, calls: list):
    def run(prompt, model, cwd, out, effort, timeout):
        calls.append((model, prompt))
        r = replies[model]
        return r if isinstance(r, str) else "```json\n" + json.dumps(r) + "\n```"
    return run


def test_both_pass_and_hash_is_stamped_by_the_script(tmp_path):
    calls = []
    problems = fr.run(_draft(tmp_path), "d" * 64, tmp_path / "out", runner=_runner(
        {fr.AUDITOR: AUDIT_OK, fr.EDITOR: EDIT_OK}, calls), retry_delay=0)
    assert problems == []
    audit = json.loads((tmp_path / "out/audit.json").read_text(encoding="utf-8"))
    assert audit["manuscript_sha256"] == "d" * 64 and audit["auditor"] == fr.AUDITOR
    assert {m for m, _ in calls} == {fr.AUDITOR, fr.EDITOR}
    assert all("We show X." in p for _, p in calls)  # reviewers read the drafts, not a summary


def test_editorial_revise_blocks(tmp_path):
    problems = fr.run(_draft(tmp_path), "d" * 64, tmp_path / "out", runner=_runner(
        {fr.AUDITOR: AUDIT_OK, fr.EDITOR: {**EDIT_OK, "verdict": "REVISE"}}, []), retry_delay=0)
    assert any("editorial" in p and "REVISE" in p for p in problems)


def test_open_major_finding_with_pass_blocks(tmp_path):
    bad = {**AUDIT_OK, "findings": [{"id": "F1", "severity": "major", "type": "unsupported_claim",
                                      "status": "unresolved", "evidence": "x", "countercheck": "y",
                                      "resolution": "z"}]}
    problems = fr.run(_draft(tmp_path), "d" * 64, tmp_path / "out", runner=_runner(
        {fr.AUDITOR: bad, fr.EDITOR: EDIT_OK}, []), retry_delay=0)
    assert any("open major finding" in p for p in problems)
    brief = (tmp_path / "out/lead_action_brief.md").read_text(encoding="utf-8")
    assert "| F1 | — | unsupported_claim | x | z |" in brief


def test_unparseable_reply_is_retried_once_then_blocks(tmp_path):
    calls = []
    problems = fr.run(_draft(tmp_path), "d" * 64, tmp_path / "out", runner=_runner(
        {fr.AUDITOR: "sorry, no json", fr.EDITOR: EDIT_OK}, calls), retry_delay=0)
    assert sum(m == fr.AUDITOR for m, _ in calls) == 2
    assert any("no parseable JSON" in p for p in problems)
    assert (tmp_path / "out/lead_action_brief.md").is_file()


def test_dry_run_calls_no_model(tmp_path):
    calls = []
    assert fr.run(_draft(tmp_path), "d" * 64, tmp_path / "out", runner=_runner({}, calls), dry_run=True) == []
    assert calls == [] and (tmp_path / "out/prompt_audit.md").is_file()
    assert not (tmp_path / "out/lead_action_brief.md").exists()
    for name in ("audit", "editorial"):
        prompt_text = (tmp_path / f"out/prompt_{name}.md").read_text(encoding="utf-8")
        assert all(phrase in prompt_text for phrase in (
            "numeric scores", "accept/reject slogans", "rewrite manuscript prose", "structured facts only"
        ))
    prompt = (tmp_path / "out/prompt_audit.md").read_text(encoding="utf-8")
    for field in ("affirmative_core", "regime_boundary", "math_counterexample", "number_mismatch",
                  "missing_control", "lean_mismatch", "scope_caveat", "must be minor and advisory",
                  "never block PASS or request apologetic disclaimers in the Abstract or Introduction"):
        assert field in prompt
