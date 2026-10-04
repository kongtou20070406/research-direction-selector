"""Bounded AND/OR dependency analysis of reported labels, never a proof kernel.

Schema: nodes=[{id,status,source}], hyperedges=[{id,premises,conclusion,
status,source}], goals=[node_id], optional limits. A source is a locator
string or {locator,file?,sha256?}. Unknown nodes can be proved directly;
their evidence atoms are ``node:<id>``. Proposed rules add ``rule:<id>``.
Only UNKNOWN leaves default to direct evidence atoms; derived nodes need
explicit allow_direct_evidence=true for a separate direct-proof route.
SUPPORTED closure never uses those hypothetical evidence atoms.

Evidence bindings (V2 slice): a node or hyperedge may declare
``evidence: {"receipt": {"project_root": ..., "sha256": ...}}`` naming one
receipt in a project ledger. Receipt-bound records enter the SUPPORTED
closure only when ``--audit-receipts`` (or ``audit_receipts_enabled=True``)
grounds the binding against the named ledger - a receipt with that sha256
whose ``run_status`` is ``SUCCEEDED``. Declaring a binding without running
the audit is fail-closed: the record stays out of the closure and is listed
in ``receipt_blocked_node_ids``. Matching bytes and succeeded
runs are not statement verification: the assurance string is unchanged.

Retraction (V2 slice): flipping any one derivation's status to CONTRADICTED
or UNKNOWN is expressed by editing that record's status in the input map and
re-running analysis; the closure recomputes from scratch, so a conclusion
with a remaining healthy OR route (a different SUPPORTED hyperedge with
grounded premises) keeps its support. Only conclusions whose every route
lost support are retracted, and aggregate failure never names a guilty
premise.
"""
import argparse
from copy import deepcopy
import hashlib
import heapq
import json
import sqlite3
from pathlib import Path

ASSURANCE = "INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF"
DEFAULT_LIMITS = {"max_nodes": 256, "max_hyperedges": 512,
                  "max_blocker_sets": 128, "max_combinations": 50000,
                  "max_source_bytes": 8 * 1024 * 1024}
HARD_LIMITS = {"max_nodes": 4096, "max_hyperedges": 16384,
               "max_blocker_sets": 2048, "max_combinations": 1000000,
               "max_source_bytes": 64 * 1024 * 1024}


class _Truncated(Exception):
    pass


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _source(value):
    if isinstance(value, str):
        _require(bool(value.strip()), "source locator must be nonempty")
        return
    _require(isinstance(value, dict) and isinstance(value.get("locator"), str)
             and value["locator"].strip(), "source must contain a locator")
    if "file" in value or "sha256" in value:
        _require(isinstance(value.get("file"), str) and value["file"],
                 "auditable source requires file and sha256")
        digest = value.get("sha256")
        _require(isinstance(digest, str) and len(digest) == 64
                 and all(c in "0123456789abcdefABCDEF" for c in digest),
                 "source sha256 must have 64 hexadecimal characters")


def _evidence(value, kind, ident):
    _require(value is None or isinstance(value, dict), f"{kind} {ident} evidence must be an object")
    if value is None:
        return None
    _require(set(value) == {"receipt"}, f"{kind} {ident} evidence supports only receipt bindings")
    receipt = value["receipt"]
    _require(isinstance(receipt, dict) and set(receipt) == {"project_root", "sha256"},
             f"{kind} {ident} receipt binding needs project_root and sha256")
    _require(isinstance(receipt["project_root"], str) and receipt["project_root"].strip(),
             f"{kind} {ident} receipt project_root must be a nonempty path")
    digest = receipt["sha256"]
    _require(isinstance(digest, str) and len(digest) == 64
             and all(c in "0123456789abcdefABCDEF" for c in digest),
             f"{kind} {ident} receipt sha256 must have 64 hexadecimal characters")
    return {"receipt": {"project_root": receipt["project_root"], "sha256": digest.lower()}}


