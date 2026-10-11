#!/usr/bin/env python3
"""Export a standalone, offline, read-only force-directed hypergraph explorer."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

from rds_hypergraph import ASSURANCE, _validate, analyze_hypergraph
from rds_hypergraph_input import load_input, prepare_input
from rds_hypergraph_readable import graph_digest
from rds_bounded_io import read_regular_bytes

MAX_INPUT_BYTES = 8 * 1024 * 1024
VIEW_LIMITS = {"nodes": 4096, "hyperedges": 8192, "incidences": 32768}
ANALYSIS_LIMITS = {"nodes": 200, "hyperedges": 400, "incidences": 2000}


def _asset_text(relative):
    return (Path(__file__).resolve().parent / "assets" / relative).read_bytes().decode("utf-8")


def graph_view(value, source, *, snapshot_sha256=None, demo=False):
    """Render large maps without requiring combinatorial blocker analysis."""
    spec, review = prepare_input(value, str(source))
    if review["errors"]:
        raise ValueError("Hypergraph input: " + json.dumps(review["errors"], ensure_ascii=False))
    validation = deepcopy(spec)
    if "limits" in validation and not isinstance(validation["limits"], dict):
        raise ValueError("Hypergraph limits must be an object")
    validation_limits = validation.setdefault("limits", {})
    validation_limits.setdefault("max_nodes", VIEW_LIMITS["nodes"])
    validation_limits.setdefault("max_hyperedges", 16384)
    _validate(validation)
    counts = {"nodes": len(spec["nodes"]), "hyperedges": len(spec["hyperedges"]),
              "incidences": sum(len(e["premises"]) + 1 for e in spec["hyperedges"])}
    result = {"status": "AVAILABLE", "source": str(source), "snapshot_sha256": snapshot_sha256,
              "demo": demo, "assurance": ASSURANCE, "counts": counts, "limits": VIEW_LIMITS,
              "input_review": review, "receipt_audit": "NOT_RUN", "analysis": None}
    if any(counts[key] > cap for key, cap in VIEW_LIMITS.items()):
        return {**result, "status": "DISPLAY_LIMIT", "graph": None, "analysis_status": "NOT_RUN"}
    result["graph"] = spec
    result["graph_sha256"] = graph_digest(spec)
    if any(counts[key] > cap for key, cap in ANALYSIS_LIMITS.items()):
        return {**result, "analysis_status": "NOT_RUN_LARGE_GRAPH"}
    bounded = deepcopy(spec)
    limits = bounded.setdefault("limits", {})
    limits["max_combinations"] = min(limits.get("max_combinations", 50000), 50000)
    limits["max_blocker_sets"] = min(limits.get("max_blocker_sets", 128), 128)
    result["analysis"] = analyze_hypergraph(bounded)
    result["analysis_status"] = "INCOMPLETE" if result["analysis"]["truncated"] else "COMPLETE"
    return result


def large_demo():
    """1,200 synthetic claims; hub/cluster structure, never scientific evidence."""
    nodes = [{"id": "research-map", "label": "研究地图", "status": "UNKNOWN", "source": "synthetic:map"},
             {"id": "research-question", "label": "研究问题", "status": "UNKNOWN", "source": "synthetic:question"}]
    edges = []
    topics = ["表征学习", "因果推断", "优化方法", "实验设计", "统计检验", "机制解释", "数据质量", "泛化边界",
              "鲁棒性", "计算预算", "结构先验", "理论约束", "基线比较", "误差分析", "评估协议", "负面结果",
              "可复现性", "不确定性", "研究假设", "证据追踪", "替代解释", "模型结构", "任务收益", "后续问题"]
    for i, topic in enumerate(topics):
        ident = f"topic-{i:02d}"
        nodes.append({"id": ident, "label": topic, "status": "UNKNOWN", "source": "synthetic:topic"})
        edges.append({"id": f"route-{i:02d}", "premises": ["research-map", "research-question"],
                      "conclusion": ident, "status": "PROPOSED", "relation": "研究依赖", "source": "synthetic:route"})
    for i in range(1174):
        ident, topic = f"N{i+1:04d}", f"topic-{i % 24:02d}"
        status = ("UNKNOWN", "UNKNOWN", "SUPPORTED", "UNKNOWN", "CONTRADICTED")[i % 5]
        nodes.append({"id": ident, "label": ident, "status": status,
                      "source": f"synthetic:claim/{i+1}", "description": f"{topics[i % 24]}的合成节点，仅用于大图界面与性能验证。"})
        edges.append({"id": f"E{i+1:04d}", "premises": [topic], "conclusion": ident,
                      "status": "PROPOSED" if i % 3 else "SUPPORTED", "relation": "主题关联", "source": "synthetic:relation"})
        if i > 30 and i % 5 == 0:
            edges.append({"id": f"X{i:04d}", "premises": [f"N{i-23:04d}", f"N{i-7:04d}"],
                          "conclusion": ident, "status": "PROPOSED", "relation": "竞争解释" if i % 20 == 0 else "共同前提", "source": "synthetic:cross-link"})
    return {"schema": 1, "scope": "SYNTHETIC PERFORMANCE / VISUAL FIXTURE; NO SCIENTIFIC RESULTS",
            "nodes": nodes, "hyperedges": edges, "goals": ["research-map"],
            "limits": {"max_nodes": 4096, "max_hyperedges": 8192}}


def read_graph(root, graph_path=None, *, demo=False, large=False):
    """Read only the saved snapshot and bound CAS, without an external evidence audit."""
    try:
        if demo and large:
            return graph_view(large_demo(), "synthetic:1200-node-preview", demo=True)
        if demo:
            graph_path = Path(__file__).resolve().parents[1] / "examples" / "hypergraph-view.json"
        if graph_path is not None:
            path = Path(graph_path).resolve()
            raw = read_regular_bytes(path, MAX_INPUT_BYTES, label='Hypergraph input',
                                     limit_message='Hypergraph input exceeds 8 MiB')
            value, repairs = load_input(raw.decode("utf-8-sig"))
            result = graph_view(value, path, demo=demo)
            result["input_review"]["repairs"][:0] = [{"path": "$", "reason": repair} for repair in repairs]
            return result
        from rds_tms_store import current
        saved = current(root)
        if saved is None:
            return {"status": "MISSING", "graph": None, "source": None, "demo": False}
        result = graph_view(saved["dependency_map"], Path(root).resolve() / ".rds" / "project.sqlite3",
                            snapshot_sha256=saved["sha256"])
        if saved.get("source_base_dir") is not None:
            result["record_source_base_dir"] = saved["source_base_dir"]
        return result
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
        return {"status": "UNAVAILABLE", "graph": None, "source": str(graph_path or root), "reason": str(exc), "demo": demo}


def analysis_notice(result):
    return {"NOT_RUN_LARGE_GRAPH": "阻断条件分析未运行：图规模超过分析上限。图仍完整显示。",
            "INCOMPLETE": "阻断条件分析不完整：已达到计算上限。",
            "COMPLETE": "阻断条件分析已完成。",
            "NOT_RUN": "阻断条件分析未运行。"}.get(result.get("analysis_status"), "阻断条件分析状态未知。")


def render_html(result):
    payload = json.dumps({**result, "display": display_records(result), "analysis_notice": analysis_notice(result)}, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c")
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    # Substitute code before data so user fields cannot become template tokens.
    return (HTML.replace("__GRAPH_CSS__", CSS).replace("__GRAPH_JS__", JS)
            .replace("__D3_BUNDLE__", D3_BUNDLE).replace("__GRAPH_DATA__", payload))


# Official UMD distributions, pinned and embedded for a single-file offline export.
# Refresh from npm tarballs only; retain each upstream ISC license and version.
D3_BUNDLE = _asset_text("hypergraph/d3-bundle.js")


HTML = _asset_text("hypergraph/index.html")

CSS = _asset_text("hypergraph/style.css")

JS = _asset_text("hypergraph/app.js")


# The user-selected replica has no published license at this pinned revision.
# Read its local checkout at export time; do not redistribute it in this repository.
REPLICA_COMMIT = "b811f4d12f909d496d44c7f2d98b3a408eb1a627"
REPLICA_FILES = {
    "renderer.js": "e3b0aed506ab9ed6717ef1ef2fb177eb08c52be574bdbba5cff612dd62c75ca7",
    "sim-worker.js": "cd618311568bd64bef2b52ea0c302d17c25eeb4bc6c8041f5d312c284bc07316",
    "style.css": "3f3c8ce502225c01b664d3842643301bd926a8e81252152fecd7c438f29eb59d",
}
PIXI_SHA256 = "712a3e1943b062a907d86c5bb56b67c7b32a3f03b3654768b00b7016d30a24c6"


def dependency_levels(ids, links):
    """Longest-path ranks on the SCC condensation; cycles share a rank.

    Iterative traversal keeps the full display limit independent of Python's
    recursion limit. Disconnected records with no directed relation are unranked.
    """
    adj, rev = {i: [] for i in ids}, {i: [] for i in ids}
    for s, t, _ in links:
        adj[s].append(t)
        rev[t].append(s)
    seen, order = set(), []
    for first in ids:
        if first in seen:
            continue
        seen.add(first)
        stack = [(first, iter(adj[first]))]
        while stack:
            n, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                order.append(n)
                stack.pop()
            elif nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, iter(adj[nxt])))
    component, cid = {}, -1
    for first in reversed(order):
        if first in component:
            continue
        cid += 1
        component[first] = cid
        stack = [first]
        while stack:
            for nxt in rev[stack.pop()]:
                if nxt not in component:
                    component[nxt] = cid
                    stack.append(nxt)
    out, indegree = {c: set() for c in component.values()}, {c: 0 for c in component.values()}
    for s, t, _ in links:
        a, b = component[s], component[t]
        if a != b and b not in out[a]:
            out[a].add(b)
            indegree[b] += 1
    rank = {c: 0 for c in out}
    queue = [c for c in out if not indegree[c]]
    for c in queue:
        for nxt in out[c]:
            rank[nxt] = max(rank[nxt], rank[c] + 1)
            indegree[nxt] -= 1
            if not indegree[nxt]:
                queue.append(nxt)
    center = max(rank.values(), default=0) / 2
    return {i: (rank[component[i]] - center) * 160 if adj[i] or rev[i] else None for i in ids}


def _adapt_record_view(spec):
    """Resolve reported identities without running inference or auditing research.

    Older owned snapshots predate record_kind. Adapt only reserved run/receipt
    identities and structured byte references; never recover IDs from prose.
    The adapter operates on a copy so the exported snapshot stays unchanged.
    """
    adapted = deepcopy(spec)
    # Record matching is linear in the displayed records, independent of the
    # smaller default limits intended for combinatorial dependency analysis.
    limits = adapted.setdefault("limits", {})
    limits.setdefault("max_nodes", VIEW_LIMITS["nodes"])
    limits.setdefault("max_hyperedges", VIEW_LIMITS["hyperedges"])
    legacy = set()
    for row in adapted["nodes"]:
        if "record_kind" in row:
            continue
        ident, src = row["id"], row.get("source")
        if ident.startswith("owned:run:") and isinstance(row.get("manifest_sha256"), str):
            row["record_kind"] = "run"
            row.setdefault("run_id", ident.removeprefix("owned:run:"))
        elif ident.startswith("owned:receipt:") and isinstance(row.get("receipt_sha256"), str):
            row["record_kind"] = "receipt"
            row.setdefault("run_id", ident.removeprefix("owned:receipt:"))
        elif row.get("interpretation") and isinstance(row.get("run_id"), str):
            if isinstance(row["interpretation"], str) and row["interpretation"] in {"MISSING", "PENDING"}:
                row["record_kind"] = "declared_output"
            elif isinstance(src, dict) and src.get("sha256") and (src.get("file") or src.get("path")):
                row["record_kind"] = "artifact"
        elif isinstance(src, dict) and src.get("receipt_id") and src.get("sha256") and (src.get("path") or src.get("file")):
            row["record_kind"] = "observation"
        if "record_kind" in row:
            legacy.add(ident)
    # Legacy observations carry an exact receipt identity rather than run_id;
    # a unique receipt supplies the run. Old artifact inventories lack receipt_id.
    receipts, by_run = {}, {}
    for row in adapted["nodes"]:
        if row.get("record_kind") == "receipt":
            src = row.get("source") if isinstance(row.get("source"), dict) else {}
            aliases = [v for v in (row.get("receipt_id"), row.get("receipt_sha256"), src.get("receipt_id")) if v is not None]
            if aliases and all(isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{64}", v) for v in aliases) and len({v.lower() for v in aliases}) == 1:
                key = aliases[0].lower()
                receipts.setdefault(key.lower(), []).append(row)
                if isinstance(row.get("run_id"), str):
                    by_run.setdefault(row["run_id"], []).append(key)
    for row in adapted["nodes"]:
        if row["id"] not in legacy:
            continue
        src = row.get("source") if isinstance(row.get("source"), dict) else {}
        key = src.get("receipt_id")
        matches = receipts.get(key.lower(), []) if isinstance(key, str) else []
        if row["record_kind"] == "observation" and "run_id" not in row and len(matches) == 1:
            row["run_id"] = matches[0].get("run_id")
        if row["record_kind"] == "artifact" and not any(row.get(k) for k in ("receipt_id", "receipt_sha256")) and not key:
            candidates = by_run.get(row.get("run_id"), [])
            if len(candidates) == 1:
                row["receipt_id"] = candidates[0]
    return adapted


def _record_view_report(spec, *, adapted=None, source_base=None):
    adapted = _adapt_record_view(spec) if adapted is None else adapted
    if source_base is not None and "record_source_base_dir" not in adapted:
        adapted = deepcopy(adapted)
        adapted["record_source_base_dir"] = source_base
    import rds_hypergraph
    if callable(getattr(rds_hypergraph, "record_topology", None)):
        return rds_hypergraph.record_topology(adapted)
    return _legacy_record_report(adapted)


def _legacy_record_report(spec):
    """Conservative typed resolver for installations without record_topology."""
    rows, issues, indexes, relations = {}, [], {k: {} for k in ("run", "receipt", "artifact")}, []
    kinds = {"contract", "run", "receipt", "artifact", "declared_output", "lifecycle_fact", "observation"}
    required = {"run": ("run_id",), "receipt": ("run_id", "receipt_id"),
                "artifact": ("run_id", "path", "sha256"), "declared_output": ("run_id", "path"),
                "lifecycle_fact": ("run_id",), "observation": ("run_id",)}
    source_base = spec.get("record_source_base_dir")
    if source_base is not None:
        try:
            if not (isinstance(source_base, (str, Path)) and str(source_base) and "\0" not in str(source_base)
                    and Path(source_base).is_absolute()):
                raise ValueError("Invalid reported source base")
            source_base = Path(source_base).resolve()
        except (OSError, ValueError, RuntimeError):
            source_base = None  # Match original strings, without inferring cwd.
            issues.append({"node_id": None, "scope": "dependency_map", "field": "record_source_base_dir",
                           "reason": "INVALID_BINDING", "candidates": [], "omitted_candidates": 0})

    def issue(row, field, reason, candidates=()):
        issues.append({"node_id": row["id"], "field": field, "reason": reason,
                       "candidates": sorted(candidates)[:3], "omitted_candidates": max(0, len(candidates) - 3)})

    for node in spec["nodes"]:
        if not isinstance(node.get("record_kind"), str) or node["record_kind"] not in kinds:
            if node.get("record_kind") is not None:
                issue(node, "record_kind", "INVALID_RECORD_KIND")
            continue
        src = node.get("source") if isinstance(node.get("source"), dict) else {}
        row = {"node": node, "kind": node["record_kind"], "invalid_fields": set()}
        for field, aliases in (("run_id", [node.get("run_id")]),
                ("receipt_id", [node.get("receipt_id"), node.get("receipt_sha256"), src.get("receipt_id")]),
                ("path", [node.get("artifact_path"), node.get("output_path"), src.get("file"), src.get("path")]),
                ("sha256", [src.get("sha256")])):
            values = [v for v in aliases if v is not None]
            digest = field in {"receipt_id", "sha256"}
            if any(not isinstance(v, str) or not v or len(v) > (64 if digest else 2048)
                   or field == "path" and "\0" in v
                   or digest and re.fullmatch(r"[0-9a-fA-F]{64}", v) is None for v in values):
                issue(node, field, "INVALID_BINDING")
                row["invalid_fields"].add(field)
                continue
            values = [v.lower() if digest else v for v in values]
            if field == "path" and source_base is not None:
                try:
                    values = [str((source_base / v).resolve()) for v in values]
                except (OSError, ValueError, RuntimeError):
                    issue(node, field, "INVALID_BINDING")
                    row["invalid_fields"].add(field)
                    continue
            if len(set(values)) > 1:
                issue(node, field, "CONFLICTING_BINDINGS")
                row["invalid_fields"].add(field)
            elif values:
                row[field] = values[0]
        for field in required.get(row["kind"], ()):
            if field not in row and field not in row["invalid_fields"]:
                issue(node, field, "RUN_NOT_REGISTERED" if field == "run_id" and node.get("route_id")
                      and row["kind"] in {"lifecycle_fact", "observation"} else "MISSING_BINDING")
        rows[node["id"]] = row
        kind = row["kind"]
        key = row.get("run_id") if kind == "run" else row.get("receipt_id") if kind == "receipt" else (
            row.get("run_id"), row.get("path"), row.get("sha256")) if kind == "artifact" else None
        if key is not None and not (isinstance(key, tuple) and None in key):
            indexes[kind].setdefault(key, []).append(node["id"])

    def match(row, kind, key, field):
        if key is None or isinstance(key, tuple) and None in key:
            return None
        candidates = indexes[kind].get(key, [])
        if len(candidates) != 1:
            issue(row["node"], field, "AMBIGUOUS_BINDING" if candidates else "UNMATCHED_BINDING", candidates)
            return None
        return candidates[0]

    def link(kind, origin, target):
        if origin is not None:
            relations.append({"kind": kind, "from": origin, "to": target["node"]["id"],
                              "binding": "DECLARED_OUTPUT" if kind == "declared_output" else "REPORTED_EXACT_ID_MATCH",
                              "scientific_support": "UNKNOWN"})

    for row in rows.values():
        kind = row["kind"]
        if kind == "run":
            match(row, "run", row.get("run_id"), "run_id")
            continue  # Check origins without consumers; never add a self link.
        if kind == "contract":
            continue
        run = match(row, "run", row.get("run_id"), "run_id")
        receipt = match(row, "receipt", row.get("receipt_id"), "receipt_id") if kind != "receipt" else None
        receipt_conflict = False
        if receipt is not None:
            receipt_run = rows[receipt].get("run_id")
            if receipt_run is None:
                receipt_conflict = True  # Origin ownership is missing or invalid.
            elif row.get("run_id") is not None and receipt_run != row["run_id"]:
                issue(row["node"], "receipt.run_id", "CONFLICTING_BINDINGS", [receipt])
                receipt_conflict = True
        if kind == "receipt":
            # Preserve origin ambiguity diagnostics independently of membership.
            match(row, "receipt", row.get("receipt_id"), "receipt_id")
            link("run_receipt", run, row)
        elif kind == "artifact":
            match(row, "artifact", (row.get("run_id"), row.get("path"), row.get("sha256")), "artifact_identity")
            link("run_artifact", run, row)
            if row.get("run_id") is not None and not receipt_conflict:
                link("receipt_artifact", receipt, row)
        elif kind == "declared_output":
            if row.get("path"):
                link("declared_output", run, row)
        elif kind == "lifecycle_fact":
            link("run_lifecycle_fact", run, row)
        elif kind == "observation":
            link("run_observation", run, row)
            if receipt_conflict or "receipt_id" in row["invalid_fields"]:
                continue  # The independently exact run link remains available.
            artifact = match(row, "artifact", (row.get("run_id"), row.get("path"), row.get("sha256")), "artifact_identity")
            if artifact is not None and "receipt_id" in rows[artifact]["invalid_fields"]:
                continue  # Invalid receipt metadata cannot match absent metadata.
            if artifact is not None and rows[artifact].get("receipt_id") != row.get("receipt_id"):
                issue(row["node"], "artifact.receipt_id", "CONFLICTING_BINDINGS", [artifact])
            else:
                link("artifact_observation", artifact, row)
    return {"record_relations": sorted(relations, key=lambda r: (r["kind"], r["from"], r["to"])),
            "record_topology": {"assurance": "REPORTED_RECORD_IDENTITIES_NOT_SCIENTIFIC_SUPPORT",
                                "issues": sorted(issues, key=lambda i: (i["node_id"] or "", i["field"], i["reason"])),
                                "resolver": "CONSERVATIVE_LEGACY_FALLBACK"}}


def _locale_label(row, default):
    label = row.get("label")
    if isinstance(label, dict):
        label = next((label[k] for k in ("zh", "en") if isinstance(label.get(k), str) and label[k]), None)
    return label if isinstance(label, str) and label else default


DISPLAY_STYLES = {"声明": ("#c2c8d2", "circle"), "执行": ("#b39ddb", "square"),
                  "回执": ("#82c4af", "ring"), "产物": ("#82b6d4", "hexagon"),
                  "观测": ("#d5c17e", "circle"), "超边": ("#9c95af", "diamond")}
LIFECYCLE_ID = re.compile(r"^(?:owned:fact:)?run\.(.+)\.(succeeded|failed|timed_out|status|completed|running|registered|not_registered)$")
HASH_NAME_TOKEN = re.compile(r"(?<![a-zA-Z0-9])[0-9a-fA-F]{32,}(?![a-zA-Z0-9])")


def readable_text(text, *, machine=False):
    # Decimal quantities in human labels and filenames are not hash identities.
    return HASH_NAME_TOKEN.sub(lambda m: "" if machine or re.search(r"[a-fA-F]", m[0]) else m[0], text)


def display_record(row, *, edge=False):
    """Private presentation only: a short name and the recorded status basis.

    An execution predicate being false does not establish its opposite. Neither
    a receipt outcome nor a declaration status is an independently audited proof.
    """
    ident = row["id"]
    typed = row.get("record_kind")
    fact = row.get("owned_fact")
    src = row.get("source")
    lifecycle = LIFECYCLE_ID.fullmatch(ident) if isinstance(fact, dict) or typed == "lifecycle_fact" or ident.startswith("owned:fact:") else None
    kind = {"run": "执行", "receipt": "回执", "artifact": "产物", "declared_output": "产物",
            "fact": "观测", "observation": "观测", "lifecycle_fact": "观测"}.get(typed if isinstance(typed, str) else "")
    missing_output = ident.startswith("owned:output:") and row.get("interpretation") == "MISSING"
    prefixes = (("owned:run:", "执行"), ("owned:receipt:", "回执"), ("owned:artifact:", "产物"),
                ("owned:output:", "产物"),
                ("owned:fact:", "观测"), ("observation.", "观测"), ("run.", "观测"),
                ("receipt.", "回执"))
    observation_identity = typed == "observation" or isinstance(fact, dict) or (isinstance(src, dict) and
                            isinstance(src.get("sha256"), str) and isinstance(src.get("receipt_id"), str))
    receipt_identity = typed == "receipt" or any(isinstance(row.get(k), str) and row[k]
                                                for k in ("receipt_id", "receipt_sha256", "outcome", "run_status"))
    prefix = next(((p, k) for p, k in prefixes if ident.startswith(p) and
                   (p != "run." or lifecycle) and (p != "observation." or observation_identity) and
                   (p != "receipt." or receipt_identity) and (p != "owned:output:" or missing_output)), None)
    kind = "超边" if edge else kind or (prefix[1] if prefix else "声明")
    machine_identity = bool(prefix or lifecycle or ident.startswith("owned:"))
    short = ident
    if lifecycle:
        short = lifecycle[1]
    elif prefix:
        short = ident[len(prefix[0]):]
    if prefix or lifecycle or kind in {"执行", "回执"}:
        short = re.sub(r"[._:/\\-]+", " ", short).strip() or ident
    path_name = False
    if kind == "产物":
        src = row.get("source")
        path = (src.get("file") or src.get("path")) if isinstance(src, dict) else None
        if missing_output and isinstance(src, dict) and isinstance(row.get("run_id"), str):
            locator = src.get("locator")
            marker = "missing declared output " + row["run_id"] + ":"
            if isinstance(locator, str) and locator.startswith(marker):
                path = locator[len(marker):]
        if isinstance(path, str) and path:
            short = path.replace("\\", "/").rsplit("/", 1)[-1] or ident
            path_name = True
    short = readable_text(short, machine=machine_identity and not path_name)
    if path_name and short.startswith("."):
        short = kind + short
    short = short.strip(" ._:/\\-")
    if not short or short.startswith("owned:"):
        run_name = row.get("run_id")
        short = re.sub(r"[._:/\\-]+", " ", run_name).strip() + " · " + kind if isinstance(run_name, str) and run_name and not re.fullmatch(r"[0-9a-fA-F]{32,}", run_name) else kind
    claim = row.get("claim")
    label = _locale_label(row, claim if isinstance(claim, str) and claim else short)
    if label == ident and machine_identity:
        label = short
    label = readable_text(label).strip() or kind
    outline = {"color": "#9299a6", "state": "neutral", "basis": "中性：UNKNOWN / 待验证；不表示科研证明"}
    execution = kind in {"执行", "回执"} or lifecycle is not None
    if execution:
        states, notes = [], []
        outcomes = {"SUCCEEDED": "success", "FAILED": "failure", "TIMED_OUT": "failure",
                    "TIMEOUT": "failure", "CANCELLED": "failure", "CANCELED": "failure"}
        for key in ("outcome", "run_status", "lifecycle_status"):
            if key in row:
                value = row[key]
                notes.append(key + "=" + json.dumps(value, ensure_ascii=False))
                states.append(outcomes.get(value) if isinstance(value, str) else None)
        if lifecycle:
            fact = row.get("owned_fact")
            fact = fact if isinstance(fact, dict) else {}
            value = fact.get("value")
            notes.append(lifecycle[2] + "=" + json.dumps(value, ensure_ascii=False))
            fact_id = fact.get("id")
            valid_id = fact_id is None or fact_id == ident.removeprefix("owned:fact:")
            states.append(("success" if lifecycle[2] == "succeeded" else "failure")
                          if value is True and fact.get("reliable") is True and valid_id and
                          lifecycle[2] in {"succeeded", "failed", "timed_out"} else None)
        state = states[0] if states and all(s == states[0] for s in states) else None
        outline["basis"] = "执行记录：" + ("；".join(notes) or "未记录明确结果")
        if row.get("status") != "SUPPORTED":
            state = None
            outline["basis"] += "；原始记录状态=" + str(row.get("status", "UNKNOWN")) + "（待验证或冲突）"
        if state:
            outline.update(state=state, color="#39b872" if state == "success" else "#e65b63")
        else:
            outline["basis"] += "；中性（否定 / 待定 / 无效或冲突，不能推断相反结果）"
        outline["basis"] += "；未审计，不表示科研证明"
    elif kind in {"声明", "超边"}:
        state = {"SUPPORTED": "success", "CONTRADICTED": "failure"}.get(row.get("status"))
        outline["basis"] = "声明状态：" + str(row.get("status", "UNKNOWN")) + "；未独立验收，不表示科研证明"
        if state:
            outline.update(state=state, color="#39b872" if state == "success" else "#e65b63")
    color, shape = DISPLAY_STYLES[kind]
    if missing_output:
        status_text = "输出缺失"
    elif kind in {"声明", "超边"}:
        status_text = {"SUPPORTED": "声明支持", "CONTRADICTED": "声明反驳", "PROPOSED": "候选", "UNKNOWN": "待确认"}.get(row.get("status"), "待确认")
    elif execution:
        status_text = {"success": "执行成功", "failure": "执行失败"}.get(outline["state"], "未判定")
    else:
        status_text = "已记录" if row.get("status") == "SUPPORTED" else "待确认"
    return {"label": label, "kind": kind, "color": color, "shape": shape, "outline": outline, "status_text": status_text}


def display_records(result, *, adapted=None, report=None):
    spec = result.get("graph") or {}
    adapted = (_adapt_record_view(spec) if spec else spec) if adapted is None else adapted
    report = (_record_view_report(spec, adapted=adapted, source_base=result.get("record_source_base_dir")) if spec else {"record_topology": {"issues": []}}) if report is None else report
    issues = report["record_topology"]["issues"]
    effective = {row["id"]: row for row in adapted.get("nodes", [])}
    by_node = {}
    for issue in issues:
        by_node.setdefault(issue.get("node_id"), []).append(issue)
    readable = result.get("readable")
    if readable is not None:
        from rds_hypergraph_readable import validate_readable
        readable = validate_readable(readable, result)
    presentation = {}
    for key in ("nodes", "hyperedges"):
        presentation[key] = {}
        for row in spec.get(key, []):
            info = display_record(effective.get(row["id"], row) if key == "nodes" else row, edge=key == "hyperedges")
            entry = (readable or {}).get(key, {}).get(row["id"], {})
            if entry.get("title", "").strip():
                info["label"] = entry["title"].strip()
            info["summary"] = entry.get("summary", "").strip()
            scoped = by_node.get(row["id"], []) if key == "nodes" else []
            info["diagnostics"] = readable_diagnostics(scoped)
            presentation[key][row["id"]] = info
    presentation["diagnostics"] = readable_diagnostics(issues, graph=True)
    return presentation


def readable_diagnostics(issues, *, graph=False):
    """Bounded human explanations; raw resolver evidence stays in the payload."""
    reasons = {"MISSING_BINDING": "未记录", "UNMATCHED_BINDING": "未找到匹配",
               "AMBIGUOUS_BINDING": "有多个匹配", "CONFLICTING_BINDINGS": "记录冲突",
               "INVALID_BINDING": "记录无效", "RUN_NOT_REGISTERED": "执行尚未登记"}
    messages = []
    for issue in issues:
        field = issue.get("field", "")
        subject = "执行来源" if "run" in field else "回执来源" if "receipt" in field else "产物来源" if "artifact" in field else "来源"
        message = subject + "：" + reasons.get(issue.get("reason"), "待核对") + "。"
        if not any(item["message"] == message for item in messages) or not graph:
            candidates = issue.get("candidates", [])
            messages.append({"message": message, "candidates": candidates[:8],
                             "omitted_candidates": issue.get("omitted_candidates", 0) + max(0, len(candidates) - 8)})
    return {"items": messages[:8], "omitted": max(0, len(messages) - 8), "count": len(issues)}


def write_html_atomic(output, page):
    """Replace the output directory entry without writing through input aliases."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".rds-hypergraph-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(page)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def replica_view(result, *, presentation=None, report=None):
    """Incidence geometry plus explicitly bound provenance; never alter the map.

    Provenance is a separate view relation, not an inference or scientific edge.
    Do not join records by similar text, orphan IDs, or status.
    """
    spec = result.get("graph")
    if not spec:
        return {"nodes": {}, "links": [], "provenance_count": 0, "relations": []}
    nodes, links, groups = {}, [], {}
    adapted = _adapt_record_view(spec) if presentation is None or report is None else None
    report = _record_view_report(spec, adapted=adapted, source_base=result.get("record_source_base_dir")) if report is None else report
    presentation = display_records(result, adapted=adapted, report=report) if presentation is None else presentation
    ids = {n["id"]: f"c{i}" for i, n in enumerate(spec["nodes"])}
    goals = set(spec["goals"])
    palette = {"声明": "#c2c8d2", "执行": "#b39ddb", "回执": "#82c4af", "产物": "#82b6d4", "观测": "#d5c17e"}
    run_ids = {n["id"]: n.get("run_id", n["id"].removeprefix("owned:run:")) for n in spec["nodes"]
               if n.get("record_kind") == "run" and isinstance(n.get("run_id"), str) and n["run_id"]
               or n["id"].startswith("owned:run:") and isinstance(n.get("manifest_sha256"), str)}
    for i, row in enumerate(spec["nodes"]):
        ident = row["id"]
        display = presentation["nodes"][ident]
        kind, label = display["kind"], display["label"]
        nodes[f"c{i}"] = {"type": "", "label": label, "color": {"rgb": int(palette[kind][1:], 16), "a": 1},
                            "rds": {"record": ident, "kind": kind, "size": 1.5 if ident in goals else 1,
                                    "group": None, "color": palette[kind], "outline": display["outline"], "status_text": display["status_text"]}}
    for i, edge in enumerate(spec["hyperedges"]):
        hub = f"h{i}"
        relation = edge.get("relation") if isinstance(edge.get("relation"), str) and edge["relation"].strip() else "依赖"
        members = [ids[p] for p in edge["premises"]] + [ids[edge["conclusion"]]]
        edge_display = presentation["hyperedges"][edge["id"]]
        edge_title = (result.get("readable") or {}).get("hyperedges", {}).get(edge["id"], {}).get("title", "").strip()
        nodes[hub] = {"type": "hyperedge", "label": edge_title or ("AND" if len(edge["premises"]) > 1 else "") + " ◇",
                      "color": {"rgb": 0x9c95af, "a": 1},
                      "rds": {"edge": i, "kind": "超边", "members": members, "size": .65, "group": None,
                              "outline": edge_display["outline"], "status_text": edge_display["status_text"]}}
        style = {"relation": relation, "family": "dependency", "hyperedge": hub, "color": "#bdc2d2",
                 "width": 1.25, "opacity": .55, "dash": edge["status"] != "SUPPORTED", "arrow": False}
        links.extend([[ids[p], hub, dict(style)] for p in edge["premises"]])
        links.append([hub, ids[edge["conclusion"]], {**style, "arrow": True}])
    pairs = {(s, t) for s, t, _ in links}
    provenance, declarations = 0, 0

    def bind(s, t, relation):
        nonlocal provenance, declarations
        if s == t or (s, t) in pairs:
            return
        pairs.add((s, t))
        declaration = relation["kind"] == "declared_output"
        provenance += not declaration
        declarations += declaration
        links.append([s, t, {"relation": "声明输出" if declaration else "来源绑定", "family": "declaration" if declaration else "provenance", "color": "#b7c6c3",
                            "width": .65, "opacity": .32, "dash": True, "arrow": False,
                            "binding": dict(relation)}])

    completions = {(e["premises"][0], e["conclusion"]): f"h{i}" for i, e in enumerate(spec["hyperedges"]) if len(e["premises"]) == 1}
    for relation in report["record_relations"]:
        origin, target = relation["from"], relation["to"]
        bind(ids[origin], ids[target], relation)
        if origin in run_ids:
            if relation["kind"] != "run_receipt" or (origin, target) in completions:
                groups[ids[target]] = run_ids[origin]
            if relation["kind"] == "run_receipt" and (origin, target) in completions:
                groups[completions[origin, target]] = run_ids[origin]
    for ident, run in run_ids.items():
        groups[ids[ident]] = run
    for uid, group in groups.items():
        nodes[uid]["rds"]["group"] = group
    levels = dependency_levels(nodes, links)
    for uid, level in levels.items():
        nodes[uid]["rds"]["flowX"] = level
    # Project containment is a display layer, never a new logical assertion.
    # Join one representative per existing component, rather than a full clique.
    parent = {uid: uid for uid in nodes}
    degree = {uid: 0 for uid in nodes}

    def find(uid):
        while parent[uid] != uid:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    for s, t, _ in links:
        parent[find(t)] = find(s)
        degree[s] += 1
        degree[t] += 1
    components = {}
    for uid in nodes:
        components.setdefault(find(uid), []).append(uid)
    scope_links = []
    for members in components.values():
        representative = max(members, key=lambda uid: (
            nodes[uid]["rds"].get("record") in goals,
            nodes[uid]["rds"]["kind"] == "执行", degree[uid]))
        scope_links.append(["scope", representative, {
            "relation": "项目归属", "family": "membership", "color": "#8196b0",
            "width": .6, "opacity": .23, "dash": True, "arrow": False,
            "binding": {"field": "displayed_snapshot", "source": result.get("source"),
                        "sha256": result.get("snapshot_sha256")}}])
    scope = {"node": {"type": "", "label": "项目快照", "color": {"rgb": 0x8aa1b8, "a": 1},
                      "rds": {"virtual": True, "kind": "项目", "size": 1, "flowX": None,
                              "group": None, "color": "#8aa1b8"}},
             "links": scope_links, "components": len(components),
             "unlinked_records": sum(degree[uid] == 0 for uid in ids.values())}
    return {"nodes": nodes, "links": links, "provenance_count": provenance, "declaration_count": declarations,
            "record_topology": report["record_topology"], "record_relations": report["record_relations"],
            "scope": scope, "relations": sorted({meta["relation"] for _, _, meta in links} | {"项目归属"})}


