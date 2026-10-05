"""Admit bounded AI bridge definitions for testing; never adopt scientific facts."""
from collections import defaultdict
from copy import deepcopy
import json


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def _theory_bridge(proposal, gap, defined, frontier, nodes):
    """Check an explicit relation definition, never infer a theory hierarchy."""
    from rds_frontier import _source, _metadata, _date
    bridge = proposal.get("theory_bridge")
    if not isinstance(bridge, dict):
        return None, ["Theory reformulation requires theory_bridge"]
    errors = []
    bridge = deepcopy(bridge)
    bridge.setdefault("kind", "unspecified")
    bridge.setdefault("source_model", gap.get("current_model"))
    bridge.setdefault("preserved_claim", f"Original goal {gap['target']}; preservation unverified")
    bridge.setdefault("applicability_conditions", proposal.get("assumptions"))
    bridge.setdefault("changed_assumptions", [])
    kind = bridge["kind"]
    if not _text(kind):
        errors.append("Theory bridge kind, when supplied, must be a short description")
    if bridge["source_model"] != gap.get("current_model"):
        errors.append("Theory bridge must preserve the gap's current_model")
    candidate = bridge.get("candidate_model")
    if not isinstance(candidate, str) or candidate not in defined or candidate == gap.get("current_model"):
        errors.append("Theory bridge requires a distinct defined candidate_model")
    for field in ("mapping", "preserved_claim"):
        if not _text(bridge.get(field)):
            errors.append(f"Theory bridge requires {field}")
    for field in ("changed_assumptions", "applicability_conditions"):
        values = bridge.get(field)
        minimum = 0 if field == "changed_assumptions" else 1
        if not isinstance(values, list) or not minimum <= len(values) <= 16 or not all(_text(v) for v in values):
            errors.append(f"Theory bridge requires {minimum}..16 {field}")
    if kind == "approximation":
        for field in ("domain", "error_bound"):
            if not _text(bridge.get(field)):
                errors.append(f"Approximation requires an explicit {field}")
    cutoff = frontier.get("as_of")
    for record in [bridge, *[node for node in nodes if isinstance(node, dict)]]:
        if not _source(record.get("source")):
            errors.append("Theory bridge and new nodes require source locators")
        else:
            try:
                _metadata(record["source"], "theory source")
            except ValueError as exc:
                errors.append(str(exc))
        when = record.get("available_on")
        if cutoff and when is None:
            errors.append("Theory bridge/new node has UNKNOWN_AVAILABILITY at as_of")
        elif when is not None:
            try:
                if cutoff and _date(when, "theory available_on") > _date(cutoff, "as_of"):
                    errors.append("Theory bridge/new node is a FUTURE_RECORD at as_of")
                else:
                    _date(when, "theory available_on")
            except ValueError as exc:
                errors.append(str(exc))
    if errors:
        return None, errors
    fields = ("kind", "source_model", "candidate_model", "mapping", "preserved_claim",
              "changed_assumptions", "applicability_conditions", "available_on", "domain", "error_bound")
    result = {key: deepcopy(bridge[key]) for key in fields if key in bridge}
    from rds_frontier import _ref
    result.update(source=_ref("theory_bridge", {"id": proposal["id"], "source": bridge["source"]})["source"],
                  relation_status="PROPOSED", applicability_status="UNKNOWN", preservation_status="UNKNOWN")
    return result, []


def formal_gate(statement, certificate=None, *, generate=False):
    """Replay a declared side condition; never infer it from graph relatedness."""
    if not isinstance(statement, dict):
        return {"status": "UNKNOWN", "assurance": "NONE", "admitted": False,
                "reason": "No explicit formal obligation"}
    from rds_verify import checked_result, verify
    if certificate is None and not generate:
        result = {"status": "UNKNOWN", "assurance": "NONE",
                  "reason": "A bound certificate is required"}
    else:
        result = verify(statement) if certificate is None else checked_result(statement, certificate)
    # A conditional probability law is a theorem fact, not evidence that an
    # experiment supplies independent draws or a nonnegative supermartingale.
    admitted = (result.get("status") == "PASS"
                and result.get("assurance") == "LEAN_KERNEL_CHECKED"
                and isinstance(result.get("certificate"), dict)
                and result.get("application_status", "PASS") == "PASS")
    return {**result, "admitted": admitted,
            "claim_relation": "declared_side_condition_only"}