def _validate(spec):
    _require(isinstance(spec, dict) and spec.get("schema", 1) == 1,
             "hypergraph schema must be 1")
    limits = dict(DEFAULT_LIMITS)
    supplied = spec.get("limits", {})
    _require(isinstance(supplied, dict) and not (set(supplied) - set(limits)),
             "unknown hypergraph limit")
    limits.update(supplied)
    for name, value in limits.items():
        _require(type(value) is int and 1 <= value <= HARD_LIMITS[name],
                 "limits must be bounded positive integers: " + name)
    nodes, edges, goals = [spec.get(name) for name in ("nodes", "hyperedges", "goals")]
    _require(isinstance(nodes, list) and len(nodes) <= limits["max_nodes"],
             "max_nodes exceeded or nodes is not a list")
    _require(isinstance(edges, list) and len(edges) <= limits["max_hyperedges"],
             "max_hyperedges exceeded or hyperedges is not a list")
    _require(isinstance(goals, list) and len(goals) <= limits["max_nodes"],
             "goals must be a bounded list")
    index, edge_ids = {}, set()
    for node in nodes:
        _require(isinstance(node, dict), "node must be an object")
        ident = node.get("id")
        _require(isinstance(ident, str) and 0 < len(ident) <= 256
                 and ident not in index, "invalid or duplicate node id")
        _require(node.get("status") in ("SUPPORTED", "UNKNOWN", "CONTRADICTED"),
                 "invalid node status")
        _require("allow_direct_evidence" not in node
                 or type(node["allow_direct_evidence"]) is bool,
                 "allow_direct_evidence must be boolean")
        _source(node.get("source"))
        if node.get("evidence") is not None:
            node["evidence"] = deepcopy(node["evidence"])
            node["evidence"] = _evidence(node["evidence"], "node", ident)
        index[ident] = node
    for edge in edges:
        _require(isinstance(edge, dict), "hyperedge must be an object")
        ident, tails, head = edge.get("id"), edge.get("premises"), edge.get("conclusion")
        _require(isinstance(ident, str) and 0 < len(ident) <= 256
                 and ident not in edge_ids, "invalid or duplicate hyperedge id")
        edge_ids.add(ident)
        _require(isinstance(tails, list) and all(isinstance(t, str) and t in index for t in tails)
                 and len(tails) == len(set(tails)), "invalid or duplicate premises")
        _require(isinstance(head, str) and head in index, "unknown conclusion")
        _require(edge.get("status") in ("SUPPORTED", "PROPOSED", "CONTRADICTED"),
                 "invalid hyperedge status")
        _source(edge.get("source"))
        if edge.get("evidence") is not None:
            edge["evidence"] = deepcopy(edge["evidence"])
            edge["evidence"] = _evidence(edge["evidence"], "hyperedge", ident)
    _require(all(isinstance(goal, str) and goal in index for goal in goals)
             and len(goals) == len(set(goals)), "unknown or duplicate goal")
    return index, edges, goals, limits


def audit_sources(spec, base_dir=None):
    """Check optional file digests; matching bytes cannot verify a statement."""
    nodes, edges, _, limits = _validate(spec)
    remaining = limits["max_source_bytes"]
    rows = []
    base = Path.cwd() if base_dir is None else Path(base_dir)
    for kind, records in (("node", nodes.values()), ("rule", edges)):
        for record in records:
            source = record["source"]
            if not isinstance(source, dict) or "file" not in source:
                continue
            path = Path(source["file"])
            if not path.is_absolute():
                path = base / path
            row = {"kind": kind, "id": record["id"], "source": deepcopy(source)}
            try:
                size = path.stat().st_size
                if size > remaining:
                    row["status"] = "BYTE_LIMIT_EXCEEDED"
                else:
                    with path.open("rb") as stream:
                        raw = stream.read(remaining + 1)
                    if len(raw) > remaining:
                        row["status"] = "BYTE_LIMIT_EXCEEDED"
                    else:
                        remaining -= len(raw)
                        actual = hashlib.sha256(raw).hexdigest()
                        row.update(status="MATCH" if actual == source["sha256"].lower() else "MISMATCH",
                                   actual_sha256=actual)
            except OSError as exc:
                row.update(status="UNAVAILABLE", reason=type(exc).__name__)
            rows.append(row)
    return {"assurance": "FILE_BYTES_ONLY_NOT_STATEMENT_VERIFICATION", "audits": rows,
            "all_requested_files_match": all(row["status"] == "MATCH" for row in rows)}


def read_project_receipt(root_text, digest_sha):
    """Read one receipt body by sha256 from a project ledger, read-only.

    Returns ``{"status": "RECEIPT_FOUND", "body": {...}}``, ``RECEIPT_NOT_FOUND``,
    ``RECEIPT_AMBIGUOUS`` when more than one stored receipt carries the sha256,
    ``RECEIPT_BODY_INVALID`` with the type of a stored body that is not a JSON object
    or with the owning ledger's integrity failure (#118), or ``LEDGER_UNAVAILABLE``
    with the exception type; the caller judges the body.
    """
    try:
        from rds_project import ProjectStore, ReceiptIntegrityError
        store = ProjectStore(root_text)
        with store._db(True) as db:
            hits = db.execute("SELECT run_id,sha256,body FROM receipts WHERE sha256=? LIMIT 2",
                              (digest_sha,)).fetchall()
        if not hits:
            return {"status": "RECEIPT_NOT_FOUND"}
        if len(hits) > 1:
            # The ledger writer never repeats a sha256 (it covers the run_id key); no row order picks one.
            return {"status": "RECEIPT_AMBIGUOUS"}
        row = hits[0]
        raw = row["body"]
        if not isinstance(raw, (str, bytes)):
            # An untyped imported column can hold NULL or a number; neither is stored JSON text.
            kind = "null" if raw is None else "integer" if isinstance(raw, int) else "real"
            return {"status": "RECEIPT_BODY_INVALID", "reason": "sqlite " + kind}
        try:
            # The owner's own check: one value per key, its run, its recorded and recomputed sha256.
            return {"status": "RECEIPT_FOUND", "body": ProjectStore._receipt(row)}
        except ReceiptIntegrityError as exc:
            failure = str(exc)
        body = json.loads(raw)  # Malformed or overdeep JSON stays an unavailable ledger, as before.
        if not isinstance(body, dict):
            # An imported or corrupted row is data, not a receipt: nothing is read out of it.
            kind = ("null" if body is None else "boolean" if isinstance(body, bool) else "array"
                    if isinstance(body, list) else "string" if isinstance(body, str) else "number")
            return {"status": "RECEIPT_BODY_INVALID", "reason": kind}
        # Keep only the failure: the run name in the owner's message is text from the rejected row.
        prefix = f"Project receipt integrity failure: run {row['run_id']} body "
        failure = failure.removeprefix(prefix).removesuffix("; inspect retained state")
        if failure == "is not valid JSON":
            # The plain decode accepted it, so only the owner's extra hook frame hit the recursion
            # limit: the same too-deep body as one level deeper, never a second classification.
            return {"status": "LEDGER_UNAVAILABLE", "reason": "RecursionError"}
        return {"status": "RECEIPT_BODY_INVALID", "reason": failure}
    except (ValueError, FileNotFoundError, OSError, RuntimeError, sqlite3.Error) as exc:
        # RuntimeError: Path.resolve() on a symlink loop; the ledger is unreadable, not a crash.
        return {"status": "LEDGER_UNAVAILABLE", "reason": type(exc).__name__}


