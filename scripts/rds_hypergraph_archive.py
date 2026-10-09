"""Bounded, read-only archive import and complete semantic-zoom presentation.

Archive membership, history and provenance are display relations, never inputs
to scientific inference. The selected original JSON bytes remain separately
bound and available to the agent layer without changing their schema.
"""
from collections import Counter, defaultdict
import base64
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile
import zlib

MAX_BYTES = 512 * 1024 * 1024
MAX_NODES = 250_000
MAX_EDGES = 2_000_000
LEAF_SIZE = 64
ARCHIVE_SCHEMAS = {"rds-readonly-archive-v1", "refrm-unified-evidence-graph-v1"}
ZIP_ENTRIES = ("ALL_NODES_AND_EDGES.json", "ReFRM_RDS/unified_graph/graph.json")


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key: " + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("Non-finite JSON number: " + value)

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            nonfinite(value)
        return number

    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                          parse_constant=nonfinite, parse_float=finite)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Archive must contain bounded UTF-8 JSON") from exc


def _read_zip_entry(archive, name, limit=MAX_BYTES):
    matches = [info for info in archive.infolist() if info.filename == name]
    if len(matches) != 1:
        raise ValueError("Archive entry must occur exactly once: " + name)
    info = matches[0]
    if info.file_size > limit:
        raise ValueError("Archive JSON exceeds byte limit")
    try:
        with archive.open(info) as stream:
            raw = stream.read(limit + 1)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error) as exc:
        raise ValueError("Archive ZIP entry is damaged, encrypted or unsupported: " + name) from exc
    if len(raw) > limit or len(raw) != info.file_size:
        raise ValueError("Archive entry length differs or exceeds byte limit")
    return raw


def read_archive(path, *, entry=None):
    try:
        return _read_archive(path, entry=entry)
    except zipfile.BadZipFile as exc:
        raise ValueError("Archive ZIP is damaged or invalid") from exc


