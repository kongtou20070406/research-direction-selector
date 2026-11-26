import datetime as dt
import json
from pathlib import Path

from scripts import sessions


def _utc(hours_ago: float = 0.0) -> dt.datetime:
    return dt.datetime(2026, 11, 26, 12, 0, 0, tzinfo=dt.timezone.utc) - dt.timedelta(hours=hours_ago)


def test_path_overlaps_edge_cases():
    assert sessions.path_overlaps("loop_agi/", "loop_agi/reference/m.py")
    assert sessions.path_overlaps(r"loop_agi\reference\m.py", "loop_agi")
    assert not sessions.path_overlaps("loop_agi/", "loop_agi_v2/m.py")
    assert sessions.path_overlaps("cards/056*", "cards/056.md")
    assert sessions.path_overlaps("cards/056*", "cards/")
    assert not sessions.path_overlaps("cards/056*", "cards/057.md")
    assert sessions.path_overlaps(".", "latex/sec1.tex")
    assert sessions.path_overlaps("*", "src/model.py")
    assert not sessions.path_overlaps("read-only", "src/model.py")
    assert not sessions.path_overlaps("readonly", "read-only")


def test_claim_conflict_force_heartbeat_release_prune(tmp_path: Path):
    now = _utc(0)
    ok1, res1 = sessions.claim(
        tmp_path, "chat-exp", task="Card 056 training", scope=["loop_agi/", "cards/056.md"], resources=["gpu:0"], now=now
    )
    assert ok1 and res1["id"] == "chat-exp"

    # Overlapping directory & GPU refused without --force (returns False, conflicts)
    ok_bad, conflicts = sessions.claim(
        tmp_path, "chat-eng", task="Refactor engine", scope=["loop_agi/engine"], resources=["gpu:0"], now=now
    )
    assert not ok_bad and len(conflicts) == 1
    assert conflicts[0]["with"] == "chat-exp"
    assert any("loop_agi" in s for s in conflicts[0]["scopes"])
    assert conflicts[0]["resources"] == ["gpu:0"]

    # Non-overlapping scope + cloud GPU succeeds
    ok2, _ = sessions.claim(
        tmp_path, "chat-paper", task="Draft method section", scope=["latex/sections_en"], resources=["colab:L4"], now=now
    )
    assert ok2

    # Force override writes conflicting entry, and brief_lines surfaces conflict
    ok_force, _ = sessions.claim(
        tmp_path, "chat-eng", task="Hotfix", scope=["loop_agi/engine"], force=True, now=now
    )
    assert ok_force
    lines = sessions.brief_lines(tmp_path, now=now)
    assert any("chat-exp" in line and "gpu:0" in line for line in lines)
    assert any("⚠ CONFLICT: chat-eng <-> chat-exp" in line for line in lines)

    # Heartbeat refreshes updated_at; release removes entry
    hb = sessions.heartbeat(tmp_path, "chat-eng", task="Hotfix done", now=now + dt.timedelta(minutes=5))
    assert hb is not None and hb["task"] == "Hotfix done"
    assert sessions.release(tmp_path, "chat-eng", now=now + dt.timedelta(minutes=5))
    assert not sessions.release(tmp_path, "chat-eng", now=now + dt.timedelta(minutes=5))

    # TTL expiration + prune
    sessions.claim(tmp_path, "chat-old", task="Old run", scope=["scripts/"], ttl_hours=1.0, now=_utc(5))
    assert all(s["id"] != "chat-old" for s in sessions.load_active(tmp_path, now=now))
    assert sessions.prune(tmp_path, now=now) == 1