def receipt_reader():
    """One operation's receipt lookup: each declared (project_root, sha256) pair is read at most once.

    The key is the declared pair itself, so different root texts are never merged into one
    identity, and a failed read is reused as the same fail-closed result.
    """
    seen = {}

    def read(root_text, digest_sha):
        if (root_text, digest_sha) not in seen:
            seen[root_text, digest_sha] = read_project_receipt(root_text, digest_sha)
        return deepcopy(seen[root_text, digest_sha])

    return read


def audit_receipts(spec, read_receipt=None):
    """Verify receipt-bound evidence against frozen, append-only project ledgers.

    A binding is GROUNDED only when the named ledger holds a receipt with the
    declared sha256 whose run_status is SUCCEEDED. A succeeded run is not a
    statement proof; the result only upgrades the receipt, never the claim.
    """
    nodes, edges, _, _ = _validate(spec)
    read_receipt = read_receipt or read_project_receipt
    bindings = {}
    for kind, records in (("node", nodes.values()), ("rule", edges)):
        for record in records:
            evidence = record.get("evidence")
            if evidence is None:
                continue
            binding = evidence["receipt"]
            key = (binding["project_root"], binding["sha256"])
            bindings.setdefault(key, []).append(f"{kind}:{record['id']}")
    audited = []
    for (root_text, digest_sha), users in sorted(bindings.items()):
        row = {"receipt": {"project_root": root_text, "sha256": digest_sha}, "used_by": sorted(users)}
        found = read_receipt(root_text, digest_sha)
        if found["status"] != "RECEIPT_FOUND":
            row.update(found)
        elif (body := found["body"]).get("sha256") != digest_sha or body.get("run_status") != "SUCCEEDED":
            row.update(status="RECEIPT_NOT_SUCCEEDED", run_status=body.get("run_status"))
        else:
            row.update(status="GROUNDED", run_id=body.get("run_id"))
        audited.append(row)
    grounded = {(row["receipt"]["project_root"], row["receipt"]["sha256"])
                for row in audited if row["status"] == "GROUNDED"}
    return {"assurance": "RECEIPT_EXECUTION_NOT_STATEMENT_VERIFICATION", "audits": audited,
            "grounded_receipts": sorted(grounded),
            "all_receipts_grounded": bool(audited) and len(grounded) == len(audited)}


def _supported_closure(nodes, edges, grounded_receipts=frozenset()):
    """Declared closure with the original scan-order first derivation witnesses."""
    closure, receipt_block = set(), {}
    for ident, node in nodes.items():
        if node["status"] != "SUPPORTED":
            continue
        evidence = node.get("evidence")
        if evidence is None or (evidence["receipt"]["project_root"],
                                evidence["receipt"]["sha256"]) in grounded_receipts:
            closure.add(ident)
        else:
            receipt_block[ident] = "receipt not grounded"
    derivations, conflicts, pending = {}, set(), []
    # Forward-ordered graphs finish in one scan, then one bounded heap pass.
    for i, edge in enumerate(edges):
        head = edge["conclusion"]
        if edge["status"] != "SUPPORTED" or head in closure:
            continue
        evidence = edge.get("evidence")
        if evidence is not None and (evidence["receipt"]["project_root"],
                                     evidence["receipt"]["sha256"]) not in grounded_receipts:
            continue
        if not all(tail in closure for tail in edge["premises"]):
            pending.append(i)
        elif nodes[head]["status"] == "CONTRADICTED":
            conflicts.add(edge["id"])
        else:
            closure.add(head)
            derivations[head] = edge["id"]
    # Without new support, the first pass already reached the fixed point.
    if pending and derivations:
        missing, waiting, ready = {}, {}, []
        for i in pending:
            edge = edges[i]
            if edge["conclusion"] in closure:
                continue
            tails = [tail for tail in edge["premises"] if tail not in closure]
            missing[i] = len(tails)
            for tail in tails:
                waiting.setdefault(tail, []).append(i)
            if not tails:
                ready.append((1, i))
        heapq.heapify(ready)
        while ready:
            turn, i = heapq.heappop(ready)
            edge = edges[i]
            head = edge["conclusion"]
            if nodes[head]["status"] == "CONTRADICTED":
                conflicts.add(edge["id"])
            elif head not in closure:
                closure.add(head)
                derivations[head] = edge["id"]
                for j in waiting.pop(head, ()):
                    missing[j] -= 1
                    if missing[j] == 0:
                        # Earlier rules wait for the next virtual ordered scan.
                        heapq.heappush(ready, (turn + (j <= i), j))
    return closure, derivations, conflicts, receipt_block


