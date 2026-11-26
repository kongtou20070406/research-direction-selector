"""Tests for scripts/sync_skills.py: links replace identical copies, drifted copies are refused."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sync_skills", ROOT / "scripts" / "sync_skills.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def _setup(tmp_path: Path, install_files: dict[str, bytes] | None):
    repo = tmp_path / "repo"
    (repo / "skills" / "s1").mkdir(parents=True)
    (repo / "skills" / "s1" / "SKILL.md").write_bytes(b"---\nname: s1\n---\nbody\n")
    install = tmp_path / "install" / "s1"
    if install_files is not None:
        install.mkdir(parents=True)
        for rel, data in install_files.items():
            (install / rel).write_bytes(data)
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"links": {"s1": [str(install)]}, "copies": {}}), encoding="utf-8")
    return repo, install, targets


def test_missing_install_becomes_link(tmp_path):
    repo, install, targets = _setup(tmp_path, None)
    assert sync.link_all(repo, targets, tmp_path / "bak") == 0
    assert sync.link_state(install, repo / "skills" / "s1") == "linked"
    assert sync.status(repo, targets) == 0


def test_identical_copy_is_backed_up_then_linked_crlf_ignored(tmp_path):
    repo, install, targets = _setup(tmp_path, {"SKILL.md": b"---\r\nname: s1\r\n---\r\nbody\r\n"})
    assert sync.link_all(repo, targets, tmp_path / "bak") == 0
    assert sync.link_state(install, repo / "skills" / "s1") == "linked"
    backups = list((tmp_path / "bak").iterdir())
    assert len(backups) == 1 and (backups[0] / "SKILL.md").is_file()


def test_drifted_copy_is_refused_and_left_alone(tmp_path):
    repo, install, targets = _setup(tmp_path, {"SKILL.md": b"---\nname: s1\n---\nedited by another agent\n"})
    assert sync.link_all(repo, targets, tmp_path / "bak") == 1
    assert sync.link_state(install, repo / "skills" / "s1") == "copy"
    assert sync.tree_diff(install, repo / "skills" / "s1") == ["differs: SKILL.md"]
    assert sync.status(repo, targets) == 1


def test_hook_installs_push_check_and_copy_refresh(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    assert sync.install_hook(tmp_path) == 0
    hooks = tmp_path / ".git" / "hooks"
    assert "skills/paper-write/tests" in (hooks / "pre-push").read_text(encoding="utf-8")
    for name in ("post-commit", "post-merge"):
        body = (hooks / name).read_text(encoding="utf-8")
        assert "magpie library sync" in body and body.rstrip().endswith("exit 0")
