"""The Obsidian board renders the split tree, agenda, routes and ladder from program.json."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("program_board", ROOT / "skills/research-gate/scripts/program_board.py")
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
routes_spec = importlib.util.spec_from_file_location("routes_fixture", ROOT / "tests/test_program_routes.py")
routes_fixture = importlib.util.module_from_spec(routes_spec)
routes_spec.loader.exec_module(routes_fixture)


def test_board_shows_tree_agenda_and_routes(tmp_path):
    path = tmp_path / "program.json"
    path.write_text(json.dumps(routes_fixture._tree(), ensure_ascii=False), encoding="utf-8")
    text = board.build(path, today="2026-11-24")
    assert "```mermaid" in text and "flowchart TD" in text
    assert "n_W3 -->|R-split| n_W3_1" in text            # split edge labelled by its route
    assert "可以开工的叶子" in text and "`W3.2`" in text   # ready leaf
    assert "太大、需要再拆" in text and "`W5`" in text
    assert "北极星该复审了" in text
    assert "| `R-split` | active | W3 |" in text
    out = tmp_path / "board.md"
    assert board.main([str(path), "--out", str(out), "--today", "2026-11-24"]) == 0 and out.is_file()


def test_board_renders_full_hierarchy_in_table_and_mermaid(tmp_path):
    prog = routes_fixture._tree()
    prog["propositions"][-1]["statement"] = "Does the full repair criterion hold | at depth 4?"
    path = tmp_path / "program.json"
    path.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")

    text = board.build(path, today="2026-11-24")
    table_header = "### 表格视图（完整问题与层级）"
    flow_header = "### 流程图（Mermaid）"
    assert text.index(table_header) < text.index(flow_header)
    table, flow = text.split(table_header, 1)[1].split(flow_header, 1)
    root = "| **`W3`** |"
    first_child = "| ├─ `W3.1` | `R-split` |"
    last_child = "| └─ `W3.2` | `R-split` |"
    assert table.index(root) < table.index(first_child) < table.index(last_child)
    assert "| 需再拆 |" in next(row for row in table.splitlines() if row.startswith(root))
    assert "Does the full repair criterion hold \\| at depth 4? | 未做 | ✓可解 |" in table
    assert "n_W3 -->|R-split| n_W3_1" in flow
    assert "n_W3 -->|R-split| n_W3_2" in flow


def test_board_respects_focus_and_lists_paper_gaps(tmp_path):
    prog = routes_fixture._tree()
    prog["focus"] = {"since": "2026-11-24", "active": ["W3"], "note": "paper first"}
    prog["papers"] = [{"id": "P1", "title": "t", "status": "drafting", "anchored_to": "S@1", "propositions": ["W3"],
                       "primary_claims": ["C3", "C2"], "background_claims": []}]
    path = tmp_path / "program.json"
    path.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
    text = board.build(path, today="2026-11-24")
    agenda = text.split("## 论文还缺的证据")[0]
    assert "当前焦点" in text and "paper first" in text
    assert "可以开工的叶子" in agenda and "`W3.2`" in agenda          # W3.2 is under the focus node
    assert "搁置中（不排进待办）" in agenda and "`W5`" in agenda.split("搁置中")[1]
    assert "`W5` " not in agenda.split("搁置中")[0]                     # parked, not on the to-do list
    assert "**P1**" in text and "`C2`" in text and "refuted" in text     # the paper still needs C2