def _goal_relevance(edges, goals):
    """Reverse reachability through supported and proposed rules only."""
    incoming = {}
    for edge in edges:
        if edge["status"] != "CONTRADICTED":
            incoming.setdefault(edge["conclusion"], []).append(edge)
    relevant_nodes, relevant_edges = set(goals), set()
    pending = list(goals)
    while pending:
        for edge in incoming.get(pending.pop(), ()):
            relevant_edges.add(edge["id"])
            for tail in edge["premises"]:
                if tail not in relevant_nodes:
                    relevant_nodes.add(tail)
                    pending.append(tail)
    return relevant_nodes, relevant_edges


def analyze_hypergraph(spec, audit_receipts_enabled=False, read_receipt=None):
    """Least declared closure and complete minimal missing-evidence sets, or UNKNOWN."""
    nodes, edges, goals, limits = _validate(spec)
    grounded, receipt_audit = frozenset(), None
    if audit_receipts_enabled:
        receipt_audit = audit_receipts(spec, read_receipt)
        grounded = frozenset(receipt_audit["grounded_receipts"])
    closure, derivations, conflicts, receipt_block = _supported_closure(nodes, edges, grounded)
    blocked_nodes = set(receipt_block)
    blocked_rules = {edge["id"] for edge in edges if edge["status"] == "SUPPORTED"
                     and edge.get("evidence") is not None
                     and (edge["evidence"]["receipt"]["project_root"],
                          edge["evidence"]["receipt"]["sha256"]) not in grounded}
    relevant_nodes, relevant_edges = _goal_relevance(edges, goals)

    incoming = {edge["conclusion"] for edge in edges}
    direct = {ident for ident, node in nodes.items()
              if (node["status"] == "UNKNOWN" or ident in blocked_nodes)
              and node.get("allow_direct_evidence", ident not in incoming)}
    families = {ident: {frozenset()} if ident in closure else
                {frozenset({"node:" + ident})} if ident in direct else set()
                for ident, node in nodes.items()}
    combinations, truncated, reason = 0, False, None

    def insert(family, candidate):
        if any(old <= candidate for old in family):
            return False
        updated = {old for old in family if not candidate < old}
        updated.add(candidate)
        if len(updated) > limits["max_blocker_sets"]:
            raise _Truncated("max_blocker_sets exceeded")
        family.clear()
        family.update(updated)
        return True

    def spend():
        nonlocal combinations
        if combinations >= limits["max_combinations"]:
            raise _Truncated("max_combinations exceeded")
        combinations += 1

    # ponytail: antichain fixed point is exponential in the worst case;
    # explicit set/work caps return UNKNOWN rather than incomplete minimums.
    try:
        changed = True
        while changed:
            changed = False
            for edge in edges:
                head = edge["conclusion"]
                if edge["id"] not in relevant_edges or edge["status"] == "CONTRADICTED" \
                        or head in closure or nodes[head]["status"] == "CONTRADICTED":
                    continue
                status = "PROPOSED" if edge["id"] in blocked_rules else edge["status"]
                plans = {frozenset({"rule:" + edge["id"]})} if status == "PROPOSED" \
                    else {frozenset()}
                for tail in edge["premises"]:
                    joined = set()
                    for left in sorted(plans, key=lambda v: (len(v), sorted(v))):
                        for right in sorted(families[tail], key=lambda v: (len(v), sorted(v))):
                            spend()
                            insert(joined, left | right)
                    plans = joined
                    if not plans:
                        break
                for plan in sorted(plans, key=lambda v: (len(v), sorted(v))):
                    spend()
                    changed |= insert(families[head], plan)
    except _Truncated as exc:
        truncated, reason = True, str(exc)

    ready = []
    for edge in edges:
        if edge["id"] in relevant_edges and edge["status"] == "PROPOSED" \
                and edge["conclusion"] not in closure \
                and nodes[edge["conclusion"]]["status"] != "CONTRADICTED" \
                and set(edge["premises"]) <= closure:
            ready.append({"token": "rule:" + edge["id"], "kind": "PROPOSED_RULE_PROOF",
                          "premises_declared_supported": list(edge["premises"]),
                          "conclusion": edge["conclusion"], "source": deepcopy(edge["source"])})
    for edge in edges:
        if edge["id"] in blocked_rules and edge["conclusion"] not in closure \
                and nodes[edge["conclusion"]]["status"] != "CONTRADICTED" \
                and set(edge["premises"]) <= closure:
            ready.append({"token": "rule:" + edge["id"], "kind": "RECEIPT_REVALIDATION",
                          "premises_declared_supported": list(edge["premises"]),
                          "conclusion": edge["conclusion"], "source": deepcopy(edge["source"])})
    for ident in sorted(relevant_nodes - closure):
        if ident in direct:
            ready.append({"token": "node:" + ident,
                          "kind": "DIRECT_PROOF_ALTERNATIVE" if ident in incoming else "LEAF_NODE_EVIDENCE",
                          "source": deepcopy(nodes[ident]["source"])})

    results = {}
    for goal in goals:
        supported = goal in closure
        status = "DECLARED_SUPPORTED" if supported else nodes[goal]["status"]
        if status == "UNKNOWN" and not truncated and not families[goal]:
            status = "UNRESOLVED"
        sets = [[]] if supported else [] if truncated else \
            [sorted(v) for v in sorted(families[goal], key=lambda v: (len(v), sorted(v)))]
        results[goal] = {"status": status, "minimal_missing_evidence_sets": sets,
                         "blocker_sets_complete": supported or not truncated}
    return {"schema": 1, "assurance": ASSURANCE,
            "declared_supported_closure": sorted(closure),
            "declared_derivation_rules": derivations,
            "active_contradicted_conclusion_rules": sorted(conflicts),
            "receipt_blocked_node_ids": sorted(receipt_block),
            "receipt_audit": receipt_audit,
            "goals": results, "ready_obligations": ready,
            "truncated": truncated, "truncation_reason": reason,
            "combinations_examined": combinations, "limits": limits,
            "blocker_semantics": "UNKNOWN leaves default to direct evidence; derived nodes need explicit allow_direct_evidence=true. Proposed rule IDs remain separate proof obligations. No cost or probability is inferred.",
            "direct_evidence_node_ids": sorted(direct),
            "reported_nodes": deepcopy(spec["nodes"]),
            "reported_hyperedges": deepcopy(spec["hyperedges"])}


