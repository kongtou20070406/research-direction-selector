"""CPU replay acceptance for scoped rule changes, separate from scientific gain."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from rds_advisor_search import search_directions
from rds_experiments import compose_experiments
from rds_meta import digest, validate_rule

VERSION = "rds-rsi-replay-2"
LIMITS = {"max_cases": 64, "max_candidates": 64, "max_depth": 32,
          "max_nodes": 512, "max_combinations": 512, "max_compose_depth": 4,
          "max_runtime_ms": 10000}
EXPECTED_KEYS = {"required_actions", "required_blocked_rules", "forbidden_ready_actions",
                 "allowed_ready_actions", "required_queries", "required_interventions",
                 "forbidden_interventions", "minimum_candidates", "maximum_candidates", "no_ready"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def program_version():
    """Bind the replay engines, including the optional artifact importer."""
    base = Path(__file__).resolve().parent
    names = ("rds_rsi.py", "rds_rsi_confirmation.py", "rds_experiments.py",
             "rds_advisor_search.py", "rds_meta.py", "rds_artifacts.py",
             "rds_source_documents.py")
    files = {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
             for name in names if (base / name).exists()}
    return {"version": VERSION, "files": files, "sha256": digest(files)}


def commit_casepack(casepack):
    """Attach expectation hashes before evaluation; this is not a curator signature."""
    pack = deepcopy(casepack)
    pack["precommit"] = {"expected_sha256": digest({c["id"]: c["expected"] for c in pack["cases"]})}
    return pack


def _validate_cases(pack):
    if not isinstance(pack, dict) or pack.get("schema") != 1 or pack.get("purpose") != "REGRESSION_ACCEPTANCE":
        raise ValueError("Casepack schema 1 and REGRESSION_ACCEPTANCE purpose are required")
    if len(_json(pack).encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Casepack exceeds the 2 MiB replay input bound")
    budget = pack.get("budget", {})
    if not isinstance(budget, dict) or set(budget) != set(LIMITS):
        raise ValueError("Casepack must declare every replay budget dimension")
    for key, cap in LIMITS.items():
        if type(budget[key]) is not int or not 1 <= budget[key] <= cap:
            raise ValueError(f"Replay budget {key} must be 1..{cap}")
    cases = pack.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= budget["max_cases"]:
        raise ValueError("Zero cases or case count beyond the declared budget")
    ids = [c.get("id") for c in cases if isinstance(c, dict)]
    if len(ids) != len(cases) or any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Replay cases require unique nonempty ids")
    proposed, heldout = pack.get("proposed_on_ids"), pack.get("heldout_ids")
    if not isinstance(proposed, list) or not proposed or not isinstance(heldout, list) or not heldout:
        raise ValueError("Declared proposal-development ids and heldout ids are required")
    if not all(isinstance(i, str) and i.strip() for i in proposed + heldout):
        raise ValueError("Case partition ids must be strings")
    if len(set(proposed)) != len(proposed) or len(set(heldout)) != len(heldout) or set(proposed) & set(heldout):
        raise ValueError("Development and heldout ids must be unique and disjoint")
    if set(heldout) != {c["id"] for c in cases if c.get("partition") == "heldout"}:
        raise ValueError("Heldout declaration must match actual heldout cases")
    for case in cases:
        if case.get("partition") not in {"development", "heldout"}:
            raise ValueError("Case partition must be development or heldout")
        if case["partition"] == "development" and case["id"] not in proposed:
            raise ValueError("Development case absent from proposed_on_ids")
        if case.get("engine", "search") not in {"search", "compose"} or not isinstance(case.get("context"), dict):
            raise ValueError("Only bounded search/compose case contexts are supported")
        expected = case.get("expected")
        if not isinstance(expected, dict) or not expected or not set(expected) <= EXPECTED_KEYS:
            raise ValueError("Case requires explicit precommitted grader assertions")
        for key in ("required_actions",):
            if key in expected and (not isinstance(expected[key], dict) or not all(
                    isinstance(k, str) and v in {"READY", "NEEDS_EVIDENCE", "BLOCKED_BUDGET", "BLOCKED_PREREQUISITE"}
                    for k, v in expected[key].items())):
                raise ValueError("required_actions must map ids to declared statuses")
        for key in ("required_blocked_rules", "forbidden_ready_actions", "allowed_ready_actions", "required_queries"):
            if key in expected and (not isinstance(expected[key], list) or not all(isinstance(v, str) for v in expected[key])):
                raise ValueError(f"{key} must contain explicit ids")
        for key in ("required_interventions", "forbidden_interventions"):
            if key in expected and (not isinstance(expected[key], list) or not all(isinstance(v, dict) for v in expected[key])):
                raise ValueError(f"{key} must be intervention objects")
        for key in ("minimum_candidates", "maximum_candidates"):
            if key in expected and (type(expected[key]) is not int or not 0 <= expected[key] <= 64):
                raise ValueError(f"{key} must be 0..64")
        if "no_ready" in expected and type(expected["no_ready"]) is not bool:
            raise ValueError("no_ready must be boolean")
    if pack.get("precommit", {}).get("expected_sha256") != digest({c["id"]: c["expected"] for c in cases}):
        raise ValueError("Grader expectation commitment is absent or was rewritten")
    return cases, budget


def _check_graph(graph, budget):
    nodes, edges = graph.get("nodes"), graph.get("edges", [])
    if not isinstance(nodes, list) or len(nodes) > budget["max_nodes"] or not isinstance(edges, list) or len(edges) > 4096:
        raise ValueError("Graph exceeds the replay node/edge budget")
    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if len(ids) != len(nodes) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Graph node ids must be unique strings")
    parents = {rid: [] for rid in ids}
    for edge in edges:
        if isinstance(edge, dict) and edge.get("relation") == "prerequisite_for" and edge.get("to") in parents:
            parents[edge["to"]].append(edge.get("from"))
    done, active = set(), set()
    def visit(rid):
        if rid in active:
            raise ValueError("Cyclic prerequisite graph cannot be adopted")
        if rid in done or rid not in parents:
            return
        active.add(rid)
        for parent in parents[rid]:
            visit(parent)
        active.remove(rid)
        done.add(rid)
    for rid in ids:
        visit(rid)


def _grade(output, expected):
    rows = output.get("candidates", []) + output.get("blocked_candidates", [])
    actions = {}
    for row in rows:
        aid = row.get("action", {}).get("id") or row.get("action_id")
        if aid:
            actions.setdefault(aid, set()).add(row["status"])
    ready = {aid for aid, statuses in actions.items() if "READY" in statuses}
    ready.update(c["id"] for c in output.get("candidates", []) if c.get("status") == "READY" and "interventions" in c)
    blocked = {c.get("rule_id") for c in output.get("blocked_candidates", [])}
    queries = {q.get("fact") for q in output.get("queries", [])}
    queries.update(q.get("fact") for q in output.get("rule_search", {}).get("queries", []))
    interventions = {_json(i) for c in output.get("candidates", []) for i in c.get("interventions", [])}
    errors = []
    for aid, status in expected.get("required_actions", {}).items():
        # Presence is existential; ready-action constraints apply to every rule.
        if status not in actions.get(aid, set()):
            observed = sorted(actions[aid]) if aid in actions else "ABSENT"
            errors.append(f"action {aid}: expected {status}, observed {observed}")
    if not set(expected.get("required_blocked_rules", [])) <= blocked:
        errors.append("required blocked rule absent")
    forbidden = set(expected.get("forbidden_ready_actions", [])) & ready
    if forbidden:
        errors.append("forbidden action became READY: " + ", ".join(sorted(forbidden)))
    if "allowed_ready_actions" in expected and not ready <= set(expected["allowed_ready_actions"]):
        errors.append("undeclared action became READY: " + ", ".join(sorted(ready - set(expected["allowed_ready_actions"]))))
    if not set(expected.get("required_queries", [])) <= queries:
        errors.append("required evidence query absent")
    if not {_json(i) for i in expected.get("required_interventions", [])} <= interventions:
        errors.append("required intervention absent")
    if {_json(i) for i in expected.get("forbidden_interventions", [])} & interventions:
        errors.append("forbidden intervention proposed")
    count = len(output.get("candidates", []))
    if count < expected.get("minimum_candidates", 0) or count > expected.get("maximum_candidates", 64):
        errors.append("candidate count outside expected range")
    if expected.get("no_ready") and ready:
        errors.append("unexpected READY candidate: " + ", ".join(sorted(ready)))
    return {"passed": not errors, "errors": errors,
            "action_statuses": {aid: sorted(actions[aid]) for aid in sorted(actions)}}


def _run(graph, case, budget):
    limits = {key: budget[key] for key in ("max_candidates", "max_depth", "max_nodes")}
    if case.get("engine", "search") == "search":
        output = search_directions(graph, case["context"], **limits,
                                   max_combinations=budget["max_combinations"],
                                   max_compose_depth=budget["max_compose_depth"])
    else:
        output = compose_experiments(graph, case["context"], case.get("templates", []),
                                     max_candidates=budget["max_candidates"],
                                     max_depth=budget["max_compose_depth"],
                                     max_combinations=budget["max_combinations"], search_limits=limits)
    if output.get("cycles") or output.get("rule_search", {}).get("cycles"):
        raise ValueError("Replay encountered a cycle")
    if output["truncation"]["truncated"] or output.get("experiment_composition", {}).get("truncation", {}).get("truncated"):
        raise ValueError("Replay exceeded candidate, combination, depth or node limits")
    return output


def evaluate_candidate(rule, graph, casepack, *, confirmation_dir=None):
    """Run baseline and candidate on the same frozen, declared casepack.

    Heldout status is caller-declared, not independently sealed. Acceptance
    measures this finite regression change, never research-policy improvement.
    """
    started = time.perf_counter()
    report = {"status": "REJECTED", "assurance": "CPU_CASE_REPLAY", "adoption_eligible": False,
              "auto_apply": False, "research_policy_gain_measured": False, "cases_evaluated": 0,
              "replays_executed": 0, "results": [], "rejection_reasons": [],
              "limitations": ["Case expectations and heldout partitions are caller-declared; no sealed benchmark is claimed.",
                              "Finite rule-regression acceptance is not a measured research-policy gain."]}
    try:
        validate_rule(rule)
        if len(_json(rule).encode("utf-8")) > 256 * 1024 or len(_json(graph).encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("Rule or graph exceeds replay input size bound")
        cases, budget = _validate_cases(casepack)
        candidate_graph = deepcopy(graph)
        replaced = False
        for i, node in enumerate(candidate_graph["nodes"]):
            if node["id"] == rule["id"]:
                candidate_graph["nodes"][i] = deepcopy(rule)
                replaced = True
                break
        if not replaced:
            candidate_graph["nodes"].append(deepcopy(rule))
        _check_graph(graph, budget)
        _check_graph(candidate_graph, budget)
        report["bindings"] = {"candidate_sha256": digest(rule), "base_graph_sha256": digest(graph),
                              "casepack_sha256": digest(casepack), "program": program_version()}
        from rds_rsi_confirmation import record_exposure
        campaign = casepack.get("confirmation_campaign")
        if "confirmation_campaign" in casepack and campaign is None:
            raise ValueError("confirmation_campaign cannot be null")
        exposure = record_exposure(cases, report["bindings"], campaign, confirmation_dir)
        if exposure is not None:
            report["confirmation_exposure"] = exposure
        for case in cases:
            baseline = _run(graph, case, budget)
            candidate = _run(candidate_graph, case, budget)
            report["replays_executed"] += 2
            result = {"case_id": case["id"], "partition": case["partition"],
                      "baseline": baseline, "candidate": candidate,
                      "baseline_grade": _grade(baseline, case["expected"]),
                      "candidate_grade": _grade(candidate, case["expected"])}
            report["results"].append(result)
            report["cases_evaluated"] += 1
            if (time.perf_counter() - started) * 1000 > budget["max_runtime_ms"]:
                raise ValueError("Replay exceeded declared CPU wall-time budget")
        report["counts"] = {"baseline_passed": sum(r["baseline_grade"]["passed"] for r in report["results"]),
                            "candidate_passed": sum(r["candidate_grade"]["passed"] for r in report["results"]),
                            "heldout_cases": sum(r["partition"] == "heldout" for r in report["results"]),
                            "heldout_improvements": sum(r["partition"] == "heldout" and not r["baseline_grade"]["passed"]
                                                        and r["candidate_grade"]["passed"] for r in report["results"]),
                            "regressions": sum(r["baseline_grade"]["passed"] and not r["candidate_grade"]["passed"]
                                               for r in report["results"])}
        if report["counts"]["candidate_passed"] != len(cases):
            report["rejection_reasons"].append("Candidate fails precommitted case assertions")
        if not report["counts"]["heldout_improvements"]:
            report["rejection_reasons"].append("No demonstrated improvement on declared heldout cases")
        if report["counts"]["regressions"]:
            report["rejection_reasons"].append("Candidate regresses a previously passing case")
        report["replay_sha256"] = digest(report["results"])
        if not report["rejection_reasons"]:
            report.update(status="ACCEPTABLE_REGRESSION_CHANGE", adoption_eligible=True)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError, OSError) as exc:
        report["rejection_reasons"].append(str(exc))
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return report
