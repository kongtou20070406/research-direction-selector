"""Shared offline rendering assets and recorded-result presentation."""
import json
from pathlib import Path
import re


def _asset_text(relative):
    return (Path(__file__).resolve().parent / "assets" / relative).read_bytes().decode("utf-8")


# Official UMD distributions, pinned and embedded for a single-file offline export.
# Refresh from npm tarballs only; retain each upstream ISC license and version.
D3_BUNDLE = _asset_text("hypergraph/d3-bundle.js")


# The selected replica has no published license at this pinned revision.
# Read its local checkout at export time; do not redistribute it here.
REPLICA_COMMIT = "b811f4d12f909d496d44c7f2d98b3a408eb1a627"


REPLICA_FILES = {
    "renderer.js": "e3b0aed506ab9ed6717ef1ef2fb177eb08c52be574bdbba5cff612dd62c75ca7",
    "sim-worker.js": "cd618311568bd64bef2b52ea0c302d17c25eeb4bc6c8041f5d312c284bc07316",
    "style.css": "3f3c8ce502225c01b664d3842643301bd926a8e81252152fecd7c438f29eb59d",
}


PIXI_SHA256 = "712a3e1943b062a907d86c5bb56b67c7b32a3f03b3654768b00b7016d30a24c6"


PIXI_LICENSE = _asset_text("hypergraph/pixi-LICENSE.txt")


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


def _replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Pinned replica patch no longer matches: " + old[:70])
    return source.replace(old, new, 1)


_REPLICA_RENDERER_PATCHES = json.loads(_asset_text("replica/renderer-patches.json"))


def patch_replica_renderer(source, *, replace_once=None):
    """Small, auditable patches to the exact user-selected renderer."""
    replace_once = _replace_once if replace_once is None else replace_once
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[0])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[1])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[2])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[3])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[4])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[5])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[6])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[7])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[8])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[9])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[10])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[11])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[12])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[13])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[14])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[15])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[16])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[17])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[18])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[19])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[20])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[21])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[22])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[23])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[24])
    # Keep upstream node dragging: pin just the grabbed node, reheat to .3,
    # and let adjacent particles follow through their configured forces.
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[25])
    source = replace_once(source, *_REPLICA_RENDERER_PATCHES[26])
    return source