def trace_support_cone(spec, node_id, audit_receipts_enabled=False):
    """Explain the selected declared justification, without requiring a support table."""
    spec = deepcopy(spec)
    result = analyze_hypergraph(spec, audit_receipts_enabled=audit_receipts_enabled)
    return _selected_support_cone(spec, node_id, set(result['declared_supported_closure']),
                                  result['declared_derivation_rules'])


def _selected_support_cone(spec, node_id, closure, derivations):
    nodes, edges, _, _ = _validate(spec)
    _require(node_id in nodes, "unknown node to trace: " + str(node_id))
    index = {edge["id"]: edge for edge in edges}
    cone_nodes, cone_rules, pending = {node_id}, set(), [node_id]
    visited = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        rule = derivations.get(current)
        if rule is not None:
            cone_rules.add(rule)
            cone_nodes.update(index[rule]["premises"])
            pending.extend(index[rule]["premises"])
    return {"node_id": node_id, "supported": node_id in closure,
            "derivation_rule": derivations.get(node_id),
            "support_cone_nodes": sorted(cone_nodes), "support_cone_rules": sorted(cone_rules),
            "meaning": "Selected declared justification; independent alternatives may also exist"}


def review_hypergraph(value, *, locator="input", retract_nodes=(), retract_rules=(),
                      refute_nodes=(), refute_rules=(), change_source=None, trace=None, updates=(),
                      update_locators=(), audit_receipts_enabled=False, read_receipt=None):
    """Own input compilation, status changes and one current dependency analysis.

    The returned dependency_map is the next input: callers submit changes and
    reuse an immutable snapshot instead of maintaining derived support tables.
    """
    from rds_hypergraph_input import prepare_input
    spec, input_review = prepare_input(value, locator)

    def incomplete():
        return {"status": "UNKNOWN", "assurance": ASSURANCE, "input_review": input_review,
                "dependency_map": spec, "authorization": "UNCHANGED",
                "next_step": {"action": "clarify_input", "fields": input_review["errors"][:3],
                              "omitted_fields": max(0, len(input_review["errors"]) - 3)}}

    if input_review["errors"]:
        return incomplete()
    try:
        nodes, edges, _, _ = _validate(spec)
    except ValueError as exc:
        input_review["errors"].append({"path": "$", "reason": str(exc)})
        return incomplete()
    grounded = frozenset(audit_receipts(spec, read_receipt)['grounded_receipts']) if audit_receipts_enabled else frozenset()
    previous_closure, previous_derivations, _, _ = _supported_closure(nodes, edges, grounded)
    original_spec = spec
    candidate = deepcopy(spec)
    applied = []
    if not isinstance(updates, (list, tuple)) or len(updates) > 8:
        input_review['errors'].append({'path': 'updates', 'reason': 'supply at most eight declaration fragments'})
        return incomplete()
    for i, update in enumerate(updates):
        fragment, fragment_review = prepare_input(update, update_locators[i] if i < len(update_locators)
                                                  else str(locator) + '#update/' + str(i))
        for kind in ('repairs', 'warnings', 'errors'):
            input_review[kind].extend({'path': 'update[' + str(i) + '].' + row['path'], 'reason': row['reason']}
                                      for row in fragment_review[kind])
        if fragment is None or fragment_review['errors']:
            continue
        if 'limits' in fragment and fragment['limits'] != candidate.get('limits', {}):
            input_review['errors'].append({'path': 'updates.limits', 'reason': 'incremental declarations cannot change computation limits'})
            continue
        if fragment.get('schema', 1) != candidate.get('schema', 1):
            input_review['errors'].append({'path': 'updates.schema', 'reason': 'incremental declarations must use the same schema'})
            continue
        for key in ('question_id', 'goal_revision', 'scope'):
            if key in fragment:
                if key in candidate and candidate[key] != fragment[key]:
                    input_review['errors'].append({'path': 'updates.' + key,
                                                  'reason': 'declaration conflicts with the saved scope; explicitly import the new map'})
                else:
                    candidate[key] = deepcopy(fragment[key])
        generated = set(fragment_review['generated_node_ids'])
        for kind, key in (('node', 'nodes'), ('rule', 'hyperedges')):
            positions = {record['id']: j for j, record in enumerate(candidate[key])}
            for record in fragment[key]:
                name = record['id']
                previous = candidate[key][positions[name]] if name in positions else None
                if kind == 'node' and name in generated and previous is not None:
                    continue  # A rule's reference does not overwrite an existing observation.
                merged = {**deepcopy(previous or {}), **deepcopy(record)}
                if previous == merged:
                    continue
                if previous is None:
                    positions[name] = len(candidate[key])
                    candidate[key].append(merged)
                else:
                    candidate[key][positions[name]] = merged
                applied.append({'kind': kind, 'id': name, 'operation': 'declare',
                                'previous_status': previous['status'] if previous else None,
                                'previous_source': deepcopy(previous['source']) if previous else None,
                                'status': merged['status'], 'source': deepcopy(merged['source'])})
        candidate['goals'] = list(dict.fromkeys(candidate['goals'] + fragment['goals']))
    if input_review['errors']:
        return incomplete()
    try:
        nodes, edges, _, _ = _validate(candidate)
    except ValueError as exc:
        input_review['errors'].append({'path': 'updates', 'reason': str(exc)})
        return incomplete()
    spec = candidate
    edge_index = {edge["id"]: edge for edge in edges}
    changes = {}
    for kind, values, status in (("node", retract_nodes, "UNKNOWN"),
                                 ("rule", retract_rules, "PROPOSED"),
                                 ("node", refute_nodes, "CONTRADICTED"),
                                 ("rule", refute_rules, "CONTRADICTED")):
        values = [values] if isinstance(values, str) else values
        for ident in values:
            ident = ident.strip() if isinstance(ident, str) else ident
            index = nodes if kind == "node" else edge_index
            if not isinstance(ident, str) or ident not in index:
                input_review["errors"].append({"path": kind, "reason": "unknown change target: " + str(ident)})
            elif (kind, ident) in changes and changes[kind, ident] != status:
                input_review["errors"].append({"path": kind + ":" + ident,
                                              "reason": "conflicting retraction and refutation"})
            else:
                changes[kind, ident] = status
    if trace is not None and trace not in nodes:
        input_review["errors"].append({"path": "trace", "reason": "unknown node: " + str(trace)})
    if change_source is not None:
        try:
            _source(change_source)
        except ValueError as exc:
            input_review["errors"].append({"path": "change_source", "reason": str(exc)})
    if input_review["errors"]:
        spec = original_spec
        return incomplete()
    revised = deepcopy(spec)
    for kind, records in (("node", revised["nodes"]), ("rule", revised["hyperedges"])):
        for record in records:
            key = (kind, record["id"])
            if key in changes:
                origin = deepcopy(change_source) if change_source is not None \
                    else str(locator) + "#change/" + kind + "/" + record["id"]
                applied.append({"kind": kind, "id": record["id"], "previous_status": record["status"],
                                "previous_source": deepcopy(record["source"]),
                                "status": changes[key], "source": origin})
                # Withdraw support without destroying the original evidence binding.
                # The change's source is recorded in the persisted revision.
                record.update(status=changes[key])
    result = analyze_hypergraph(revised, audit_receipts_enabled=audit_receipts_enabled,
                               read_receipt=read_receipt)
    result.update(status="INCOMPLETE" if result["truncated"] else "ANALYZED",
                  dependency_map=revised, input_review=input_review, authorization="UNCHANGED")
    if applied:
        closure = set(result["declared_supported_closure"])
        result["revision"] = {"changes": applied, "lost_support": sorted(previous_closure - closure),
                              "gained_support": sorted(closure - previous_closure),
                              "alternative_derivations": {
                                  name: {"previous_rule": previous_derivations[name], "active_rule": rule}
                                  for name, rule in result["declared_derivation_rules"].items()
                                  if name in previous_derivations and previous_derivations[name] != rule},
                              "meaning": "Declared dependency impact; changes do not verify scientific refutation"}
    if trace is not None:
        result["support_cone"] = _selected_support_cone(revised, trace, set(result['declared_supported_closure']),
                                                        result['declared_derivation_rules'])
    if result["truncated"]:
        step = {"action": "inspect_computation_limit", "reason": result["truncation_reason"]}
    elif result["active_contradicted_conclusion_rules"]:
        step = {"action": "review_conflicting_support", "rules": result["active_contradicted_conclusion_rules"][:3]}
    else:
        goal = next((name for name, row in result["goals"].items()
                     if row["status"] != "DECLARED_SUPPORTED"), None)
        if goal is None:
            step = {"action": "check_goal_evidence" if result["goals"] else "declare_goal"}
        else:
            row = result["goals"][goal]
            step = {"action": "resolve_missing_evidence", "goal": goal, "status": row["status"],
                    "tokens": row["minimal_missing_evidence_sets"][0][:3]
                    if row["minimal_missing_evidence_sets"] else []}
    result["next_step"] = step
    return result


