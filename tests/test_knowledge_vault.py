import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "research-gate" / "scripts"
spec = importlib.util.spec_from_file_location("knowledge_vault", SCRIPTS / "knowledge_vault.py")
kv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kv)
kn = kv.kn


def setup(tmp_path):
    repo = tmp_path / "proj-a"
    (repo / "cards").mkdir(parents=True)
    (repo / "cards" / "044_x.md").write_text("# card\n", encoding="utf-8")
    data = tmp_path / "vault" / "知识库" / "_数据"
    data.mkdir(parents=True)
    reg = data / "proj-a.jsonl"
    kn.add(reg, "primitive", "single scalar | gain", "Push-T", ["card/044"], "proj-a", ["gain"], "2026-11-24")
    kn.add(reg, "dead_end", "3000 steps undertrains", "Push-T", ["card/099", "https://x.org/a"], "proj-a/card043", [], "2026-11-23")
    kn.add(data / "跨项目.jsonl", "asset", "FPF anchors", "dehaze", ["notes.md"], "proj-gamma", [], "2026-11-24", prefix="X")
    (data / "项目.json").write_text(json.dumps({"proj-a": {"root": str(repo), "library": "proj-a成果库"}}), encoding="utf-8")
    return data, tmp_path / "vault" / "知识库", repo


def test_build_writes_project_kind_and_entry_pages(tmp_path):
    cfg, out, repo = setup(tmp_path)
    res = kv.build(cfg, out, "2026-11-25")
    assert res["problems"] == [] and res["entries"] == 3
    overview = (out / "00-知识库总览.md").read_text(encoding="utf-8")
    assert "[[按项目/proj-a\\|proj-a]] | 1 | 1 | 0 | 0 | [[proj-a成果库/00-总览（先看这页）\\|proj-a成果库]]" in overview
    assert "proj-gamma" in overview
    proj = (out / "按项目" / "proj-a.md").read_text(encoding="utf-8")
    assert "single scalar \\| gain" in proj and "[[条目/proj-a/K-002\\|K-002]]" in proj
    note = (out / "条目" / "proj-a" / "K-001.md").read_text(encoding="utf-8")
    assert "  - 项目/proj-a" in note and "  - 知识库/原语" in note
    assert "file:///" in note and "044_x.md" in note
    dead = (out / "条目" / "proj-a" / "K-002.md").read_text(encoding="utf-8")
    assert "`card/099`" in dead and "[https://x.org/a](https://x.org/a)" in dead
    assert (out / "条目" / "proj-gamma" / "X-001.md").is_file()
    assert (out / "按类型" / "禁区.md").is_file()


def test_build_skips_overview_refresh_log(tmp_path):
    data, out, _ = setup(tmp_path)
    kn.add(data / "proj-b.jsonl", "practice", "keep a baseline", "training", ["card/001"], "proj-b")
    log = data / "总览刷新记录.jsonl"
    record = {"project": "proj-a", "state": "refreshed", "problems": [], "date": "2026-11-25",
              "report": str(data / "总览刷新报告" / "proj-a_2026-11-25.md")}
    log.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    original = log.read_bytes()

    result = kv.build(data, out, "2026-11-25")

    assert result["problems"] == [] and result["entries"] == 4 and result["pages"] > 0
    assert (out / "条目" / "proj-a" / "K-001.md").is_file()
    assert (out / "条目" / "proj-b" / "K-001.md").is_file()
    assert log.read_bytes() == original


def test_build_reports_invalid_entries_without_writing_pages(tmp_path):
    data, out, _ = setup(tmp_path)
    (data / "invalid.jsonl").write_text('{"id":"K-010","source":"proj-a"}\n{}\n', encoding="utf-8")

    result = kv.build(data, out, "2026-11-25")

    assert {k: result[k] for k in ("pages", "changed", "removed")} == {"pages": 0, "changed": 0, "removed": 0}
    assert "K-010: missing kind" in result["problems"]
    assert "?: missing id" in result["problems"]
    assert not (out / "00-知识库总览.md").exists()


def test_rebuild_removes_stale_notes_and_keeps_data(tmp_path):
    cfg, out, _ = setup(tmp_path)
    kv.build(cfg, out, "2026-11-25")
    stale = out / "条目" / "proj-a" / "K-999.md"
    stale.write_text("old", encoding="utf-8")
    res = kv.build(cfg, out, "2026-11-25")
    assert not stale.exists() and res["removed"] == 1 and res["changed"] == 0
    assert (out / "_数据" / "跨项目.jsonl").is_file() and (out / "_数据" / "proj-a.jsonl").is_file()


def test_duplicate_ids_within_a_project_are_reported(tmp_path):
    data, out, repo = setup(tmp_path)
    kn.add(data / "dup.jsonl", "primitive", "another", "s", ["e"], "proj-a")
    assert any("same id twice" in p for p in kv.build(data, out)["problems"])


def test_links_are_vault_rooted(tmp_path):
    cfg, out, _ = setup(tmp_path)
    (tmp_path / "vault" / ".obsidian").mkdir()
    kv.build(cfg, out, "2026-11-25")
    proj = (out / "按项目" / "proj-a.md").read_text(encoding="utf-8")
    assert "[[知识库/条目/proj-a/K-001\\|K-001]]" in proj
    assert "[[proj-a成果库/00-总览（先看这页）|proj-a成果库]]" in proj
