"""Acceptance tests for skills/research-gate/scripts/share.py (locked before dispatch).

`share.py export` mirrors only the knowledge worth exchanging through git: the program file,
the decision log, and the small text evidence behind supported or refuted claims. Each export
prepends a short changelog so a collaborator reads the delta, not the history.
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("share", ROOT / "skills/research-gate/scripts/share.py")
share = importlib.util.module_from_spec(spec)
spec.loader.exec_module(share)
publish_spec = importlib.util.spec_from_file_location("publish_shared", ROOT / "skills/research-gate/scripts/publish_shared.py")
publisher = importlib.util.module_from_spec(publish_spec)
publish_spec.loader.exec_module(publisher)


def _project(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "proj"
    (root / "cards").mkdir(parents=True)
    (root / "proofs").mkdir()
    (root / "runs").mkdir()
    (root / "cards" / "002.md").write_text("W1 result\n", encoding="utf-8")
    (root / "proofs" / "A.lean").write_text("theorem a : True := trivial\n", encoding="utf-8")
    (root / "runs" / "ckpt.pt").write_bytes(b"\x00" * 2048)              # binary: never shared
    (root / "runs" / "big.json").write_text("x" * 3000, encoding="utf-8")  # over the size cap in tests
    (root / "DECISIONS.md").write_text("| 日期 | 原话 | 含义 |\n", encoding="utf-8")
    prog = {
        "schema": "research-program/v1", "program": "demo",
        "north_star": {"current": "S@1", "versions": [{"id": "S@1", "date": "2026-11-19", "statement": "s"}], "amendments": []},
        "propositions": [{"id": "W1", "statement": "a", "anchored_to": "S@1", "depends_on": [], "status": "partial", "claims": ["C1", "C2", "C3"]}],
        "claims": [
            {"id": "C1", "statement": "x", "strength": "observed", "status": "supported", "evidence": ["cards/002.md", "runs/ckpt.pt"], "propositions": ["W1"], "anchored_to": "S@1"},
            {"id": "C2", "statement": "y", "strength": "proved", "status": "supported", "evidence": ["proofs/A.lean", "ledger:7"], "propositions": ["W1"], "anchored_to": "S@1"},
            {"id": "C3", "statement": "z", "strength": "observed", "status": "proposed", "evidence": ["runs/big.json"], "propositions": ["W1"], "anchored_to": "S@1"},
        ],
        "papers": [{"id": "P1", "title": "t", "status": "drafting", "anchored_to": "S@1", "propositions": ["W1"], "primary_claims": ["C1"], "background_claims": []}],
        "routes": [{"id": "R1", "question": "q", "targets": ["W1"], "status": "active", "opened": "2026-11-20", "last_activity": "2026-11-24",
                    "kill": {"falsifier": "f", "deadline": "2027-01-01"}, "cards": ["002"]}],
    }
    (root / "program.json").write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
    return root, prog


def test_export_shares_only_small_text_evidence_of_settled_claims(tmp_path):
    root, _ = _project(tmp_path)
    out = tmp_path / "shared"
    manifest = share.export(root / "program.json", root, out, today="2026-11-24", max_bytes=1000)
    files = set(manifest["files"])
    assert {"program.json", "DECISIONS.md", "evidence/cards/002.md", "evidence/proofs/A.lean"} <= files
    assert not any("ckpt.pt" in f or "big.json" in f for f in files)          # binary, or claim not settled
    reasons = {s["path"]: s["reason"] for s in manifest["skipped"]}
    assert reasons["runs/ckpt.pt"] == "not text"
    assert (out / "MANIFEST.json").is_file() and (out / "CHANGES.md").is_file()
    assert share.check_shared(out) == []


def test_changes_report_only_the_delta():
    base = {
        "north_star": {"current": "S@1"},
        "propositions": [{"id": "W1", "status": "partial"}],
        "claims": [{"id": "C1", "status": "proposed"}],
        "routes": [{"id": "R1", "status": "active"}],
        "papers": [{"id": "P1", "status": "drafting"}],
    }
    new = copy.deepcopy(base)
    new["north_star"]["current"] = "S@2"
    new["propositions"].append({"id": "W1.1", "status": "open"})
    new["claims"][0]["status"] = "supported"
    new["routes"][0]["status"] = "paused"
    lines = share.changes(base, new)
    text = "\n".join(lines)
    assert "S@1 → S@2" in text
    assert "W1.1" in text and "新增" in text
    assert "C1: proposed → supported" in text
    assert "R1: active → paused" in text
    assert not any("P1" in l for l in lines)                                 # unchanged items stay silent
    assert share.changes(base, copy.deepcopy(base)) == []


def test_second_export_prepends_changelog_and_prunes_stale_evidence(tmp_path):
    root, prog = _project(tmp_path)
    out = tmp_path / "shared"
    share.export(root / "program.json", root, out, today="2026-11-24", max_bytes=1000)
    prog["claims"][2]["status"] = "supported"
    prog["claims"][2]["evidence"] = ["cards/002.md"]
    prog["claims"][1]["status"] = "refuted"
    prog["claims"][1]["refuted_by"] = ["cards/002.md"]
    prog["claims"][1]["evidence"] = ["ledger:7"]                              # A.lean no longer cited
    (root / "program.json").write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
    manifest = share.export(root / "program.json", root, out, today="2026-11-25", max_bytes=1000)
    assert "evidence/proofs/A.lean" not in manifest["files"] and not (out / "evidence/proofs/A.lean").exists()
    log = (out / "CHANGES.md").read_text(encoding="utf-8")
    assert log.index("2026-11-25") < log.index("2026-11-24")                   # newest entry first
    assert "C3: proposed → supported" in log and "C2: supported → refuted" in log
    assert share.check_shared(out) == []


def test_check_shared_catches_tampering_and_strays(tmp_path):
    root, _ = _project(tmp_path)
    out = tmp_path / "shared"
    share.export(root / "program.json", root, out, today="2026-11-24", max_bytes=1000)
    (out / "evidence" / "cards" / "002.md").write_text("edited by hand\n", encoding="utf-8")
    (out / "notes.txt").write_text("stray\n", encoding="utf-8")
    text = "\n".join(share.check_shared(out))
    assert "evidence/cards/002.md" in text and "notes.txt" in text


def test_cli(tmp_path):
    root, _ = _project(tmp_path)
    out = tmp_path / "shared"
    assert share.main(["export", "--program", str(root / "program.json"), "--root", str(root), "--to", str(out),
                       "--today", "2026-11-24", "--max-bytes", "1000"]) == 0
    assert share.main(["check", str(out)]) == 0
    (out / "program.json").write_text("{}", encoding="utf-8")
    assert share.main(["check", str(out)]) == 1


def _knowledge_entry(eid, kind, *, source="demo/card048", **fields):
    return {"id": eid, "kind": kind, "source": source, "status": "active",
            "statement": "Reuse the scalar gain", "scope": "Push-T", "date": "2026-11-24",
            "evidence": ["card/048"], **fields}


def _write_registry(path, *entries, newline="\n"):
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
                    encoding="utf-8", newline=newline)


def test_publish_cli_mirrors_knowledge_and_renders_active_entries(tmp_path):
    root, _ = _project(tmp_path)
    out = tmp_path / "shared"
    a, b = tmp_path / "demo.jsonl", tmp_path / "assets.jsonl"
    windows_home = "C:" + r"\Users"
    windows_home_slash = "C:" + "/Users"
    linux_home = "/" + "home"
    project_note = (root / "lit/note.md").as_posix()
    outside_note = "E:" + "/private/secret.md"
    _write_registry(a, _knowledge_entry("K-017", "dead_end", evidence=[
        "card/048", "https://example.org/notes", project_note, outside_note,
        "E:" + r"\runs\private.md", "E:" + "/private.md",
        "D:" + r"\my folder\private.md", "D:" + "/private.md", windows_home + r"\me\private.md",
        windows_home_slash + "/me/private.md", r"\\host\share\private.md", linux_home + "/me/private.md",
    ]), _knowledge_entry("K-018", "primitive", status="retracted"), newline="\r\n")
    _write_registry(b, _knowledge_entry("X-007", "asset", source="other/card010",
                                       evidence=[outside_note]))
    args = ["--program", str(root / "program.json"), "--root", str(root), "--to", str(out), "--today", "2026-11-24"]
    source_contents = {path: path.read_bytes() for path in (a, b)}

    assert publisher.main([*args, "--knowledge", str(a), "--knowledge", str(b)]) == 0

    expected = {a: [["card/048", "https://example.org/notes", "lit/note.md"], ["card/048"]],
                b: [["(local-note)"]]}
    for path, evidence in expected.items():
        mirror = (out / "knowledge" / path.name).read_bytes()
        assert mirror.endswith(b"\n") and b"\r" not in mirror
        assert project_note.encode() not in mirror and b"private" not in mirror and b"secret.md" not in mirror
        assert [json.loads(line)["evidence"] for line in mirror.splitlines()] == evidence
        assert path.read_bytes() == source_contents[path]
    summary = (out / "KNOWLEDGE.md").read_text(encoding="utf-8")
    assert "generated_by: publish_shared.py · 2026-11-24" in summary
    assert "| 项目 | 原语 | 禁区 | 跨项目资产 | 外部实践 |" in summary
    assert "| demo | 0 | 1 | 0 | 0 |" in summary and "| other | 0 | 0 | 1 | 0 |" in summary
    assert "| K-017 | demo | Reuse the scalar gain | Push-T | 2026-11-24 | card/048、https://example.org/notes、lit/note.md |" in summary
    assert "## 跨项目资产" in summary and "| X-007 | other |" in summary
    assert "K-018" not in summary
    assert all(marker not in summary for marker in (project_note, outside_note, "private.md", "my folder", "secret.md"))
    log = (out / "CHANGES.md").read_text(encoding="utf-8")
    assert "## 2026-11-24" in log and "知识库 K-017 新增（dead_end）" in log
    assert "知识库 X-007 新增（asset）" in log
    assert share.check_shared(out) == []

    # A plain program publish keeps the previously exported knowledge and its manifest entries.
    assert publisher.main(args) == 0
    assert (out / "CHANGES.md").read_text(encoding="utf-8") == log
    assert (out / "KNOWLEDGE.md").read_text(encoding="utf-8") == summary
    assert share.check_shared(out) == []


@pytest.mark.parametrize(("field", "value"), [
    ("statement", "Stored under D:" + r"\my folder\private"),
    ("scope", "实验位于/" + "home/researcher/private"),
])
def test_publish_blocks_embedded_local_paths_before_writing(tmp_path, field, value):
    root, _ = _project(tmp_path)
    out = tmp_path / "shared"
    registry = tmp_path / "demo.jsonl"
    _write_registry(registry, _knowledge_entry("K-001", "primitive", **{field: value}))

    problems = publisher.publish(root / "program.json", root, out, "2026-11-24", [registry])
    assert "privacy blocker: local machine path in knowledge/demo.jsonl" in problems
    assert "privacy blocker: local machine path in KNOWLEDGE.md" in problems
    assert not out.exists()


def test_publish_checks_retained_knowledge_privacy(tmp_path):
    root, _ = _project(tmp_path)
    out = tmp_path / "shared"
    registry = tmp_path / "demo.jsonl"
    _write_registry(registry, _knowledge_entry("K-001", "primitive"))
    assert publisher.publish(root / "program.json", root, out, "2026-11-24", [registry]) == []
    (out / "KNOWLEDGE.md").write_text("Reference " + "E:" + "/private/secret.md\n", encoding="utf-8")
    _write_registry(out / "knowledge" / registry.name,
                    _knowledge_entry("K-001", "primitive", status="retracted", scope=r"Local \\host\share"))
    manifest = (out / "MANIFEST.json").read_bytes()

    problems = publisher.publish(root / "program.json", root, out, "2026-11-25")
    assert problems == [
        "privacy blocker: local machine path in KNOWLEDGE.md",
        "privacy blocker: local machine path in knowledge/demo.jsonl",
    ]
    assert (out / "MANIFEST.json").read_bytes() == manifest


def test_publish_can_retract_placeholder_from_previous_mirror(tmp_path):
    root, _ = _project(tmp_path)
    program, out = root / "program.json", tmp_path / "shared"
    registry = tmp_path / "demo.jsonl"
    entry = _knowledge_entry("K-001", "primitive", statement="（待结卡填）")
    (out / "knowledge").mkdir(parents=True)
    _write_registry(out / "knowledge" / registry.name, entry)
    _write_registry(registry, {**entry, "status": "retracted"})

    assert publisher.publish(program, root, out, "2026-11-24", [registry]) == []
    assert "K-001: active → retracted" in (out / "CHANGES.md").read_text(encoding="utf-8")
    assert share.check_shared(out) == []


def test_publish_knowledge_logs_status_additions_and_prunes_removed_registries(tmp_path):
    root, prog = _project(tmp_path)
    program, out = root / "program.json", tmp_path / "shared"
    a, b = tmp_path / "a.jsonl", tmp_path / "b.JSONL"
    a_entry = _knowledge_entry("K-003", "primitive")
    _write_registry(a, a_entry)
    _write_registry(b, _knowledge_entry("K-003", "practice", source="other"))
    assert publisher.publish(program, root, out, "2026-11-24", [a, b]) == []
    log_path = out / "CHANGES.md"
    previous = log_path.read_text(encoding="utf-8")

    _write_registry(a, {**a_entry, "status": "superseded", "superseded_by": "K-004"},
                    _knowledge_entry("K-004", "primitive"))
    prog["propositions"][0]["status"] = "open"
    program.write_text(json.dumps(prog), encoding="utf-8")
    assert publisher.publish(program, root, out, "2026-11-25", [a]) == []

    log = log_path.read_text(encoding="utf-8")
    assert log.endswith(previous.split("\n\n", 1)[1])
    current = log.split("## 2026-11-25\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert "W1: partial → open" in current
    assert "知识库 K-003: active → superseded" in current
    assert "知识库 K-004 新增（primitive）" in current
    assert "知识库 K-003 移除" in current
    assert not (out / "knowledge" / b.name).exists()
    assert share.check_shared(out) == []

    before_noop = log_path.read_bytes()
    assert publisher.publish(program, root, out, "2026-11-26", [a]) == []
    assert log_path.read_bytes() == before_noop
    assert share.check_shared(out) == []

    assert publisher.publish(program, root, out, "2026-11-27", []) == []
    current = log_path.read_text(encoding="utf-8").split("\n## ", 2)[1]
    assert current.startswith("2026-11-27\n\n")
    assert "知识库 K-003 移除" in current and "知识库 K-004 移除" in current
    assert "无变化" not in current
    assert not list((out / "knowledge").iterdir())
    assert share.check_shared(out) == []


def test_publish_knowledge_problems_block_before_rewriting_shared_files(tmp_path):
    root, prog = _project(tmp_path)
    program, out = root / "program.json", tmp_path / "shared"
    good, bad = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    _write_registry(good, _knowledge_entry("K-001", "primitive"))
    assert publisher.publish(program, root, out, "2026-11-24", [good]) == []
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    prog["propositions"][0]["status"] = "open"
    program.write_text(json.dumps(prog), encoding="utf-8")

    _write_registry(bad, {"id": "K-002", "source": "demo"})
    assert any("K-002: missing kind" in issue for issue in publisher.publish(program, root, out, "2026-11-25", [good, bad]))
    bad.write_text("{unfinished\n", encoding="utf-8")
    assert any("bad JSON" in issue for issue in publisher.publish(program, root, out, "2026-11-25", [bad]))
    missing = tmp_path / "missing.jsonl"
    assert publisher.publish(program, root, out, "2026-11-25", [missing]) == [f"knowledge file not found: {missing}"]
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    assert share.check_shared(out) == []


def test_publish_registers_oversize_knowledge_and_summary(tmp_path):
    root, _ = _project(tmp_path)
    program, out = root / "program.json", tmp_path / "shared"
    registry = tmp_path / "big.jsonl"
    _write_registry(registry, _knowledge_entry("K-001", "primitive", statement="x" * 1_000_001))

    assert publisher.publish(program, root, out, "2026-11-24", [registry]) == []

    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    for name in ("knowledge/big.jsonl", "KNOWLEDGE.md"):
        assert manifest["file_size_limits"][name] == (out / name).stat().st_size > manifest["max_bytes"]
    assert share.check_shared(out) == []