def cascade_refute(spec, contradicted_node_ids=(), contradicted_rule_ids=()):
    """Declare contradiction and recompute ordinary fields from the revised map."""
    return review_hypergraph(spec, refute_nodes=contradicted_node_ids,
                            refute_rules=contradicted_rule_ids)


def _operator_verdict_target(spec, target_token):
    """Resolve one ``node:<id>``/``rule:<id>`` token to the map's record and kind."""
    _require(isinstance(target_token, str) and target_token.strip(),
             "target token must be a nonempty string")
    kind, separator, ident = target_token.strip().partition(":")
    _require(separator and kind in ("node", "rule") and ident.strip(),
             "target token must be 'node:<id>' or 'rule:<id>': use a token from ready_obligations")
    ident = ident.strip()
    key = "nodes" if kind == "node" else "hyperedges"
    records = spec.get(key)
    _require(isinstance(records, list), "dependency map lacks a " + key + " table")
    for record in records:
        if isinstance(record, dict) and record.get("id") == ident:
            return kind, ident, record
    _require(False, "unknown " + kind + " target: " + ident)


def _bounded_verdict_json(value):
    """Refuse non-finite/oversize verdict data before copying or changing a map."""
    size = 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
            size += len(chunk.encode("utf-8"))
            if size > 8 * 1024 * 1024:
                raise ValueError("operator verdict and dependency declaration exceed 8 MiB")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("operator verdict requires bounded, finite JSON data: " + str(exc)) from exc