def _replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Pinned replica patch no longer matches: " + old[:70])
    return source.replace(old, new, 1)


_REPLICA_RENDERER_PATCHES = json.loads(_asset_text("replica/renderer-patches.json"))
_REPLICA_WORKER_PATCHES = json.loads(_asset_text("replica/worker-patches.json"))


def patch_replica_renderer(source):
    """Small, auditable patches to the exact user-selected renderer."""
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[0])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[1])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[2])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[3])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[4])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[5])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[6])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[7])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[8])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[9])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[10])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[11])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[12])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[13])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[14])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[15])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[16])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[17])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[18])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[19])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[20])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[21])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[22])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[23])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[24])
    # Keep upstream node dragging: pin just the grabbed node, reheat to .3,
    # and let adjacent particles follow through their configured forces.
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[25])
    source = _replace_once(source, *_REPLICA_RENDERER_PATCHES[26])
    return source


def patch_replica_worker(source):
    source, count = re.subn(r"importScripts\([\s\S]*?\);", "", source, count=1)
    if count != 1:
        raise ValueError("Pinned replica worker import no longer matches")
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[0])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[1])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[2])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[3])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[4])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[5])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[6])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[7])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[8])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[9])
    source = _replace_once(source, *_REPLICA_WORKER_PATCHES[10])
    return source