def _read_archive(path, *, entry=None):
    """Read an explicit ZIP/JSON; do not extract files or execute bundle code."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            entry = entry or next((name for name in ZIP_ENTRIES if name in names), None)
            if not entry or entry.startswith(("/", "\\")) or "\\" in entry or any(
                    part in ("", ".", "..") for part in entry.split("/")) or ":" in entry:
                raise ValueError("An explicit safe archive graph entry is required")
            manifest = _json(_read_zip_entry(archive, "MANIFEST.json", 8 * 1024 * 1024))
            files = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(files, list):
                raise ValueError("Archive manifest must list bound files")
            bindings = [item for item in files if isinstance(item, dict) and item.get("path") == entry]
            if len(bindings) != 1:
                raise ValueError("Selected graph requires one manifest binding")
            binding = bindings[0]
            if type(binding.get("bytes")) is not int or not isinstance(binding.get("sha256"), str):
                raise ValueError("Invalid graph manifest binding")
            raw = _read_zip_entry(archive, entry)
            if len(raw) != binding["bytes"] or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
                raise ValueError("Selected graph manifest length/SHA mismatch")
    else:
        if entry is not None:
            raise ValueError("Entry selection applies only to ZIP archives")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Archive JSON exceeds byte limit")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Archive JSON exceeds byte limit")
    graph = _json(raw)
    display = build_archive_display(graph)
    digest = hashlib.sha256(raw).hexdigest()
    display["source"] = {"name": path.name, "entry": entry, "schema": graph["schema"],
                         "node_count": len(display["nodes"]), "edge_count": len(display["edges"]),
                         "raw_sha256": digest}
    return {"graph": graph, "raw": raw, "display": display, "source": str(path.resolve()),
            "entry": entry, "raw_sha256": digest, "graph_sha256": digest,
            "status": "AVAILABLE", "demo": False, "analysis_status": "NOT_RUN_ARCHIVE",
            "counts": {"nodes": len(display["nodes"]), "edges": len(display["edges"]),
                       "hyperedges": sum(node["type"] == "hyperedge" for node in display["nodes"])}}


def archive_agent_input(bundle, node_id=None, edge_id=None):
    """Return explicitly requested raw/readable records with their source binding."""
    if node_id is not None and edge_id is not None:
        raise ValueError("Select one archive node or edge")
    graph, display = bundle["graph"], bundle["display"]
    binding = {"source": bundle["source"], "entry": bundle["entry"],
               "graph_sha256": bundle["graph_sha256"], "schema": graph["schema"]}
    if node_id is not None:
        matches = [i for i, row in enumerate(graph["nodes"]) if row["id"] == node_id]
        if not matches:
            raise ValueError("Unknown raw archive node ID")
        index = matches[0]
        return {"raw": graph["nodes"][index], "readable": display["nodes"][index], "binding": binding}
    if edge_id is not None:
        originals = graph["hyperedges"] if graph["schema"] == 1 else graph["edges"]
        matches = [i for i, row in enumerate(originals) if row["id"] == edge_id]
        if not matches:
            raise ValueError("Unknown raw archive edge ID")
        index = matches[0]
        row = originals[index]
        labels = {node["id"]: node["label"] for node in display["nodes"]}
        descriptions = {"membership": "档案收录或归属；不表示科学支持", "history": "历史版本或快照先后；保持各自原范围",
                        "provenance": "来源或证据引用；不表示科学证明",
                        "original_dependency": "原快照中的依赖；共同前提须同时满足"}
        readable = {"label": _text(row.get("label"), "AND 规则" if graph["schema"] == 1 else "记录关联"),
                    "summary": _text(row.get("summary")) or ("原 AND 规则；共同前提须同时满足" if graph["schema"] == 1 else descriptions.get(row.get("semantic"), "原始记录关联；语义待确认")),
                    "type": "hyperedge" if graph["schema"] == 1 else row.get("type", "UNKNOWN"),
                    "status": row.get("status") if isinstance(row.get("status"), str) else "UNKNOWN",
                    "incidences": [item for item in display["edges"] if item[4] == index],
                    "semantics": display["semantics"], "edge_types": display["edge_types"]}
        if graph["schema"] == 1:
            readable.update(premises=[labels[id] for id in row["premises"]], conclusion=labels[row["conclusion"]])
        else:
            readable.update(source=labels[row["source"]], target=labels[row["target"]], semantic=row.get("semantic", "UNKNOWN"))
        return {"raw": row, "readable": readable,
                "binding": binding}
    return {"raw": graph, "readable": display, "binding": binding}


def _identity(row, seen, name):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
        raise ValueError(name + " requires a nonempty string ID")
    if row["id"] in seen:
        raise ValueError("Duplicate " + name + " ID: " + row["id"])
    seen.add(row["id"])


def _text(value, default=""):
    from rds_hypergraph_view import readable_text
    if isinstance(value, dict):
        value = next((value[key] for key in ("zh", "en")
                      if isinstance(value.get(key), str) and value[key].strip()), default)
    if not isinstance(value, str):
        value = default
    return readable_text(value).strip()


def build_archive_display(graph):
    """Validate identity/endpoints and build every display item without truncation."""
    if not isinstance(graph, dict) or not (
            isinstance(graph.get("schema"), str) and graph["schema"] in ARCHIVE_SCHEMAS
            or type(graph.get("schema")) is int and graph["schema"] == 1):
        raise ValueError("Unsupported explicit archive graph schema")
    native = graph["schema"] == 1
    rows = graph.get("nodes")
    originals = graph.get("hyperedges") if native else graph.get("edges")
    if not isinstance(rows, list) or not isinstance(originals, list):
        raise ValueError("Archive nodes and edges must be arrays")
    if len(rows) + (len(originals) if native else 0) > MAX_NODES:
        raise ValueError("Archive exceeds node limit")
    if len(originals) > MAX_EDGES:
        raise ValueError("Archive exceeds edge limit")
    node_ids, edge_ids = set(), set()
    nodes, node_index = [], {}
    declared_groups = graph.get("groups", [])
    groups = {}
    if isinstance(declared_groups, list):
        for row in declared_groups:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                if row["id"] in groups:
                    raise ValueError("Duplicate declared group ID: " + row["id"])
                groups[row["id"]] = {"id": row["id"], "label": _text(row.get("label"), row["id"]),
                                      "summary": _text(row.get("summary"))}
    for i, row in enumerate(rows):
        _identity(row, node_ids, "node")
        node_index[row["id"]] = i
        kind = row.get("type") if isinstance(row.get("type"), str) else "record"
        group = row.get("group") if isinstance(row.get("group"), str) and row["group"] else "type:" + kind
        groups.setdefault(group, {"id": group, "label": _text(group), "summary": ""})
        label = _text(row.get("label")) or _text(row.get("claim"))
        if not label:
            from rds_hypergraph_view import readable_text
            label = readable_text(row["id"], machine=True).strip(" .:_-/") or "记录 " + str(i + 1)
            if "/" in label or "\\" in label:
                label = label.replace("\\", "/").rsplit("/", 1)[-1]
        nodes.append({"id": row["id"], "label": label, "summary": _text(row.get("summary")),
                      "type": kind, "status": row.get("status") if isinstance(row.get("status"), str) else "UNKNOWN",
                      "group": group, "primary": row.get("primary") is True,
                      "source_id": row.get("source_id") if isinstance(row.get("source_id"), str) else None,
                      "raw_node_index": i, "raw_hyperedge_id": None, "degree": 0})
    edges, semantics, edge_types = [], [], []
    semantic_index, type_index = {}, {}

    def add_edge(s, t, semantic, kind, index):
        if not isinstance(s, str) or not isinstance(t, str) or s not in node_index or t not in node_index:
            raise ValueError("Dangling archive edge endpoint")
        if not isinstance(semantic, str) or not isinstance(kind, str):
            raise ValueError("Archive edge semantic/type must be strings")
        if semantic not in semantic_index:
            semantic_index[semantic] = len(semantics); semantics.append(semantic)
        if kind not in type_index:
            type_index[kind] = len(edge_types); edge_types.append(kind)
        a, b = node_index[s], node_index[t]
        nodes[a]["degree"] += 1; nodes[b]["degree"] += 1
        edges.append([a, b, semantic_index[semantic], type_index[kind], index])

    for i, row in enumerate(originals):
        _identity(row, edge_ids, "edge")
        if not native:
            add_edge(row.get("source"), row.get("target"), row.get("semantic", "UNKNOWN"), row.get("type", "UNKNOWN"), i)
            continue
        premises = row.get("premises")
        if not isinstance(premises, list) or not all(isinstance(p, str) for p in premises) or not isinstance(row.get("conclusion"), str):
            raise ValueError("Native hyperedge requires premises and conclusion")
        if len(edges) + len(premises) + 1 > MAX_EDGES:
            raise ValueError("Archive exceeds edge limit")
        hub = "archive:hyperedge:" + row["id"]
        while hub in node_index:
            hub = "archive:" + hub
        node_index[hub] = len(nodes)
        group = "type:hyperedge"
        groups.setdefault(group, {"id": group, "label": "超边", "summary": "原 AND 规则；显示不验收科学支持"})
        nodes.append({"id": hub, "label": _text(row.get("label"), "AND 规则 " + str(i + 1)),
                      "summary": "共同前提须同时满足；保持原规则身份与状态", "type": "hyperedge",
                      "status": row.get("status") if isinstance(row.get("status"), str) else "UNKNOWN",
                      "group": group, "primary": False, "source_id": None, "raw_node_index": None,
                      "raw_hyperedge_id": row["id"], "degree": 0})
        for premise in premises:
            add_edge(premise, hub, "original_dependency", "and_premise", i)
        add_edge(hub, row["conclusion"], "original_dependency", "and_conclusion", i)
    clusters, roots = _clusters(nodes, edges, groups)
    bounds = [min((c["bounds"][0] for c in clusters), default=0), min((c["bounds"][1] for c in clusters), default=0),
              max((c["bounds"][2] for c in clusters), default=1), max((c["bounds"][3] for c in clusters), default=1)]
    return {"schema": "rds-archive-display-v1", "nodes": nodes, "edges": edges, "semantics": semantics,
            "edge_types": edge_types, "groups": list(groups.values()), "clusters": clusters, "roots": roots,
            "bounds": bounds}


def _clusters(nodes, edges, groups):
    buckets = defaultdict(lambda: defaultdict(list))
    by_id = {node["id"]: node for node in nodes}
    source_records = defaultdict(list)
    for i, node in enumerate(nodes):
        buckets[node["group"]][node["source_id"] or "type:" + node["type"]].append(i)
        if node["type"] == "source" and node["source_id"]:
            source_records[node["source_id"]].append(node)
    clusters, roots = [], []

    def source_label(source):
        record = by_id.get(source)
        if record is None and len(source_records[source]) == 1:
            record = source_records[source][0]
        if record is not None:
            return record["label"]
        from rds_hypergraph_view import readable_text
        text = source.removeprefix("source:").removeprefix("type:")
        return readable_text(text, machine=True) or "来源"

    def create(id, label, level, parent, members=()):
        index = len(clusters)
        clusters.append({"id": "cluster:" + str(index), "label": label, "level": level, "parent": parent, "children": [],
                         "nodes": list(members), "node_count": len(members), "edge_count": 0,
                         "status_counts": dict(Counter(nodes[i]["status"] for i in members))})
        if parent is not None:
            clusters[parent]["children"].append(index)
        return index

    for group, sources in buckets.items():
        root = create("group:" + group, groups[group]["label"], 0, None); roots.append(root)
        for source, members in sources.items():
            parent = create("source:" + group + ":" + source, source_label(source), 1, root)
            for start in range(0, len(members), LEAF_SIZE):
                subset = members[start:start + LEAF_SIZE]
                leaf = create("block:" + str(parent) + ":" + str(start // LEAF_SIZE), "记录组 " + str(start // LEAF_SIZE + 1), 2, parent, subset)
                for i in subset:
                    nodes[i]["parent"] = leaf
    # Disjoint angular sectors pack unequal disks without all-pairs collision work.
    # Each ring also lies outside the previous ring's complete radial envelope.
    def pack(indices, gap):
        ordered = sorted(indices, key=lambda child: (-clusters[child]["radius"], child))
        if not ordered:
            return {}, 0
        offsets = {ordered[0]: (0, 0)}
        envelope = clusters[ordered[0]]["radius"]
        cursor = 1
        while cursor < len(ordered):
            largest = clusters[ordered[cursor]]["radius"]
            distance = envelope + largest + gap
            angle = 0
            while cursor < len(ordered):
                child = ordered[cursor]
                half = math.asin((clusters[child]["radius"] + gap / 2) / distance)
                if angle + 2 * half > 2 * math.pi:
                    break
                middle = angle + half
                offsets[child] = (distance * math.cos(middle), distance * math.sin(middle))
                angle += 2 * half
                cursor += 1
            envelope = distance + largest
        return offsets, envelope

    golden_angle = math.pi * (3 - math.sqrt(5))
    child_offsets = {}

    def size(index):
        cluster = clusters[index]
        if not cluster["children"]:
            cluster["radius"] = 26 * math.sqrt(max(0, len(cluster["nodes"]) - 1)) + 28
        else:
            for child in cluster["children"]:
                size(child)
            offsets, envelope = pack(cluster["children"], 20)
            child_offsets[index] = offsets
            cluster["radius"] = envelope + 24
            counts = Counter()
            for child in cluster["children"]:
                cluster["node_count"] += clusters[child]["node_count"]
                counts.update(clusters[child]["status_counts"])
            cluster["status_counts"] = dict(counts)
    for root in roots:
        size(root)
    root_offsets, _ = pack(roots, 64)

    def place(index, x, y):
        cluster = clusters[index]; radius = cluster["radius"]
        cluster.update(x=x, y=y, bounds=[x - radius, y - radius, x + radius, y + radius])
        for child in cluster["children"]:
            dx, dy = child_offsets[index][child]
            place(child, x + dx, y + dy)
        for ordinal, node in enumerate(cluster["nodes"]):
            distance = 26 * math.sqrt(ordinal)
            nodes[node].update(x=x + distance * math.cos(ordinal * golden_angle),
                               y=y + distance * math.sin(ordinal * golden_angle))

    for root in roots:
        place(root, *root_offsets[root])
    ancestors = []
    for node in nodes:
        chain = []; index = node["parent"]
        while index is not None:
            chain.append(index); index = clusters[index]["parent"]
        ancestors.append(chain)
    for a, b, *_ in edges:
        for index in set(ancestors[a]) | set(ancestors[b]):
            clusters[index]["edge_count"] += 1
    return clusters, roots


def _packed(raw):
    return base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")


def render_archive_html(bundle, replica_root, pixi_js):
    """Use the same pinned offline renderer bytes as the scientific viewer."""
    from rds_hypergraph_view import (D3_BUNDLE, PIXI_LICENSE, PIXI_SHA256,
                                    REPLICA_FILES, patch_replica_renderer)
    sources = {}
    for name, expected in REPLICA_FILES.items():
        raw = (Path(replica_root) / "replica" / name).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Replica source does not match pinned commit: " + name)
        sources[name] = raw.decode("utf-8")
    pixi = Path(pixi_js).read_bytes()
    if hashlib.sha256(pixi).hexdigest() != PIXI_SHA256:
        raise ValueError("PixiJS must be the official 7.4.3 dist/pixi.min.js")
    assets = Path(__file__).parent / "hypergraph_archive"
    page = (assets / "index.html").read_text(encoding="utf-8")
    substitutions = {"@@STYLE@@": (assets / "style.css").read_text(encoding="utf-8"),
                     "@@PIXI@@": "/* " + PIXI_LICENSE + " */\n" + pixi.decode("utf-8"),
                     "@@RENDERER@@": patch_replica_renderer(sources["renderer.js"]),
                     "@@D3@@": D3_BUNDLE,
                     "@@WORKER@@": (assets / "worker.js").read_text(encoding="utf-8"),
                     "@@EXPLORER@@": (assets / "explorer.js").read_text(encoding="utf-8")}
    for key, value in substitutions.items():
        page = page.replace(key, re.sub(r"</script", r"<\\/script", value, flags=re.I))
    display = json.dumps(bundle["display"], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    return page.replace("@@ARCHIVE_DATA@@", _packed(display)).replace("@@ARCHIVE_RAW@@", _packed(bundle["raw"]))
