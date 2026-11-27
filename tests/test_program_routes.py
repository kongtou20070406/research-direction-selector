"""Acceptance tests for routes and automated review in program.py (locked before dispatch).

A route is one line of attack on one or more rungs (a model family, a task line, a proof
strategy). Routes open and close often; the program review turns their state into an agenda
without anyone rereading the history.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("program", ROOT / "skills/research-gate/scripts/program.py")
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)


def _prog():
    return {
        "schema": "research-program/v1", "program": "demo-program",
        "north_star": {"current": "S@1", "versions": [{"id": "S@1", "date": "2026-08-01", "statement": "s"}],
                       "amendments": [], "review_every_days": 60},
        "propositions": [
            {"id": "W1", "statement": "a", "anchored_to": "S@1", "depends_on": [], "status": "supported", "claims": ["C1"]},
            {"id": "W3", "statement": "b", "anchored_to": "S@1", "depends_on": ["W1"], "status": "partial", "claims": ["C2"]},
            {"id": "W5", "statement": "c", "anchored_to": "S@1", "depends_on": [], "status": "open", "claims": []},
        ],
        "claims": [
            {"id": "C1", "statement": "x", "strength": "observed", "status": "supported", "evidence": ["ledger:1"], "propositions": ["W1"], "anchored_to": "S@1", "route": "R-old"},
            {"id": "C2", "statement": "y", "strength": "observed", "status": "refuted", "evidence": ["ledger:2"], "refuted_by": ["ledger:3"], "propositions": ["W3"], "anchored_to": "S@1", "route": "R-anti"},
        ],
        "papers": [],
        "contributors": [{"id": "researcher", "kind": "person", "scope": "internal"}],
        "routes": [
            {"id": "R-old", "question": "q", "targets": ["W1"], "status": "active", "opened": "2026-10-01", "last_activity": "2026-11-23",
             "kill": {"falsifier": "T=64 diverges", "budget_gpu_hours": 20, "deadline": "2026-12-31"}, "spent_gpu_hours": 25, "cards": ["002"]},
            {"id": "R-anti", "question": "q", "targets": ["W3"], "status": "active", "opened": "2026-10-01", "last_activity": "2026-10-20",
             "kill": {"falsifier": "no repair beyond depth 3", "deadline": "2026-11-01"}, "cards": ["004"]},
            {"id": "R-jepa", "question": "q", "targets": ["W5"], "status": "retired", "opened": "2026-11-01", "retired_reason": "merged evidence into R-old",
             "kill": {"falsifier": "f"}, "cards": ["009"]},
        ],
    }


def test_routes_pass_check():
    assert program.check(_prog())[0] == []


def test_route_rules():
    p = _prog()
    p["routes"][0]["targets"] = ["W9"]
    p["routes"][1]["kill"] = {"falsifier": "f"}            # active without budget or deadline
    del p["routes"][2]["retired_reason"]
    p["routes"].append({"id": "R-old", "question": "dup", "targets": ["W1"], "status": "paused", "kill": {"falsifier": "f", "deadline": "2027-01-01"}})
    p["claims"][0]["route"] = "R-none"
    text = "\n".join(program.check(p)[0])
    for needle in ("W9", "R-anti", "retired_reason", "R-old", "R-none"):
        assert needle in text, needle


def test_review_builds_agenda():
    agenda = program.review(_prog(), today="2026-11-24", stale_days=14)
    assert agenda["over_budget"] == ["R-old"]
    assert agenda["past_deadline"] == ["R-anti"]
    assert agenda["stalled"] == ["R-anti"]                    # 35 days without activity
    assert agenda["retire_candidates"] == ["R-anti"]          # every claim it produced is refuted
    assert agenda["paper_candidates"] == ["W1"]               # supported claim not yet owned by a paper
    assert agenda["north_star_review_due"] is True            # last version 2026-08-01, every 60 days
    assert agenda["unattacked_rungs"] == ["W5"]              # only a retired route ever targeted W5


def test_review_quiet_program_and_owned_claims():
    p = _prog()
    p["routes"] = [r for r in p["routes"] if r["id"] == "R-old"]
    p["routes"][0]["spent_gpu_hours"] = 5
    p["north_star"]["last_review"] = "2026-11-20"
    p["papers"] = [{"id": "P1", "title": "t", "status": "drafting", "anchored_to": "S@1", "propositions": ["W1"],
                    "primary_claims": ["C1"], "background_claims": [], "contributors": [{"who": "researcher", "roles": ["writing"]}]}]
    agenda = program.review(p, today="2026-11-24", stale_days=14)
    assert agenda["over_budget"] == [] and agenda["stalled"] == [] and agenda["past_deadline"] == []
    assert agenda["paper_candidates"] == []
    assert agenda["north_star_review_due"] is False
    assert agenda["unattacked_rungs"] == ["W3", "W5"]         # open or partial rungs with no active or paused route


def test_route_new_and_cli(tmp_path):
    p = program.route_new(_prog(), route_id="R-new", question="Does damping fix W3?", targets=["W3"],
                          falsifier="no gain at depth 4", deadline="2026-12-20", budget_gpu_hours=10, opened="2026-11-24")
    route = next(r for r in p["routes"] if r["id"] == "R-new")
    assert route["status"] == "proposed" and route["cards"] == [] and route["last_activity"] == "2026-11-24"
    assert program.check(p)[0] == []
    path = tmp_path / "program.json"
    path.write_text(json.dumps(_prog()), encoding="utf-8")
    out = tmp_path / "agenda.json"
    assert program.main(["review", str(path), "--today", "2026-11-24", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["over_budget"] == ["R-old"]
    md = tmp_path / "agenda.md"
    assert program.main(["review", str(path), "--today", "2026-11-24", "--md", str(md)]) == 0
    assert "R-anti" in md.read_text(encoding="utf-8")


def _tree():
    """W3 is too big; route R-split breaks it into W3.1 (solved) and W3.2 (tractable leaf, ready)."""
    p = _prog()
    p["routes"].append({"id": "R-split", "question": "split W3", "targets": ["W3"], "status": "active", "opened": "2026-11-20",
                        "last_activity": "2026-11-24", "kill": {"falsifier": "a child is refuted", "deadline": "2027-01-01"}, "cards": []})
    p["propositions"] += [
        {"id": "W3.1", "statement": "one-step repair at depth 3", "parent": "W3", "route": "R-split", "anchored_to": "S@1",
         "depends_on": [], "status": "supported", "claims": ["C3"], "tractable": True},
        {"id": "W3.2", "statement": "repair survives depth 4", "parent": "W3", "route": "R-split", "anchored_to": "S@1",
         "depends_on": ["W3.1"], "status": "open", "claims": [], "tractable": True},
    ]
    p["claims"].append({"id": "C3", "statement": "23/200 at depth 3", "strength": "observed", "status": "supported", "evidence": ["ledger:4"],
                        "propositions": ["W3.1"], "anchored_to": "S@1", "route": "R-split"})
    return p


def test_decomposition_tree_rules():
    assert program.check(_tree())[0] == []
    p = _tree()
    p["propositions"][-1]["parent"] = "W404"
    p["propositions"][1]["parent"] = "W3.1"              # W3 under its own child: cycle
    p["propositions"][3]["tractable"] = True
    p["propositions"].append({"id": "W3.1.a", "statement": "x", "parent": "W3.1", "anchored_to": "S@1", "depends_on": [],
                              "status": "open", "claims": []})  # a tractable leaf may not be split further
    text = "\n".join(program.check(p)[0])
    assert "W404" in text and "cycle" in text and "W3.1" in text and "tractable" in text


def test_review_drives_split_solve_compose():
    agenda = program.review(_tree(), today="2026-11-24", stale_days=14)
    assert agenda["ready_leaves"] == ["W3.2"]             # tractable, open, prerequisites supported
    assert agenda["too_big"] == ["W5"]                    # open, no children, not tractable: split it
    assert agenda["ready_to_compose"] == []
    p = _tree()
    p["propositions"][-1]["status"] = "supported"
    p["propositions"][-1]["claims"] = ["C3"]
    agenda = program.review(p, today="2026-11-24", stale_days=14)
    assert agenda["ready_to_compose"] == ["W3"]           # every child of route R-split stands: go back up
    assert agenda["ready_leaves"] == []
    p["propositions"][-1]["status"] = "refuted"
    agenda = program.review(p, today="2026-11-24", stale_days=14)
    assert agenda["resplit"] == ["W3"]                    # a child of the active split failed: try another split