def render_replica_html(result, replica_root, pixi_js):
    sources = {}
    for name, expected in REPLICA_FILES.items():
        raw = (Path(replica_root) / "replica" / name).read_bytes()
        # A checkout with LF rather than CRLF must match the same upstream bytes.
        normalized = raw.replace(b"\r\n", b"\n")
        if hashlib.sha256(normalized).hexdigest() != expected:
            raise ValueError("Replica source does not match pinned commit: " + name)
        sources[name] = raw.decode("utf-8").replace("\r\n", "\n")
    pixi = Path(pixi_js).read_bytes()
    if hashlib.sha256(pixi).hexdigest() != PIXI_SHA256:
        raise ValueError("PixiJS must be the official 7.4.3 dist/pixi.min.js")
    spec = result.get("graph") or {}
    adapted = _adapt_record_view(spec) if spec else spec
    report = _record_view_report(spec, adapted=adapted, source_base=result.get("record_source_base_dir")) if spec else {"record_topology": {"issues": []}}
    presentation = display_records(result, adapted=adapted, report=report)
    payload = {**result, "display": presentation, "analysis_notice": analysis_notice(result), "replica_view": replica_view(result, presentation=presentation, report=report),
               "renderer": {"repository": "https://github.com/runningZ1/obsidian-graph-replica", "commit": REPLICA_COMMIT,
                            "pixi": "7.4.3", "runtime_network": False}}
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c")
    data = data.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    # Template replacement happens before data insertion. No data enters JS/HTML.
    assets = {"__STYLE__": sources["style.css"] + REPLICA_EXTRA_CSS,
              "__PIXI__": "/* " + PIXI_LICENSE + " */\n" + pixi.decode("utf-8"), "__D3__": D3_BUNDLE,
              "__WORKER__": patch_replica_worker(sources["sim-worker.js"]),
              "__RENDERER__": patch_replica_renderer(sources["renderer.js"]), "__APP__": REPLICA_APP}
    page = REPLICA_HTML
    for key, value in assets.items():
        page = page.replace(key, value.replace("</script", "<\\/script"))
    return page.replace("__DATA__", data)