def test_cli_claim_check_list_and_worktree_resolution(tmp_path: Path, capsys):
    main_repo = tmp_path / "main-repo"
    (main_repo / ".git" / "worktrees" / "wt-paper").mkdir(parents=True)
    wt = tmp_path / "wt-paper"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main_repo / '.git' / 'worktrees' / 'wt-paper'}\n", encoding="utf-8")
    (main_repo / ".git" / "worktrees" / "wt-paper" / "commondir").write_text("../..\n", encoding="utf-8")

    assert sessions.resolve_shared_root(wt) == main_repo.resolve()
    assert sessions.board_path(wt) == main_repo.resolve() / ".agents" / "sessions.json"

    assert sessions.main(["claim", "chat-a", "--task", "Run", "--scope", "src/", "--resource", "gpu:0", "--project", str(main_repo)]) == 0
    capsys.readouterr()

    # Conflict from linked worktree returns exit code 2
    assert sessions.main(["claim", "chat-b", "--task", "Edit src", "--scope", "src/x.py", "--worktree", str(wt), "--project", str(wt)]) == 2
    assert "CONFLICT with chat-a" in capsys.readouterr().out

    # Disjoint claim from linked worktree succeeds and writes to main_repo/.agents/sessions.json
    assert sessions.main(["claim", "chat-b", "--task", "Write latex", "--scope", "latex/", "--worktree", str(wt), "--project", str(wt)]) == 0
    capsys.readouterr()

    # check --id ignores self
    assert sessions.main(["check", "--id", "chat-b", "--scope", "latex/sec1.tex", "--project", str(wt)]) == 0
    assert sessions.main(["check", "--id", "chat-c", "--scope", "latex/sec1.tex", "--project", str(wt)]) == 1
    capsys.readouterr()

    assert sessions.main(["list", "--json", "--project", str(wt)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {s["id"] for s in payload["sessions"]} == {"chat-a", "chat-b"}


def test_lead_swarm_hierarchy_and_cascade_release(tmp_path: Path, capsys):
    now = _utc(0)
    assert sessions.main(
        ["claim", "chat-exp", "--task", "Main line", "--scope", "loop_agi/", "tests/", "--project", str(tmp_path)]
    ) == 0
    assert sessions.main(
        [
            "claim",
            "w-engine",
            "--owner",
            "chat-exp",
            "--role",
            "worker",
            "--task",
            "Triton kernel",
            "--scope",
            "loop_agi/engine",
            "--resource",
            "gpu:0",
            "--project",
            str(tmp_path),
        ]
    ) == 0
    assert sessions.main(
        [
            "claim",
            "w-tests",
            "--owner",
            "chat-exp",
            "--task",
            "Parity tests",
            "--scope",
            "tests/test_engine.py",
            "--project",
            str(tmp_path),
        ]
    ) == 0
    capsys.readouterr()

    # Sibling worker under same Lead collides on loop_agi/engine
    assert sessions.main(
        [
            "claim",
            "w-engine2",
            "--owner",
            "chat-exp",
            "--task",
            "Dup kernel",
            "--scope",
            "loop_agi/engine/k.py",
            "--project",
            str(tmp_path),
        ]
    ) == 2
    assert "CONFLICT with w-engine" in capsys.readouterr().out

    # Unrelated session collides with w-engine on gpu:0 and scope
    assert sessions.main(
        ["check", "--id", "chat-other", "--scope", "loop_agi/engine/k.py", "--resource", "gpu:0", "--project", str(tmp_path)]
    ) == 1
    capsys.readouterr()

    # check --owner ignores Lead's own parent scope when no sibling owns that slice
    assert sessions.main(
        ["check", "--owner", "chat-exp", "--scope", "loop_agi/reference", "--project", str(tmp_path)]
    ) == 0
    assert sessions.main(
        ["check", "--owner", "chat-exp", "--scope", "loop_agi/engine", "--project", str(tmp_path)]
    ) == 1
    capsys.readouterr()

    lines = sessions.brief_lines(tmp_path, now=now)
    assert any(line.startswith("- chat-exp [lead]") for line in lines)
    assert any(line.startswith("  ↳ w-engine [worker]") for line in lines)
    assert not any("CONFLICT" in line for line in lines)

    # Unrelated session stays after cascade release of chat-exp
    sessions.claim(tmp_path, "chat-paper", task="Draft §3", scope=["latex/"], now=now)
    assert sessions.main(["release", "chat-exp", "--cascade", "--project", str(tmp_path)]) == 0
    assert "RELEASED chat-exp (+2 workers)" in capsys.readouterr().out
    assert [s["id"] for s in sessions.load_active(tmp_path, now=now)] == ["chat-paper"]
