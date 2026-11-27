"""Validate a research program's north star, propositions, claims, and papers.
Track amendments without changing existing results or their anchors.
Provide a plain-text report and contributor-scoped JSON exports.
"""

import sys

sys.dont_write_bytecode = True

import argparse
import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from graphlib import CycleError, TopologicalSorter
from pathlib import Path


SCHEMA = "research-program/v1"
PROPOSITION_STATUSES = ("open", "partial", "supported", "refuted", "retired")
CLAIM_STATUSES = ("proposed", "supported", "refuted", "retracted")
INACTIVE_PAPER_STATUSES = ("merged", "abandoned")
RELEASED_PAPER_STATUSES = ("submitted", "accepted", "published")
PAPER_STATUSES = ("planned", "drafting", *RELEASED_PAPER_STATUSES, *INACTIVE_PAPER_STATUSES)
ROUTE_STATUSES = ("proposed", "active", "paused", "retired", "merged")


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _index(items, kind, errors):
    by_id = {}
    for item in items:
        item_id = item["id"]
        if item_id in by_id:
            errors.append(f"duplicate {kind} id {item_id}")
        else:
            by_id[item_id] = item
    return by_id


def _validate_enum(item, field, allowed, errors):
    value = item.get(field)
    if value not in allowed:
        errors.append(f"{item['id']}: invalid {field} {value!r}")


def _validate_refs(owner, field, refs, index, errors):
    for ref in refs:
        if ref not in index:
            errors.append(f"{owner}: {field} references unknown id {ref!r}")


def _validate_evidence(owner, field, entries, root, errors):
    if root is None:
        return
    for entry in entries:
        has_drive = len(entry) >= 3 and entry[0].isalpha() and entry[1] == ":" and entry[2] in "/\\"
        is_path_like = (
            has_drive or "/" in entry or "\\" in entry
            or entry.lower().endswith((".lean", ".py", ".json", ".md", ".csv", ".tex", ".pdf", ".log", ".txt", ".png"))
        )
        if (":" in entry and not has_drive) or not is_path_like:
            continue
        relative = Path(entry.replace("\\", "/"))
        exists_under_root = False
        if not relative.anchor:
            try:
                path = (root / relative).resolve()
                exists_under_root = path.is_relative_to(root) and path.exists()
            except (OSError, RuntimeError, ValueError):
                pass
        if not exists_under_root:
            errors.append(f"{owner}: {field} path {entry!r} does not exist under {root}")


def _validate_dependencies(propositions, errors):
    graph = {
        item_id: [ref for ref in item.get("depends_on", []) if ref in propositions]
        for item_id, item in propositions.items()
    }
    _validate_acyclic(graph, "dependency", errors)


def _validate_acyclic(graph, relation, errors):
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        errors.append(f"proposition {relation} cycle: " + " -> ".join(exc.args[1]))


def _validate_decomposition(propositions, errors):
    graph = {}
    for item_id, item in propositions.items():
        if "parent" not in item:
            continue
        parent_id = item["parent"]
        _validate_refs(item_id, "parent", [parent_id], propositions, errors)
        if parent_id in propositions:
            graph[item_id] = [parent_id]
            if propositions[parent_id].get("tractable"):
                errors.append(f"{parent_id}: tractable proposition may not have child {item_id}")
    _validate_acyclic(graph, "parent", errors)


def _validate_route(route, routes, propositions, errors):
    route_id = route["id"]
    status = route.get("status")
    _validate_enum(route, "status", ROUTE_STATUSES, errors)
    _validate_refs(route_id, "targets", route.get("targets", []), propositions, errors)
    if status == "active":
        kill = route.get("kill") or {}
        if not kill.get("falsifier") or (
            kill.get("budget_gpu_hours") is None and not kill.get("deadline")
        ):
            errors.append(f"{route_id}: active route requires kill.falsifier and a budget_gpu_hours or deadline")
    if status == "retired" and not route.get("retired_reason"):
        errors.append(f"{route_id}: retired route requires retired_reason")
    if status == "merged":
        _validate_refs(route_id, "merged_into", [route.get("merged_into")], routes, errors)


def _validate_claim(claim, propositions, root, errors):
    claim_id = claim["id"]
    status = claim.get("status")
    evidence = claim.get("evidence", [])
    refuted_by = claim.get("refuted_by", [])
    _validate_enum(claim, "status", CLAIM_STATUSES, errors)
    _validate_enum(claim, "strength", ("observed", "supported", "proved"), errors)
    _validate_refs(claim_id, "propositions", claim.get("propositions", []), propositions, errors)
    if status == "supported" and not evidence:
        errors.append(f"{claim_id}: supported claim requires evidence")
    if claim.get("strength") == "proved" and not any(entry.endswith(".lean") for entry in evidence):
        errors.append(f"{claim_id}: proved claim requires .lean evidence")
    if status == "refuted" and not refuted_by:
        errors.append(f"{claim_id}: refuted claim requires refuted_by")
    _validate_evidence(claim_id, "evidence", evidence, root, errors)
    _validate_evidence(claim_id, "refuted_by", refuted_by, root, errors)