PIXI_LICENSE = _asset_text("hypergraph/pixi-LICENSE.txt")

REPLICA_HTML = _asset_text("replica/index.html")

REPLICA_EXTRA_CSS = _asset_text("replica/extra.css")

REPLICA_APP = _asset_text("replica/app.js")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--hypergraph", help="Explicit dependency JSON instead of the saved TMS snapshot")
    parser.add_argument("--archive", help="Complete read-only archive JSON or ZIP, browsed with semantic zoom")
    parser.add_argument("--archive-node", help="Original node ID to inspect with --archive --export-agent-input")
    parser.add_argument("--archive-edge", help="Original edge ID to inspect with --archive --export-agent-input")
    parser.add_argument("--demo", action="store_true", help="Synthetic visual preview only")
    parser.add_argument("--large", action="store_true", help="With --demo: 1,200 synthetic claim nodes")
    parser.add_argument("--output", default="rds-hypergraph.html")
    parser.add_argument("--replica-root", help="Local checkout of the pinned runningZ1/obsidian-graph-replica")
    parser.add_argument("--pixi-js", help="Official pixi.js 7.4.3 dist/pixi.min.js (required with --replica-root)")
    parser.add_argument("--export-agent-input", action="store_true", help="Print separate raw/readable JSON layers to stdout; no HTML is written")
    parser.add_argument("--readable-json", help="Agent-authored readable object bound to this raw graph and snapshot")
    args = parser.parse_args(argv)
    try:
        root, output = Path(args.root).resolve(), Path(args.output).resolve()
        if args.export_agent_input and args.readable_json:
            raise ValueError("Export fresh agent input or consume --readable-json, not both")
        if not args.export_agent_input and (output.suffix.lower() != ".html" or output.is_relative_to((root / ".rds").resolve())):
            raise ValueError("Output must be an .html file outside the .rds ledger")
        if args.demo and args.hypergraph or args.large and not args.demo:
            raise ValueError("Use --large only with --demo, and --demo without --hypergraph")
        if args.archive and (args.hypergraph or args.demo or args.large):
            raise ValueError("Use --archive separately from --hypergraph, --demo and --large")
        if args.archive_node or args.archive_edge:
            if not args.archive or not args.export_agent_input or args.archive_node and args.archive_edge:
                raise ValueError("Inspect one --archive-node or --archive-edge with --archive --export-agent-input")
        if args.archive and args.readable_json:
            raise ValueError("Archive names and summaries come from its readable records; dependency sidecars use --hypergraph")
        if bool(args.replica_root) != bool(args.pixi_js):
            raise ValueError("Use --replica-root and --pixi-js together")
        if args.archive and not args.export_agent_input and not args.replica_root:
            raise ValueError("Archive browsing requires the existing --replica-root and --pixi-js assets")
        if (args.hypergraph or args.archive) and not args.export_agent_input:
            source = Path(args.hypergraph or args.archive).resolve()
            if output == source or output.exists() and source.exists() and output.samefile(source):
                raise ValueError("Output must not overwrite the hypergraph input")
        if args.archive:
            from rds_hypergraph_archive import read_archive, render_archive_html, archive_agent_input
            result = read_archive(args.archive)
            if args.export_agent_input:
                print(json.dumps(archive_agent_input(result, node_id=args.archive_node, edge_id=args.archive_edge),
                                 ensure_ascii=False, allow_nan=False))
                return 0
            page = render_archive_html(result, args.replica_root, args.pixi_js)
        else:
            result = read_graph(root, args.hypergraph, demo=args.demo, large=args.large)
            if args.hypergraph and result["status"] == "UNAVAILABLE":
                raise ValueError(result["reason"])
            if args.export_agent_input:
                from rds_hypergraph_readable import agent_input
                print(json.dumps(agent_input(result), ensure_ascii=False, allow_nan=False))
                return 0
            if args.readable_json:
                from rds_hypergraph_readable import load_readable, with_readable
                readable_path = Path(args.readable_json).resolve()
                if output == readable_path or output.exists() and output.samefile(readable_path):
                    raise ValueError("Output must not overwrite readable information")
                result = with_readable(result, load_readable(readable_path, result))
            page = render_replica_html(result, args.replica_root, args.pixi_js) if args.replica_root else render_html(result)
        output.parent.mkdir(parents=True, exist_ok=True)
        write_html_atomic(output, page)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(output), "status": result["status"], "source": result["source"],
                      "demo": result["demo"], "counts": result.get("counts"),
                      "analysis_status": result.get("analysis_status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