def review_proposals(frontier, spec, pack):
    """Check a reply against the current gap and return a proposed subgraph.

    IDs, finite structure, gap coverage and a discriminating test are checked.
    Prediction truth, causal validity, test cost and execution remain unknown.
    """
    if (not isinstance(pack, dict) or type(pack.get("schema_version")) is not int
            or pack["schema_version"] != 1 or not isinstance(pack.get("proposals"), list)
            or len(pack["proposals"]) > 16):
        raise ValueError("Proposal pack schema_version=1 requires at most 16 proposals")
    if len(json.dumps(pack, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 128 * 1024:
        raise ValueError("Proposal pack exceeds 128 KiB")
    gaps = {gap["id"]: gap for gap in frontier["gaps"]}
    excluded = {row["id"] for row in frontier["excluded"] if row["record_type"] == "nodes"}
    limits = frontier.get("truncation", {}).get("limits", {})
    existing = {node["id"] for node in [node for node in spec.get("nodes", [])
                if node["id"] not in excluded][:limits.get("max_nodes", 512)]}
    excluded_edges = {row["id"] for row in frontier["excluded"] if row["record_type"] == "edges"}
    available_edges = [edge for index, edge in enumerate(spec.get("edges", []))
        if f"edge:{index}" not in excluded_edges
        and edge.get("from") in existing and edge.get("to") in existing][:limits.get("max_edges", 4096)]
    results, seen = [], set()
    for proposal in pack["proposals"]:
        if not isinstance(proposal, dict):
            raise ValueError("Each proposal must be an object")
        pid = proposal.get("id")
        if not _text(pid) or pid in seen:
            raise ValueError("Proposal IDs must be distinct nonempty strings")
        seen.add(pid)
        action_kind = proposal.get("action_kind", "path_repair")
        if action_kind in ("knowledge_expansion", "structural_reconstruction"):
            results.append(review_exploration(frontier, spec, proposal))
            continue
        if action_kind != "path_repair":
            raise ValueError("Unknown proposal action_kind")
        gap = gaps.get(proposal.get("gap_id")) if isinstance(proposal.get("gap_id"), str) else None
        formal_gap = gap is not None and gap.get("kind") == "FORMAL_OBLIGATION"
        theory_gap = gap is not None and gap.get("kind") == "THEORY_REFORMULATION"
        errors = []
        if gap is None:
            errors.append("gap_id is not an open gap in this frontier result")
        if "theory_bridge" in proposal and not theory_gap:
            errors.append("Theory bridge requires a goal.reformulation frontier gap")
        nodes, relations = proposal.get("new_nodes", []), proposal.get("relations", [])
        if (not isinstance(nodes, list) or len(nodes) > 16 or not isinstance(relations, list)
                or not (0 if formal_gap else 1) <= len(relations) <= 32):
            errors.append("Require at most 16 new nodes and bounded proposed relations")
            nodes, relations = [], []
        defined = set(existing)
        for node in nodes:
            if (not isinstance(node, dict) or not _text(node.get("id")) or node["id"] in defined
                    or node.get("kind") not in ("observable", "model", "concept", "requirement", "operation")
                    or not _text(node.get("label"))):
                errors.append("New nodes require fresh IDs, known kinds and labels")
            else:
                defined.add(node["id"])
        bridge = None
        if theory_gap:
            bridge, bridge_errors = _theory_bridge(proposal, gap, defined, frontier, nodes)
            errors.extend(bridge_errors)
            if bridge is not None:
                kinds = {node["id"]: node.get("kind") for node in spec.get("nodes", [])}
                kinds.update({node["id"]: node.get("kind") for node in nodes if isinstance(node, dict) and "id" in node and isinstance(node["id"], str)})
                if kinds.get(bridge["candidate_model"]) not in ("model", "concept"):
                    errors.append("Candidate theory must name a model or concept node")
        adjacency = defaultdict(set)
        for edge in available_edges:
            if edge.get("status") == "SUPPORTED" and (gap is None or not gap.get("relations")
                    or edge.get("relation") in gap["relations"]):
                adjacency[edge["from"]].add((edge["to"], False))
        for relation in relations:
            if (not isinstance(relation, dict) or not isinstance(relation.get("from"), str)
                    or not isinstance(relation.get("to"), str) or relation["from"] not in defined
                    or relation["to"] not in defined or not _text(relation.get("relation"))):
                errors.append("Relations require defined endpoints and a named relation")
            elif gap is None or not gap.get("relations") or relation["relation"] in gap["relations"]:
                adjacency[relation["from"]].add((relation["to"], True))
        if formal_gap:
            if proposal.get("formal_obligation") != gap.get("formal_obligation"):
                errors.append("Formal proposal must preserve the gap's exact obligation")
        elif gap is not None:
            starts = ([gap["current_model"]] if theory_gap else
                      [anchor for anchor in gap["anchors"] if anchor != gap["target"]])
            visited = {(anchor, False) for anchor in starts}
            pending = list(visited)
            while pending:
                node, used_proposal = pending.pop()
                for child, proposed in adjacency[node]:
                    state = (child, used_proposal or proposed)
                    if state not in visited:
                        visited.add(state)
                        pending.append(state)
            if (gap["target"], True) not in visited:
                errors.append("Proposed relations do not bridge a gap anchor to its target using its required relation types")
            if theory_gap and bridge is not None:
                candidate = bridge["candidate_model"]
                after = {(candidate, False)}
                pending = list(after)
                while pending:
                    node, used = pending.pop()
                    for child, proposed in adjacency[node]:
                        state = (child, used or proposed)
                        if state not in after:
                            after.add(state)
                            pending.append(state)
                if not any((candidate, first) in visited and (gap["target"], second) in after
                           and (first or second) for first in (False, True) for second in (False, True)):
                    errors.append("Candidate theory must lie on the proposed current_model-to-goal path")
        assumptions = proposal.get("assumptions")
        if not isinstance(assumptions, list) or not 1 <= len(assumptions) <= 16 or not all(_text(x) for x in assumptions):
            errors.append("Declare 1..16 assumptions")
        test = proposal.get("test")
        proof_check = theory_gap and isinstance(test, dict) and test.get("kind") == "proof_check"
        prediction = proposal.get("prediction")
        if not proof_check and (not isinstance(prediction, dict) or not isinstance(prediction.get("observable"), str)
                or prediction["observable"] not in defined or not _text(prediction.get("if_proposal"))
                or not _text(prediction.get("if_rival"))
                or prediction["if_proposal"].strip() == prediction["if_rival"].strip()):
            errors.append("Prediction requires a defined observable and distinct proposal/rival outcomes")
        if not isinstance(test, dict) or not all(_text(test.get(key)) for key in ("protocol", "measurement", "stop_condition")):
            errors.append("Test requires protocol, measurement and stop_condition")
        if theory_gap and isinstance(test, dict):
            if test.get("kind", "empirical") not in ("empirical", "proof_check"):
                errors.append("Reformulation test kind must be empirical or proof_check")
            if proof_check and (not isinstance(test.get("outcomes"), list)
                                or sorted(test["outcomes"], key=str) != ["counterexample", "unresolved", "verified"]):
                errors.append("Proof check requires verified/counterexample/unresolved outcomes")
        positive, negative = proposal.get("next_if_positive"), proposal.get("next_if_negative")
        if not _text(positive) or not _text(negative) or positive.strip() == negative.strip():
            errors.append("Positive and negative results must change different next decisions")
        report = {"id": pid, "gap_id": proposal.get("gap_id"),
                  "status": "NEEDS_DEFINITION" if errors else "NEEDS_EVIDENCE",
                  "definition_errors": errors, "scientific_support": "UNKNOWN",
                  "evidence_status": "INPUT_REPORTED", "execution_authorized": False,
                  "cost": {"status": "UNKNOWN"}}
        if not errors:
            # Only the typed proposal fields survive. Caller success/support flags
            # cannot create evidence or replace the original research question.
            report["subgraph"] = {"nodes": [{key: node[key] for key in ("id", "kind", "label")} for node in nodes], "edges": [
                {"from": edge["from"], "to": edge["to"], "relation": edge["relation"], "status": "PROPOSED"}
                for edge in relations]}
            report.update(assumptions=deepcopy(assumptions),
                          test={key: test[key] for key in ("protocol", "measurement", "stop_condition")},
                          next_if_positive=positive, next_if_negative=negative,
                          evidence_refs=deepcopy(gap["evidence_refs"]), original_decision=gap.get("decision"))
            if not proof_check:
                report["prediction"] = {key: prediction[key] for key in ("observable", "if_proposal", "if_rival")}
            if theory_gap:
                report["theory_bridge"] = bridge
                report["test"]["kind"] = test.get("kind", "empirical")
                if proof_check:
                    report["test"]["outcomes"] = list(test["outcomes"])
                from rds_frontier import _ref
                report["evidence_refs"] += [_ref("theory_bridge", {"id": pid, "source": proposal["theory_bridge"]["source"]})]
                report["evidence_refs"] += [_ref("node", node) for node in nodes]
                for output_node, input_node in zip(report["subgraph"]["nodes"], nodes):
                    output_node["source"] = _ref("node", input_node)["source"]
                    if "available_on" in input_node:
                        output_node["available_on"] = input_node["available_on"]
                gate = {"status": "UNKNOWN", "assurance": "NONE", "admitted": False,
                        "reason": "Review the exact mapping and application premises through a separately bound check"}
            else:
                gate = formal_gate(proposal.get("formal_obligation"),
                                   proposal.get("formal_certificate"), generate=True)
            report["formal_gate"] = gate
            if isinstance(proposal.get("formal_obligation"), dict):
                report["formal_obligation"] = deepcopy(proposal["formal_obligation"])
            report["candidate_eligible"] = gate["admitted"]
        else:
            report["candidate_eligible"] = False
        results.append(report)
    return {"schema_version": 1, "advisor_type": "FRONTIER_PROPOSAL_REVIEW", "proposals": results,
            "candidate_pool": [row["id"] for row in results if row["candidate_eligible"]],
            "adopted_relations": 0, "executed_tests": 0,
            "limitations": ["Distinct prediction text does not establish experimental discriminability.",
                            "Native proof admission covers only the declared mathematical side condition.",
                            "Conditional statistical laws require separately closed application premises.",
                            "This definition check does not validate causal claims, cost, code or scientific gains."]}


def review_exploration(frontier, spec, proposal):
    """Admit a testable open exploration, without demanding an anchor-goal path.

    This opt-in contract is deliberately separate from the legacy bridge
    requirement. It never admits an executable experiment or a theorem.
    """
    from rds_frontier import _source, _metadata, _date
    errors = []
    gap = next((g for g in frontier['gaps'] if g['id'] == proposal.get('gap_id')), None)
    if gap is None:
        errors.append('gap_id is not an open gap in this frontier result')
    exploration = proposal.get('exploration', {})
    if not isinstance(exploration, dict):
        exploration = {}
    for key in ('limitation', 'change', 'rationale'):
        if not _text(exploration.get(key)):
            errors.append('Exploration requires ' + key)
    sources = exploration.get('sources')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 16:
        errors.append('Exploration requires 1..16 separately classified sources')
    else:
        for source in sources:
            if (not isinstance(source, dict) or source.get('kind') not in
                    {'literature_claim', 'agent_interpretation', 'local_observation'}
                    or not _source(source.get('source'))):
                errors.append('Source requires provenance kind and locator')
            else:
                try:
                    _metadata(source, 'exploration source')
                except ValueError as exc:
                    errors.append(str(exc))
    for key in ('unknown_premises', 'search_directions'):
        values = exploration.get(key)
        if not isinstance(values, list) or not 1 <= len(values) <= 16 or not all(_text(v) for v in values):
            errors.append('Exploration requires 1..16 ' + key)
    cost = exploration.get('cost')
    from rds_frontier import _finite
    if (not isinstance(cost, dict) or not cost or len(cost) > 16
            or not all(_text(k) and _finite(v) and v >= 0 for k, v in cost.items())
            or cost.get('wall_seconds', 0) <= 0):
        errors.append('Exploration requires finite resource caps including positive wall_seconds')
    nodes = proposal.get('new_nodes', [])
    relations = proposal.get('relations', [])
    if not isinstance(nodes, list) or len(nodes) > 16 or not isinstance(relations, list) or len(relations) > 32:
        nodes, relations = [], []
        errors.append('Exploration nodes/relations exceed limits')
    excluded = {r['id'] for r in frontier.get('excluded', []) if r.get('record_type') == 'nodes'}
    limits = frontier.get('truncation', {}).get('limits', {})
    defined = {n['id'] for n in [n for n in spec.get('nodes', []) if n['id'] not in excluded][:limits.get('max_nodes', 512)]}
    for node in nodes:
        if (not isinstance(node, dict) or not _text(node.get('id')) or node['id'] in defined
                or node.get('kind') not in {'observable', 'model', 'concept', 'requirement', 'operation'}
                or not _text(node.get('label')) or not _source(node.get('source'))):
            errors.append('New concepts require fresh ID, kind, label and source')
        else:
            defined.add(node['id'])
            try:
                _metadata(node['source'], 'new concept source')
                if frontier.get('as_of'):
                    if node.get('available_on') is None:
                        errors.append('New concept has UNKNOWN_AVAILABILITY at as_of')
                    elif _date(node['available_on'], 'concept available_on') > _date(frontier['as_of'], 'as_of'):
                        errors.append('New concept is a FUTURE_RECORD at as_of')
            except ValueError as exc:
                errors.append(str(exc))
    if proposal.get('action_kind') == 'knowledge_expansion' and not nodes and not relations:
        errors.append('Knowledge expansion must add a concept or relation')
    for relation in relations:
        if (not isinstance(relation, dict) or relation.get('from') not in defined
                or relation.get('to') not in defined or not _text(relation.get('relation'))):
            errors.append('Relations require defined endpoints and relation')
    assumptions = proposal.get('assumptions')
    if not isinstance(assumptions, list) or not 1 <= len(assumptions) <= 16 or not all(_text(v) for v in assumptions):
        errors.append('Declare 1..16 assumptions')
    prediction, test = proposal.get('prediction'), proposal.get('test')
    if 'discriminator' in proposal:
        from rds_discrimination import validate
        try:
            validate(proposal['discriminator'])
        except ValueError as exc:
            errors.append(str(exc))
    if (not isinstance(prediction, dict) or prediction.get('observable') not in defined
            or not all(_text(prediction.get(k)) for k in ('if_proposal', 'if_rival'))
            or prediction['if_proposal'].strip() == prediction['if_rival'].strip()):
        errors.append('Prediction requires a defined observable and distinct outcomes')
    if not isinstance(test, dict) or not all(_text(test.get(k)) for k in ('protocol', 'measurement', 'stop_condition')):
        errors.append('Test requires protocol, measurement and stop_condition')
    positive, negative = proposal.get('next_if_positive'), proposal.get('next_if_negative')
    if not _text(positive) or not _text(negative) or positive.strip() == negative.strip():
        errors.append('Outcomes must change different next decisions')
    report = {'id': proposal['id'], 'gap_id': proposal.get('gap_id'), 'action_kind': proposal['action_kind'],
              'status': 'NEEDS_DEFINITION' if errors else 'NEEDS_EVIDENCE', 'definition_errors': errors,
              'scientific_support': 'UNKNOWN', 'execution_authorized': False, 'candidate_eligible': False,
              'formal_gate': {'status': 'UNKNOWN', 'assurance': 'NONE', 'admitted': False},
              'complete_goal_path_required': False}
    if not errors:
        report.update(exploration=deepcopy(exploration), assumptions=deepcopy(assumptions),
                      prediction=deepcopy(prediction), test=deepcopy(test),
                      next_if_positive=positive, next_if_negative=negative,
                      evidence_refs=deepcopy(gap['evidence_refs']), cost={'status': 'DECLARED_CAP', 'resources': deepcopy(cost)},
                      subgraph={'nodes': [{k: n[k] for k in ('id', 'kind', 'label', 'source')} for n in nodes],
                                'edges': [{**{k: r[k] for k in ('from', 'to', 'relation')}, 'status': 'PROPOSED'} for r in relations]})
    return report