def _paper_claims(paper):
    return [*paper.get("primary_claims", []), *paper.get("background_claims", [])]


def _validate_paper_refs(paper, papers, claims, propositions, contributors, errors):
    paper_id = paper["id"]
    for field, index in (
        ("propositions", propositions), ("primary_claims", claims), ("background_claims", claims),
    ):
        _validate_refs(paper_id, field, paper.get(field, []), index, errors)
    people = (credit.get("who") for credit in paper.get("contributors", []))
    _validate_refs(paper_id, "contributors.who", people, contributors, errors)

    target_id = paper.get("merged_into")
    if paper.get("status") == "merged" or target_id is not None:
        _validate_refs(paper_id, "merged_into", [target_id], papers, errors)
    if paper.get("status") == "merged" and target_id in papers:
        carried = set(_paper_claims(papers[target_id]))
        for claim_id in paper.get("primary_claims", []):
            if claim_id not in carried:
                errors.append(f"{paper_id}: merged_into {target_id} is missing primary claim {claim_id}")


def _validate_active_paper(paper, claims, primary_owners, errors):
    paper_id = paper["id"]
    status = paper.get("status")
    for claim_id in dict.fromkeys(_paper_claims(paper)):
        claim_status = claims.get(claim_id, {}).get("status")
        if claim_status in ("refuted", "retracted"):
            errors.append(f"{paper_id}: active paper uses {claim_status} claim {claim_id} (refuted or retracted claims are forbidden)")

    for claim_id in dict.fromkeys(paper.get("primary_claims", [])):
        owner = primary_owners.setdefault(claim_id, paper_id)
        if owner != paper_id:
            errors.append(f"{claim_id}: primary claim of active papers {owner} and {paper_id}")
        if status in RELEASED_PAPER_STATUSES and claim_id in claims:
            if claims[claim_id].get("status") != "supported":
                errors.append(f"{paper_id}: {status} paper requires supported primary claim {claim_id}")


