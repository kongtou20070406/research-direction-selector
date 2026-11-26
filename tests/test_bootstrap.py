"""bootstrap.py links skills into existing agent folders and never replaces a folder already there."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts" / "bootstrap.py")
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


def test_plan_and_apply_without_magpie(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    for name in ("alpha", "beta"):
        (repo / "skills" / name).mkdir(parents=True)
        (repo / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    (repo / ".git" / "hooks").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".claude" / "skills" / "beta").mkdir(parents=True)          # someone's own beta: must survive
    (home / ".claude" / "skills" / "beta" / "SKILL.md").write_text("mine\n", encoding="utf-8")

    steps = boot.plan(home, repo, magpie=None)
    actions = {(a, l.parent.parent.name, l.name) for a, l, _ in steps}
    assert actions == {("link", ".claude", "alpha"), ("skip-existing", ".claude", "beta")}   # only existing agent dirs

    assert boot.apply(steps, None, repo) == 0
    sync = boot._sync()
    assert sync.link_state(home / ".claude/skills/alpha", repo / "skills/alpha") == "linked"
    assert (home / ".claude/skills/beta/SKILL.md").read_text(encoding="utf-8") == "mine\n"
    assert (repo / ".git/hooks/pre-push").is_file()
    assert {a for a, _, _ in boot.plan(home, repo, magpie=None)} == {"ok", "skip-existing"}   # idempotent
