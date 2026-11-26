"""Acceptance tests for skills/research-gate/scripts/program.py (locked before dispatch).

A research program links a revisable north star, a ladder of weak propositions, a registry
of claims with evidence, the papers cut from those claims, and the people or agents who
contributed. The checker keeps those links honest with zero LLM tokens.
"""

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("program", ROOT / "skills/research-gate/scripts/program.py")
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)


def _prog():
    return {
        "schema": "research-program/v1",
        "program": "loop-agi",
        "north_star": {
            "current": "S@2",
            "versions": [
                {"id": "S@1", "date": "2026-11-19", "statement": "A time-homogeneous operator whose attractors are evidence-consistent hypotheses."},
                {"id": "S@2", "date": "2026-12-01", "statement": "Same operator, stated for world models."},
            ],
            "amendments": [{"date": "2026-12-01", "from": "S@1", "to": "S@2", "quote": "改北极星", "reason": "scope", "evidence": ["C2"]}],
        },
        "propositions": [
            {"id": "W1", "statement": "homogeneous extrapolation", "anchored_to": "S@2", "depends_on": [], "status": "supported", "claims": ["C1"]},
            {"id": "W3", "statement": "weak evidence stability", "anchored_to": "S@1", "depends_on": ["W1"], "status": "partial", "claims": ["C2", "C3"]},
        ],
        "claims": [
            {"id": "C1", "statement": "T=64 does not diverge", "strength": "observed", "status": "supported", "evidence": ["ledger:exp-011"], "propositions": ["W1"], "anchored_to": "S@2"},
            {"id": "C2", "statement": "lambda moves inconsistent states", "strength": "proved", "status": "supported", "evidence": ["proofs/W3AntiReset.lean"], "propositions": ["W3"], "anchored_to": "S@2"},
            {"id": "C3", "statement": "anti-reset helps on Push-T", "strength": "observed", "status": "refuted", "evidence": ["ledger:exp-015"], "refuted_by": ["ledger:exp-019"], "propositions": ["W3"], "anchored_to": "S@2"},
        ],
        "papers": [
            {"id": "P-AB", "title": "RecurrentWM", "status": "drafting", "anchored_to": "S@2", "propositions": ["W1", "W3"],
             "primary_claims": ["C1", "C2"], "background_claims": [], "contributors": [{"who": "lead", "roles": ["conceptualization", "writing"]}, {"who": "worker", "roles": ["software"]}]},
            {"id": "P-A", "title": "old paper A", "status": "merged", "merged_into": "P-AB", "anchored_to": "S@1", "propositions": ["W1"],
             "primary_claims": ["C1"], "background_claims": [], "contributors": [{"who": "lead", "roles": ["writing"]}]},
        ],
        "contributors": [
            {"id": "lead", "kind": "person", "scope": "internal"},
            {"id": "worker", "kind": "agent", "scope": "internal", "model": "engineering-worker"},
            {"id": "alice", "kind": "person", "scope": "external"},
        ],
    }


def test_valid_program_has_no_errors_and_warns_on_old_anchor():
    errors, warnings = program.check(_prog())
    assert errors == []
    assert any("W3" in w and "S@1" in w for w in warnings)  # re-anchor needed, not invalid
    assert not any("P-A" in w for w in warnings)  # merged papers keep their historical anchor


def test_north_star_and_references_must_resolve():
    p = _prog()
    p["north_star"]["current"] = "S@9"
    p["claims"][0]["propositions"] = ["W9"]
    p["papers"][0]["contributors"].append({"who": "bob", "roles": ["writing"]})
    p["propositions"][1]["depends_on"] = ["W3"]  # cycle
    text = "\n".join(program.check(p)[0])
    for needle in ("S@9", "W9", "bob", "cycle"):
        assert needle in text, needle


def test_claim_evidence_rules():
    p = _prog()
    p["claims"][0]["evidence"] = []                     # supported without evidence
    p["claims"][1]["evidence"] = ["ledger:exp-007"]     # proved without a formal proof file
    del p["claims"][2]["refuted_by"]                    # refuted without refuted_by
    text = "\n".join(program.check(p)[0])
    assert "C1" in text and "evidence" in text
    assert "C2" in text and "proved" in text
    assert "C3" in text and "refuted_by" in text


def test_paper_rules_primary_ownership_refuted_use_and_submission():
    p = _prog()
    p["papers"].append({"id": "P-C", "title": "salami", "status": "drafting", "anchored_to": "S@2", "propositions": ["W1"],
                        "primary_claims": ["C1"], "background_claims": ["C3"], "contributors": [{"who": "kongtou", "roles": ["writing"]}]})
    errors = "\n".join(program.check(p)[0])
    assert "C1" in errors and "P-AB" in errors and "P-C" in errors   # one primary owner among active papers
    assert "C3" in errors and "refuted" in errors                   # an active paper may not lean on a refuted claim

    q = _prog()
    q["claims"][0]["status"] = "proposed"
    q["papers"][0]["status"] = "submitted"
    assert any("P-AB" in e and "C1" in e for e in program.check(q)[0])  # submit only supported primary claims