def apply_operator_verdict(spec, target_token, operator_receipt, *, locator="operator-verdict"):
    """Apply a scoped operator verdict through the existing TMS and receipt audit.

    PASS can declare support only with a grounded succeeded execution receipt.
    A bounded_finite_model FAIL/COUNTEREXAMPLE_FOUND is adapted to CONTRADICTED;
    other FAIL results remain unproven. Refutations require an operator, exact
    input digest and finite bounded witness. The revision archives the change
    alongside the full previous source; the original source remains intact.
    Execution evidence and declared impact never verify scientific truth.
    """
    from rds_hypergraph_input import prepare_input
    spec, input_review = prepare_input(spec, locator)
    _require(not input_review["errors"],
             "invalid dependency map: " + "; ".join(row["reason"] for row in input_review["errors"][:3]))
    _require(isinstance(operator_receipt, dict), "operator receipt must be an object")
    _bounded_verdict_json({"dependency_map": spec, "operator_receipt": operator_receipt})
    received_status = operator_receipt.get("status")
    status = received_status
    if status == "FAIL":
        status = "CONTRADICTED" if (operator_receipt.get("operator") == "bounded_finite_model"
                                    and operator_receipt.get("assurance") == "COUNTEREXAMPLE_FOUND") else "ERROR"
    _require(status in ("PASS", "CONTRADICTED", "UNKNOWN", "ERROR"),
             "operator verdict status must be PASS, CONTRADICTED, UNKNOWN or ERROR; "
             "only bounded_finite_model FAIL/COUNTEREXAMPLE_FOUND is a refutation")
    kind, ident, record = _operator_verdict_target(spec, target_token)
    token = kind + ":" + ident
    key = "nodes" if kind == "node" else "hyperedges"
    read_receipt = receipt_reader()
    metadata = {"target_token": token, "status": status, "received_status": received_status,
                "effect": "UNCHANGED"}
    if status == "PASS" and operator_receipt.get("evidence") is not None:
        binding = _evidence(operator_receipt["evidence"], kind, ident)
        candidate = deepcopy(spec)
        next(row for row in candidate[key] if row["id"] == ident)["evidence"] = binding
        audit = audit_receipts(candidate, read_receipt)
        target_audit = next(row for row in audit["audits"] if token in row["used_by"])
        metadata["receipt_audit"] = target_audit
        if target_audit["status"] == "GROUNDED":
            updated = {**deepcopy(record), "status": "SUPPORTED", "evidence": binding}
            result = review_hypergraph(spec, locator=locator, updates=[{key: [updated], "goals": []}],
                                      audit_receipts_enabled=True, read_receipt=read_receipt)
            _require(not result["input_review"]["errors"], "invalid support update: " +
                     str(result["input_review"]["errors"][:3]))
            metadata.update(effect="SUPPORTED", meaning="Grounded execution receipt permits declared support; not statement verification")
            result["operator_verdict"] = metadata
            return result
    if status != "CONTRADICTED":
        result = review_hypergraph(spec, locator=locator, audit_receipts_enabled=True,
                                  read_receipt=read_receipt)
        metadata["meaning"] = ("PASS requires a grounded execution receipt; declared status unchanged" if status == "PASS"
                               else "Unproven verdict retains the current status and obligations")
        result["operator_verdict"] = metadata
        return result
    witness = operator_receipt.get("witness")
    _require(isinstance(witness, (dict, list, str, int, float)) and witness is not None,
             "a CONTRADICTED verdict requires a witness value")
    operator_name = operator_receipt.get("operator")
    _require(isinstance(operator_name, str) and operator_name.strip(), "operator receipt must name the operator")
    input_digest = operator_receipt.get("input_sha256")
    _require(isinstance(input_digest, str) and len(input_digest) == 64
             and all(c in "0123456789abcdefABCDEF" for c in input_digest),
             "input_sha256 must have 64 hexadecimal characters")
    input_digest = input_digest.lower()
    archive = {"locator": str(locator) + "#witness/" + kind + "/" + ident,
               "operator": operator_name.strip(), "verdict_status": "CONTRADICTED",
               "received_status": received_status, "input_sha256": input_digest, "witness": witness}
    _bounded_verdict_json({"dependency_map": spec, "change_source": archive})
    result = review_hypergraph(spec, locator=locator, change_source=archive,
                              audit_receipts_enabled=True, read_receipt=read_receipt,
                              **({"refute_nodes": [ident]} if kind == "node" else {"refute_rules": [ident]}))
    _require(not result["input_review"]["errors"], "invalid refutation: " + str(result["input_review"]["errors"][:3]))
    metadata.update(effect="REFUTED", operator=operator_name.strip(), input_sha256=input_digest,
                    witness=deepcopy(witness), lost_support=result.get("revision", {}).get("lost_support", []),
                    minimal_missing_evidence_sets={goal: row["minimal_missing_evidence_sets"]
                                                   for goal, row in result["goals"].items()},
                    meaning="Witness refutes the declared record; remaining routes and blockers are declared impact, not scientific verification")
    result["operator_verdict"] = metadata
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--audit-files", action="store_true")
    parser.add_argument("--audit-receipts", action="store_true",
                        help="verify receipt-bound evidence against project ledgers")
    parser.add_argument("--update", "-u", action="append", default=[])
    for flag in ("retract-node", "retract-rule", "refute-node", "refute-rule"):
        parser.add_argument("--" + flag, action="append", default=[])
    parser.add_argument("--change-source")
    parser.add_argument("--trace-cone")
    args = parser.parse_args()
    path = Path(args.input)
    _require(path.stat().st_size <= 8 * 1024 * 1024, "input file exceeds 8 MiB")
    from rds_hypergraph_input import load_input
    spec, repairs = load_input(path.read_text(encoding="utf-8-sig"))
    _require(len(args.update) <= 8, "at most eight update files")
    updates = []
    for update_path in args.update:
        update_path = Path(update_path)
        _require(update_path.stat().st_size <= 8 * 1024 * 1024, "update file exceeds 8 MiB")
        update, fixed = load_input(update_path.read_text(encoding="utf-8-sig"))
        updates.append(update)
        repairs.extend(fixed)
    result = review_hypergraph(spec, locator=str(path), retract_nodes=args.retract_node,
                              retract_rules=args.retract_rule, refute_nodes=args.refute_node,
                              refute_rules=args.refute_rule, change_source=args.change_source,
                              trace=args.trace_cone, updates=updates,
                              audit_receipts_enabled=args.audit_receipts)
    result["input_review"]["format_repairs"] = repairs
    if args.audit_files and result["status"] != "UNKNOWN":
        result["source_file_audit"] = audit_sources(result["dependency_map"], path.parent)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with Path(args.output).open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    else:
        print(text)
    return 2 if result.get("truncated") or result["input_review"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
