"""paper_gate_watch.py: runs the gate once after a quiet period, notifies on failure and on recovery."""

import importlib.util
import os
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pgw", ROOT / "skills/paper-write/scripts/paper_gate_watch.py")
pgw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pgw)


def test_debounce_fail_then_recover(tmp_path):
    (tmp_path / "paper_gate.json").write_text("{}", encoding="utf-8")
    tex = tmp_path / "main.tex"
    tex.write_text("a", encoding="utf-8")
    outcomes, calls, notes = [1, 0], [], []

    def runner(cmd, **kw):
        calls.append(cmd)
        rc = outcomes.pop(0)
        return types.SimpleNamespace(returncode=rc, stdout=f"errors {rc} | warnings 0")

    w = pgw.Watcher([tmp_path], quiet=30, fix=False, runner=runner, notifier=lambda t, b: notes.append(t))
    assert w.tick(0) == []                      # nothing changed
    os.utime(tex, (100, 100))
    assert w.tick(10) == [] and w.tick(20) == []  # change seen, still inside the quiet period
    res = w.tick(41)
    assert res and res[0][1] is False and "--compile" in calls[0] and "--fix" not in calls[0]
    assert notes and "未通过" in notes[0]
    os.utime(tex, (200, 200))
    w.tick(50)
    assert w.tick(81)[0][1] is True and "恢复" in notes[-1] and len(calls) == 2


def test_every_config_is_built(tmp_path):
    for name in ("paper_gate_zh.json", "paper_gate.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert [p.name for p in pgw.configs(tmp_path)] == ["paper_gate.json", "paper_gate_zh.json"]
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd[cmd.index("--config") + 1])
        rc = 1 if seen[-1].endswith("_zh.json") else 0
        return types.SimpleNamespace(returncode=rc, stdout=f"errors {rc} | sorry 0\n")

    ok, head = pgw.run_gate(tmp_path, False, runner)
    assert not ok and len(seen) == 2
    assert "paper_gate: errors 0" in head and "paper_gate_zh: errors 1" in head


def test_vault_discovery_and_live_refresh(tmp_path):
    vault = tmp_path / "vault"
    data = vault / "知识库/_数据"
    data.mkdir(parents=True)
    p1 = tmp_path / "paper_one"
    p2 = tmp_path / "paper_two"
    for p in (p1, p2):
        p.mkdir()
        (p / "paper_gate.json").write_text("{}", encoding="utf-8")
        (p / "main.tex").write_text("hi", encoding="utf-8")
    reg = data / "项目.json"
    reg.write_text('{"one": {"paper_dirs": ["' + p1.as_posix() + '"]}}', encoding="utf-8")
    assert pgw.discover_papers(vault, []) == [p1.resolve()]

    calls = []
    w = pgw.Watcher([], quiet=10, fix=False,
                    runner=lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=0, stdout="errors 0 | sorry 0\n"),
                    notifier=lambda t, b: None, vault=vault, refresh_dashboard=True)
    reg.write_text('{"one": {"paper_dirs": ["' + p1.as_posix() + '"]}, "two": {"paper_dirs": ["' + p2.as_posix() + '"]}}', encoding="utf-8")
    os.utime(p2 / "main.tex", (500, 500))
    w.tick(100)
    assert p2.resolve() in w.papers
    os.utime(p2 / "main.tex", (600, 600))
    w.tick(101)
    res = w.tick(115)
    assert res and res[0][0] == p2.resolve()
    assert any("command_center.py" in str(part) for cmd in calls for part in cmd)