def test_merge_must_carry_primary_claims():
    p = _prog()
    p["papers"][1]["primary_claims"] = ["C2", "C3"]  # C3 never reached P-AB
    assert any("P-A" in e and "C3" in e for e in program.check(p)[0])
    p["papers"][1]["merged_into"] = "P-ZZ"
    assert any("P-ZZ" in e for e in program.check(p)[0])


def test_proposition_status_needs_supporting_claim():
    p = _prog()
    p["propositions"][0]["claims"] = ["C3"]  # only a refuted claim behind a supported proposition
    assert any("W1" in e for e in program.check(p)[0])


def test_amending_the_north_star_keeps_results_and_flags_reanchoring():
    p = _prog()
    new = program.amend_north_star(p, new_id="S@3", date="2027-01-10", statement="Weaker star", quote="改一下", reason="evidence", evidence=["C2"])
    assert p["north_star"]["current"] == "S@2"  # input is not mutated
    assert new["north_star"]["current"] == "S@3"
    assert new["north_star"]["amendments"][-1]["from"] == "S@2"
    errors, warnings = program.check(new)
    assert errors == []
    assert len([w for w in warnings if "S@3" in w]) >= 3  # W1, C1.., P-AB now need re-anchoring
    for claim in new["claims"]:
        assert claim["status"] == next(c for c in p["claims"] if c["id"] == claim["id"])["status"]


def test_export_for_external_collaborator_is_scoped():
    p = _prog()
    p["papers"].append({"id": "P-X", "title": "with alice", "status": "planned", "anchored_to": "S@2", "propositions": ["W1"],
                        "primary_claims": [], "background_claims": ["C1"], "contributors": [{"who": "alice", "roles": ["investigation"]}]})
    view = program.export_for(p, "alice")
    assert [x["id"] for x in view["papers"]] == ["P-X"]
    assert [c["id"] for c in view["claims"]] == ["C1"]
    assert [w["id"] for w in view["propositions"]] == ["W1"]
    assert {c["id"] for c in view["contributors"]} == {"alice"}
    assert view["north_star"]["current"] == "S@2" and "amendments" not in view["north_star"]


def test_report_and_cli(tmp_path):
    path = tmp_path / "program.json"
    path.write_text(json.dumps(_prog(), ensure_ascii=False), encoding="utf-8")
    text = program.report(program.load(path))
    assert "W1" in text and "W3" in text and "P-AB" in text
    assert program.main(["check", str(path)]) == 0
    bad = copy.deepcopy(_prog())
    bad["north_star"]["current"] = "S@9"
    path.write_text(json.dumps(bad), encoding="utf-8")
    assert program.main(["check", str(path)]) == 1
    out = tmp_path / "alice.json"
    (tmp_path / "proofs").mkdir()
    (tmp_path / "proofs" / "W3AntiReset.lean").write_text("theorem t : True := trivial", encoding="utf-8")
    path.write_text(json.dumps(_prog()), encoding="utf-8")
    assert program.main(["export", str(path), "--for", "kongtou", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["program"] == "loop-agi"


def test_propose_claim_never_guesses_across_projects(tmp_path):
    prog = _prog()
    state, _, new = program.propose_claim(prog, "C-card001", "s", "W3", ["ledger:exp-020"], "card/001")
    assert state == "added" and new["claims"][-1]["status"] == "proposed" and "proposition_inferred_from" not in new["claims"][-1]
    assert program.propose_claim(new, "C-x", "s", "W3", [], "card/001")[0] == "exists"   # same source
    assert program.propose_claim(prog, "C-card002", "s", "C1/C2", [], "card/002")[0] == "needs_proposition"  # no focus
    prog["focus"] = {"active": ["W3"]}
    state, _, new = program.propose_claim(prog, "C-card002", "s", "C1/C2", [], "card/002")
    assert state == "added" and new["claims"][-1]["propositions"] == ["W3"]
    assert new["claims"][-1]["proposition_inferred_from"] == "focus"
    prog["focus"] = {"active": ["W1", "W3"]}
    assert program.propose_claim(prog, "C-card003", "s", "?", [], "card/003")[0] == "needs_proposition"
    path = tmp_path / "program.json"
    (tmp_path / "proofs").mkdir()
    (tmp_path / "proofs" / "W3AntiReset.lean").write_text("theorem t : True := trivial", encoding="utf-8")
    path.write_text(json.dumps(_prog()), encoding="utf-8")
    assert program.main(["claim-propose", str(path), "--id", "C-card004", "--statement", "s", "--proposition", "W1",
                         "--source", "card/004"]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["claims"][-1]["id"] == "C-card004"
