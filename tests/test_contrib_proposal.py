"""contrib_proposal.py: pending contributions are found on disk and on remote refs, checked, never merged."""

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("contrib_proposal", ROOT / "skills/research-gate/scripts/contrib_proposal.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

PROG = {"north_star": {"current": "S@1"}, "propositions": [{"id": "W3"}],
        "claims": [{"id": "C-old", "source": "contrib/W3-done"}]}
CLAIM = {"proposition": "W3", "statement": "s", "strength": "observed", "evidence": ["log.txt"],
         "falsifier": "f", "author": "a"}


def _folder(root: Path, name: str, claim: dict, files=("log.txt",)):
    d = root / "contrib" / name
    d.mkdir(parents=True)
    (d / "CLAIM.json").write_text(json.dumps(claim), encoding="utf-8")
    for f in files:
        (d / f).write_text("x", encoding="utf-8")


def test_working_tree_pending_and_proposal(tmp_path):
    _folder(tmp_path, "W3-ann", CLAIM)
    _folder(tmp_path, "W3-done", CLAIM)
    _folder(tmp_path, "W3-bad", {**CLAIM, "proposition": "W9", "evidence": ["gone.txt"]})
    items = cp.pending(cp.collect(tmp_path), PROG)
    assert sorted(i["folder"] for i in items) == ["W3-ann", "W3-bad"]
    by = {i["folder"]: i for i in items}
    assert cp.check(by["W3-ann"], PROG) == []
    bad = cp.check(by["W3-bad"], PROG)
    assert any("W9" in e for e in bad) and any("gone.txt" in e for e in bad)
    prop = cp.proposal(by["W3-ann"], PROG)
    assert prop["status"] == "proposed" and prop["anchored_to"] == "S@1"
    assert prop["evidence"] == ["contrib/W3-ann/log.txt"] and prop["source"] == "contrib/W3-ann"
    page = cp.render(items, PROG, "2026-11-24 22:00")
    assert "W3-ann" in page and '"status": "proposed"' in page


def test_remote_branch_contribution_is_found(tmp_path):
    def git(cwd, *a):
        subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)

    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@t")
    git(origin, "config", "user.name", "t")
    (origin / "README.md").write_text("x", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "init")
    git(origin, "checkout", "-qb", "contrib-ann")
    _folder(origin, "W3-pr", CLAIM)
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "contrib")
    git(origin, "checkout", "-q", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    items = cp.collect(clone)
    assert [i["folder"] for i in items] == ["W3-pr"] and items[0]["where"].endswith("contrib-ann")
    assert cp.check(items[0], PROG) == []
    assert not (clone / "contrib").exists()  # nothing was checked out or merged