def check(prog, root: Path | None = None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if prog.get("schema") != SCHEMA:
        errors.append(f"program {prog.get('program')!r}: invalid schema {prog.get('schema')!r}")

    root = Path(root).resolve() if root is not None else None
    north_star = prog.get("north_star", {})
    current = north_star.get("current")
    versions = _index(north_star.get("versions", []), "north_star version", errors)
    propositions = _index(prog.get("propositions", []), "proposition", errors)
    claims = _index(prog.get("claims", []), "claim", errors)
    papers = _index(prog.get("papers", []), "paper", errors)
    contributors = _index(prog.get("contributors", []), "contributor", errors)
    routes = _index(prog.get("routes", []), "route", errors)

    _validate_refs("north_star", "current", [current], versions, errors)
    for amendment in north_star.get("amendments", []):
        previous, target = amendment.get("from"), amendment.get("to")
        owner = f"north_star amendment {previous!r} to {target!r}"
        _validate_refs(owner, "from", [previous], versions, errors)
        _validate_refs(owner, "to", [target], versions, errors)
        _validate_evidence(owner, "evidence", amendment.get("evidence", []), root, errors)

    active_papers = [paper for paper in papers.values() if paper.get("status") not in INACTIVE_PAPER_STATUSES]
    for item in (*propositions.values(), *claims.values(), *papers.values()):
        _validate_refs(item["id"], "anchored_to", [item.get("anchored_to")], versions, errors)
    for item in (*propositions.values(), *claims.values(), *active_papers):
        anchor = item.get("anchored_to")
        if anchor != current:
            warnings.append(f"re-anchor {item['id']}: anchored to {anchor}, current {current}")

    for item_id, proposition in propositions.items():
        _validate_enum(proposition, "status", PROPOSITION_STATUSES, errors)
        _validate_refs(item_id, "depends_on", proposition.get("depends_on", []), propositions, errors)
        linked_claims = proposition.get("claims", [])
        _validate_refs(item_id, "claims", linked_claims, claims, errors)
        if proposition.get("status") == "supported" and not any(
            claims.get(claim_id, {}).get("status") == "supported" for claim_id in linked_claims
        ):
            errors.append(f"{item_id}: supported proposition requires a supported claim")
    _validate_dependencies(propositions, errors)
    _validate_decomposition(propositions, errors)

    for route in routes.values():
        _validate_route(route, routes, propositions, errors)
    for item in (*propositions.values(), *claims.values()):
        if "route" in item:
            _validate_refs(item["id"], "route", [item["route"]], routes, errors)

    for claim in claims.values():
        _validate_claim(claim, propositions, root, errors)
    for contributor in contributors.values():
        _validate_enum(contributor, "kind", ("person", "agent"), errors)
        _validate_enum(contributor, "scope", ("internal", "external"), errors)
    for paper in papers.values():
        _validate_enum(paper, "status", PAPER_STATUSES, errors)
        _validate_paper_refs(paper, papers, claims, propositions, contributors, errors)
    primary_owners = {}
    for paper in active_papers:
        _validate_active_paper(paper, claims, primary_owners, errors)
    return errors, warnings


def amend_north_star(prog, new_id, date, statement, quote, reason, evidence) -> dict:
    amended = deepcopy(prog)
    north_star = amended["north_star"]
    north_star["versions"].append({"id": new_id, "date": date, "statement": statement})
    north_star.setdefault("amendments", []).append({
        "date": date,
        "from": north_star["current"],
        "to": new_id,
        "quote": quote,
        "reason": reason,
        "evidence": deepcopy(evidence),
    })
    north_star["current"] = new_id
    return amended


def export_for(prog, contributor_id) -> dict:
    papers = [
        paper for paper in prog.get("papers", [])
        if any(credit["who"] == contributor_id for credit in paper.get("contributors", []))
    ]
    claim_ids = {claim_id for paper in papers for claim_id in _paper_claims(paper)}
    claims = [claim for claim in prog.get("claims", []) if claim["id"] in claim_ids]
    proposition_ids = {item_id for item in papers + claims for item_id in item.get("propositions", [])}
    contributor_ids = {credit["who"] for paper in papers for credit in paper.get("contributors", [])}
    north_star = prog["north_star"]
    current = north_star["current"]
    return deepcopy({
        "schema": prog["schema"],
        "program": prog["program"],
        "north_star": {
            "current": current,
            "versions": [version for version in north_star["versions"] if version["id"] == current],
        },
        "propositions": [item for item in prog.get("propositions", []) if item["id"] in proposition_ids],
        "claims": claims,
        "papers": papers,
        "contributors": [item for item in prog.get("contributors", []) if item["id"] in contributor_ids],
    })


def report(prog) -> str:
    claims = {claim["id"]: claim for claim in prog.get("claims", [])}
    lines = ["Proposition ladder", " | ".join(("ID", "Status", *CLAIM_STATUSES))]
    for proposition in prog.get("propositions", []):
        counts = Counter(
            claims[claim_id].get("status")
            for claim_id in proposition.get("claims", []) if claim_id in claims
        )
        values = (proposition["id"], proposition.get("status"), *(counts[status] for status in CLAIM_STATUSES))
        lines.append(" | ".join(map(str, values)))

    lines.extend(["", "Papers", "ID | Status | Primary claims"])
    for paper in prog.get("papers", []):
        primary = ", ".join(paper.get("primary_claims", []))
        lines.append(f"{paper['id']} | {paper.get('status')} | {primary}")
    _, warnings = check(prog)
    lines.extend(["", f"Warnings: {len(warnings)}"])
    return "\n".join(lines)


def _review_routes(routes, claims, today, stale_days):
    claim_statuses = defaultdict(set)
    for claim in claims:
        claim_statuses[claim.get("route")].add(claim.get("status"))

    agenda = {key: [] for key in ("over_budget", "past_deadline", "stalled", "retire_candidates")}
    for route_id, route in routes.items():
        if route.get("status") != "active":
            continue
        kill = route.get("kill") or {}
        budget = kill.get("budget_gpu_hours")
        deadline = kill.get("deadline")
        last_activity = route.get("last_activity")
        if budget is not None and route.get("spent_gpu_hours", 0) > budget:
            agenda["over_budget"].append(route_id)
        if deadline and today > date.fromisoformat(deadline):
            agenda["past_deadline"].append(route_id)
        if last_activity and (today - date.fromisoformat(last_activity)).days > stale_days:
            agenda["stalled"].append(route_id)
        statuses = claim_statuses[route_id]
        if statuses and statuses <= {"refuted", "retracted"}:
            agenda["retire_candidates"].append(route_id)
    return agenda


def _review_propositions(propositions, routes):
    children = defaultdict(lambda: defaultdict(list))
    for item in propositions.values():
        if "parent" in item:
            children[item["parent"]][item.get("route")].append(item.get("status"))

    attacked = set()
    for route in routes.values():
        status = route.get("status")
        if status in ("active", "paused"):
            attacked.update(route.get("targets", []))

    agenda = {key: [] for key in ("unattacked_rungs", "too_big", "ready_leaves", "ready_to_compose", "resplit")}
    for item_id, item in propositions.items():
        status = item.get("status")
        splits = children.get(item_id, {})
        if status in ("open", "partial"):
            if item.get("tractable"):
                if all(
                    propositions.get(ref, {}).get("status") == "supported"
                    for ref in item.get("depends_on", [])
                ):
                    agenda["ready_leaves"].append(item_id)
            elif not splits:
                agenda["too_big"].append(item_id)
                if item_id not in attacked:
                    agenda["unattacked_rungs"].append(item_id)

        for route_id, statuses in splits.items():
            if route_id not in routes:
                continue
            route_status = routes[route_id].get("status")
            if status != "supported" and route_status != "retired" and all(
                child_status == "supported" for child_status in statuses
            ):
                agenda["ready_to_compose"].append(item_id)
            if route_status == "active" and "refuted" in statuses:
                agenda["resplit"].append(item_id)
    return agenda


def _paper_candidates(propositions, claims, papers):
    owned_claims = {
        claim_id for paper in papers if paper.get("status") not in INACTIVE_PAPER_STATUSES
        for claim_id in paper.get("primary_claims", [])
    }
    available_claims = {
        claim["id"]: claim for claim in claims
        if claim.get("status") == "supported" and claim["id"] not in owned_claims
    }
    candidates = {
        item_id for item_id, item in propositions.items()
        if any(claim_id in available_claims for claim_id in item.get("claims", []))
    }
    candidates.update(
        item_id for claim in available_claims.values()
        for item_id in claim.get("propositions", []) if item_id in propositions
    )
    return candidates


def _north_star_review_due(north_star, today):
    current = north_star.get("current")
    current_date = next(
        (version.get("date") for version in north_star.get("versions", []) if version["id"] == current),
        None,
    )
    dates = [date.fromisoformat(value) for value in (current_date, north_star.get("last_review")) if value]
    return bool(dates) and (today - max(dates)).days > north_star.get("review_every_days", 90)


def review(prog, today: str, stale_days: int = 14) -> dict:
    """Build an agenda from route activity, evidence, and proposition splits."""
    review_date = date.fromisoformat(today)
    routes = {route["id"]: route for route in prog.get("routes", [])}
    propositions = {item["id"]: item for item in prog.get("propositions", [])}
    claims = prog.get("claims", [])
    agenda = _review_routes(routes, claims, review_date, stale_days)
    agenda.update(_review_propositions(propositions, routes))
    agenda["paper_candidates"] = _paper_candidates(propositions, claims, prog.get("papers", []))
    result = {key: sorted(set(ids)) for key, ids in agenda.items()}
    result["north_star_review_due"] = _north_star_review_due(prog.get("north_star", {}), review_date)
    _, warnings = check(prog)
    result["reanchor"] = [warning for warning in warnings if warning.startswith("re-anchor")]
    return result


def route_new(prog, route_id, question, targets, falsifier, opened, deadline=None, budget_gpu_hours=None) -> dict:
    """Return a copy of the program with one proposed route appended."""
    result = deepcopy(prog)
    kill = {"falsifier": falsifier}
    if deadline is not None:
        kill["deadline"] = deadline
    if budget_gpu_hours is not None:
        kill["budget_gpu_hours"] = budget_gpu_hours
    result.setdefault("routes", []).append({
        "id": route_id,
        "question": question,
        "targets": list(targets),
        "status": "proposed",
        "opened": opened,
        "last_activity": opened,
        "kill": kill,
        "cards": [],
    })
    return result


def propose_claim(prog, claim_id, statement, proposition, evidence, source, root: Path | None = None):
    """Add a ``status: proposed`` claim, e.g. from a closed experiment card. Returns (state, detail, program).

    state is ``added``, ``exists`` (same id or same source), ``needs_proposition`` or ``invalid``; only
    ``added`` returns a changed program. When ``proposition`` is not in the program and the focus has
    exactly one active node, that node is used and the claim records ``proposition_inferred_from:
    "focus"`` so a person confirms it; with no focus or several active nodes nothing is guessed.
    Missing evidence files are dropped (when ``root`` is given); the result must pass ``check``.
    """
    claims = prog.get("claims", [])
    if any(c.get("id") == claim_id or (source and c.get("source") == source) for c in claims):
        return "exists", claim_id, prog
    props = {p["id"] for p in prog.get("propositions", [])}
    claim = {"id": claim_id, "statement": statement, "strength": "observed", "status": "proposed"}
    if proposition not in props:
        active = [a for a in (prog.get("focus") or {}).get("active") or [] if a in props]
        if len(active) != 1:
            hint = f"focus has {len(active)} active nodes ({', '.join(active) or 'none'})"
            return "needs_proposition", f"{proposition!r} is not a proposition and {hint}; name one", prog
        proposition = active[0]
        claim["proposition_inferred_from"] = "focus"
    if root is not None:
        evidence = [e for e in evidence if (Path(root) / e).exists()]
    claim.update(evidence=list(evidence), propositions=[proposition],
                 anchored_to=(prog.get("north_star") or {}).get("current"))
    if source:
        claim["source"] = source
    new = deepcopy(prog)
    new.setdefault("claims", []).append(claim)
    errors, _ = check(new, root)
    if errors:
        return "invalid", "; ".join(errors[:3]), prog
    return "added", claim_id, new


def _agenda_markdown(agenda):
    lines = ["# Review agenda"]
    for section, entries in agenda.items():
        if entries:
            lines.extend(["", f"## {section}", ""])
            if entries is True:
                lines.append("- Due")
            else:
                lines.extend(f"- {entry}" for entry in entries)
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage a research program JSON file.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "report", "export", "amend"):
        subparser = commands.add_parser(command)
        subparser.add_argument("path", type=Path)
        if command == "check":
            subparser.add_argument("--root", type=Path)
        if command in ("export", "amend"):
            subparser.add_argument("--out", type=Path, required=True)
        if command == "export":
            subparser.add_argument("--for", dest="contributor_id", required=True)
        if command == "amend":
            for field in ("id", "date", "statement", "quote", "reason"):
                subparser.add_argument(f"--{field}", required=True)
            subparser.add_argument("--evidence", nargs="*", action="extend", default=[])

    review_parser = commands.add_parser("review")
    review_parser.add_argument("path", type=Path)
    review_parser.add_argument("--today", required=True)
    review_parser.add_argument("--stale-days", type=int, default=14)
    review_parser.add_argument("--out", type=Path)
    review_parser.add_argument("--md", type=Path)

    route_parser = commands.add_parser("route-new")
    route_parser.add_argument("path", type=Path)
    for field in ("id", "question", "falsifier", "opened"):
        route_parser.add_argument(f"--{field}", required=True)
    route_parser.add_argument("--targets", nargs="+", required=True)
    route_parser.add_argument("--deadline")
    route_parser.add_argument("--budget-gpu-hours", type=float)
    route_parser.add_argument("--out", type=Path, required=True)

    claim_parser = commands.add_parser("claim-propose", help="add a status: proposed claim (e.g. from a closed card)")
    claim_parser.add_argument("path", type=Path)
    for field in ("id", "statement", "proposition"):
        claim_parser.add_argument(f"--{field}", required=True)
    claim_parser.add_argument("--evidence", nargs="*", default=[])
    claim_parser.add_argument("--source", default="")
    claim_parser.add_argument("--root", type=Path, help="project root for evidence paths (default: the file's folder)")
    claim_parser.add_argument("--out", type=Path, help="default: overwrite PATH")

    args = parser.parse_args(argv)
    prog = load(args.path)
    if args.command == "claim-propose":
        state, detail, new = propose_claim(prog, args.id, args.statement, args.proposition, args.evidence,
                                           args.source, args.root or args.path.parent)
        if state == "added":
            out = args.out or args.path
            tmp = out.with_suffix(out.suffix + ".tmp")
            tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            tmp.replace(out)
        print(f"CLAIM {state} {detail}")
        return 0 if state in ("added", "exists") else 1
    if args.command == "check":
        errors, warnings = check(prog, args.root)
        for error in errors:
            print(f"ERROR: {error}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        return int(bool(errors))
    if args.command == "report":
        print(report(prog))
        return 0
    if args.command == "export":
        result = export_for(prog, args.contributor_id)
    elif args.command == "amend":
        result = amend_north_star(prog, args.id, args.date, args.statement, args.quote, args.reason, args.evidence)
    elif args.command == "route-new":
        result = route_new(
            prog, args.id, args.question, args.targets, args.falsifier, args.opened,
            deadline=args.deadline, budget_gpu_hours=args.budget_gpu_hours,
        )
    else:
        result = review(prog, args.today, args.stale_days)
        if args.md:
            args.md.write_text(_agenda_markdown(result), encoding="utf-8")
        if args.md and not args.out:
            return 0
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
