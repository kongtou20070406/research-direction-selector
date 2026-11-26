import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "research-gate" / "scripts" / "knowledge.py"
spec = importlib.util.spec_from_file_location("knowledge", SCRIPT)
kn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kn)


def test_add_assigns_ids_and_dedups(tmp_path):
    f = tmp_path / "ledger" / "knowledge.jsonl"
    assert kn.add(f, "dead_end", "3000 steps at bs=128 undertrains", "Push-T", ["card/043"], "p") == ("added", "K-001")
    assert kn.add(f, "primitive", "single scalar inv_step_scale", "Push-T", ["card/044"], "p") == ("added", "K-002")
    assert kn.add(f, "dead_end", "3000  steps at bs=128 undertrains", "x", ["y"], "p") == ("exists", "K-001")
    assert [e["id"] for e in kn.load(f)] == ["K-001", "K-002"]
    assert kn.check(kn.load(f)) == []


def test_add_refuses_invalid(tmp_path):
    f = tmp_path / "k.jsonl"
    try:
        kn.add(f, "primitive", "x", "", ["card/1"], "p")
    except ValueError as e:
        assert "missing scope" in str(e)
    else:
        raise AssertionError("expected refusal")
    assert not f.exists()


def test_check_flags_problems():
    bad = [{"id": "K-1", "kind": "idea", "statement": "s", "scope": "", "evidence": [], "source": "p",
            "date": "2026-11-24", "status": "superseded"}]
    probs = kn.check(bad)
    assert any("id must look like" in p for p in probs)
    assert any("kind must be" in p for p in probs)
    assert any("missing evidence" in p for p in probs)
    assert any("superseded needs" in p for p in probs)


def test_check_rejects_active_placeholder_but_keeps_retracted_id():
    entry = {"id": "K-001", "kind": "primitive", "statement": "（待结卡填）", "scope": "Push-T",
             "evidence": ["card/043"], "source": "proj-alpha", "date": "2026-11-24", "status": "active"}
    assert kn.check([entry]) == ["K-001: placeholder statement not allowed on active entry"]
    assert kn.check([{**entry, "status": "retracted"}]) == []
    assert kn.check([{**entry, "status": "superseded", "superseded_by": "K-002"}]) == []


def test_search_ranks_and_filters(tmp_path):
    f = tmp_path / "k.jsonl"
    kn.add(f, "asset", "FPF tail fixed-point finetuning with multi-anchor time-homogeneous training", "proj-gamma",
           ["lit/notes.md"], "proj-gamma", ["extrapolation"])
    kn.add(f, "dead_end", "重权上下文一致性损失导致半收敛", "Push-T", ["card/041"], "proj-alpha")
    kn.add(f, "primitive", "time-homogeneous single scalar", "Push-T", ["card/044"], "proj-alpha")
    hits = kn.search(kn.load(f), "time-homogeneous multi-anchor extrapolation training")
    assert hits[0][1]["id"] == "K-001"
    assert [e["id"] for _, e in kn.search(kn.load(f), "半收敛问题")] == ["K-002"]
    assert kn.search(kn.load(f), "time-homogeneous", kind="primitive")[0][1]["id"] == "K-003"


def test_inactive_hidden_by_default(tmp_path):
    f = tmp_path / "k.jsonl"
    row = {"id": "K-001", "kind": "primitive", "statement": "old trick", "scope": "s", "evidence": ["c"],
           "source": "p", "date": "2026-11-24", "status": "retracted"}
    f.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert kn.search(kn.load(f), "old trick") == []
    assert len(kn.search(kn.load(f), "old trick", include_inactive=True)) == 1


def test_render_groups(tmp_path):
    f = tmp_path / "k.jsonl"
    kn.add(f, "dead_end", "a|b", "s", ["c"], "p")
    out = kn.render(kn.load(f))
    assert "排除的做法" in out and "a\\|b" in out


def test_cli(tmp_path, capsys):
    f = tmp_path / "k.jsonl"
    assert kn.main(["add", str(f), "--kind", "practice", "--statement", "cosine lr decay", "--scope", "general",
                    "--evidence", "https://example.org", "--source", "lit"]) == 0
    assert kn.main(["check", str(f)]) == 0
    assert kn.main(["search", str(f), "-q", "cosine decay"]) == 0
    assert "K-001" in capsys.readouterr().out


def test_prefix_keeps_registries_apart(tmp_path):
    f = tmp_path / "shared.jsonl"
    assert kn.add(f, "asset", "a", "s", ["e"], "p", prefix="X") == ("added", "X-001")
    assert kn.add(f, "asset", "b", "s", ["e"], "p", prefix="X") == ("added", "X-002")
    assert kn.check(kn.load(f)) == []
