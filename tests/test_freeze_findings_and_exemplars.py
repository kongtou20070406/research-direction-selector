"""Tests for re-review finding provenance in freeze audits and for the exemplar paper fetcher."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


formal = _load("verify_manuscript_formal", "skills/paper-write/scripts/verify_manuscript_formal.py")
fetch = _load("fetch_exemplar_papers", "skills/paper-write/scripts/fetch_exemplar_papers.py")

DIGEST_V1, DIGEST_V2 = "a" * 64, "b" * 64
ITEM = {"rule": "R1", "scope": "abstract", "exact_quote": "q", "evidence_ref": "ledger#1", "verdict": "PASS"}


def _finding(fid, severity="major", status="resolved", **extra):
    f = {"id": fid, "severity": severity, "type": "unsupported_claim", "status": status,
         "evidence": "sec 1 para 2", "countercheck": "reran the binding check"}
    f.update(extra)
    return f


def _audit(path: Path, digest: str, findings=None, verdict="PASS", **extra) -> Path:
    data = {"auditor": "independent-reviewer", "manuscript_sha256": digest, "verdict": verdict, "items": [ITEM]}
    if findings is not None:
        data["findings"] = findings
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_legacy_audit_without_findings_still_passes(tmp_path):
    assert formal.verify_freeze_audit(_audit(tmp_path / "a.json", DIGEST_V1), DIGEST_V1) == []


def test_open_blocking_finding_contradicts_pass_and_needs_resolution(tmp_path):
    path = _audit(tmp_path / "a.json", DIGEST_V1, [_finding("F1", status="unresolved"), _finding("F2", "minor", "unresolved")])
    errors = formal.verify_freeze_audit(path, DIGEST_V1)
    assert any("F1 is blocking and needs a 'resolution'" in e for e in errors)
    assert any("F1 is an open major finding but the verdict is PASS" in e for e in errors)
    assert not any("F2" in e for e in errors)  # an open minor finding does not block


def test_rereview_must_carry_prior_open_findings_with_origin(tmp_path):
    prior = _audit(tmp_path / "v1.json", DIGEST_V1,
                   [_finding("F1", status="unresolved", resolution="bind the 12.3% claim")], verdict="FAIL")
    dropped = _audit(tmp_path / "v2a.json", DIGEST_V2, [_finding("F2", origin="revision_regression")],
                     prior_manuscript_sha256=DIGEST_V1)
    errors = formal.verify_freeze_audit(dropped, DIGEST_V2, prior_audit_path=prior)
    assert any("Prior open finding F1 is missing" in e for e in errors)

    no_origin = _audit(tmp_path / "v2b.json", DIGEST_V2, [_finding("F1")], prior_manuscript_sha256=DIGEST_V1)
    assert any("'origin' must be one of" in e for e in formal.verify_freeze_audit(no_origin, DIGEST_V2, prior))

    fake_inherited = _audit(tmp_path / "v2c.json", DIGEST_V2,
                            [_finding("F1", origin="inherited"), _finding("F9", "minor", origin="inherited")],
                            prior_manuscript_sha256=DIGEST_V1)
    assert any("F9 is marked inherited" in e for e in formal.verify_freeze_audit(fake_inherited, DIGEST_V2, prior))

    good = _audit(tmp_path / "v2d.json", DIGEST_V2,
                  [_finding("F1", origin="inherited"), _finding("F2", "minor", origin="previously_undetected")],
                  prior_manuscript_sha256=DIGEST_V1)
    assert formal.verify_freeze_audit(good, DIGEST_V2, prior_audit_path=prior) == []

    wrong_prior = _audit(tmp_path / "v2e.json", DIGEST_V2, [_finding("F1", origin="inherited")],
                         prior_manuscript_sha256=DIGEST_V2)
    assert any("prior_manuscript_sha256" in e for e in formal.verify_freeze_audit(wrong_prior, DIGEST_V2, prior))


def test_exemplar_manifest_matches_cards():
    exemplars = ROOT / "skills/paper-write/references/exemplars"
    entries = fetch.load_manifest(exemplars / "papers.json")
    cards = {p.stem for p in (exemplars / "cards").glob("*.md")}
    assert {e["card"] for e in entries} == cards  # every card has an original, every original a card
    for e in entries:
        text = (exemplars / "cards" / f"{e['card']}.md").read_text(encoding="utf-8")
        assert f"../papers/{e['id']}.md" in text


def test_fetch_rejects_wrong_paper_and_writes_page_markers(tmp_path):
    import fitz

    def fake_download(title):
        def _dl(url, dest, timeout):
            doc = fitz.open()
            for text in (title, "second page body"):
                doc.new_page().insert_text((72, 72), text)
            doc.save(dest)
        return _dl

    entry = {"id": "p1", "card": "c", "title": "Learning Interactive Real-World Simulators", "url": "https://example.org/p1.pdf"}
    lock, errors = fetch.fetch_all([entry], tmp_path, downloader=fake_download("Learning Interactive Real-World Simulators"))
    assert errors == [] and lock["p1"]["pages"] == 2
    md = (tmp_path / "p1.md").read_text(encoding="utf-8")
    assert "## Page 1" in md and "## Page 2" in md

    other = dict(entry, id="p2")
    _, errors = fetch.fetch_all([other], tmp_path, downloader=fake_download("Vision Transformers Need Registers"))
    assert errors and "title check failed" in errors[0]


routes = _load("check_routes", "scripts/check_routes.py")


def test_path_privacy_flags_publishable_home_paths_only(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    home = "C:" + "\\" + "Us" + "ers" + "\\" + "alice"  # built up so this test file stays clean
    (tmp_path / "a.md").write_text(f"see {home}/notes\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("Path.home() / '.gemini'\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("secret.md\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text(f"{home}/x\n", encoding="utf-8")
    errors = routes.check_path_privacy(tmp_path)
    assert errors == ["Local path leak (windows-user-home) in a.md:1"]


def test_markdown_links_separate_broken_from_vendored(tmp_path):
    root = tmp_path / "skills"
    (root / "s" / "references" / "ars").mkdir(parents=True)
    (root / "s" / "SKILL.md").write_text(
        "[ok](references/ars/VENDORED.md) [bad](references/missing.md) [tpl](url) `[code](nowhere.md)`\n",
        encoding="utf-8",
    )
    (root / "s" / "references" / "ars" / "VENDORED.md").write_text("scope note\n", encoding="utf-8")
    (root / "s" / "references" / "ars" / "doc.md").write_text("[up](shared/contract.md)\n", encoding="utf-8")
    errors, infos = routes.check_markdown_links(root)
    assert errors == ["Broken link in s/SKILL.md: references/missing.md"]
    assert infos == ["s/references/ars: 1 links to upstream files not vendored (see VENDORED.md)"]
