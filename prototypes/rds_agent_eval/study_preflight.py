"""Isolated matched-study preflight; native assembly retains all authority.

No project initialization, dispatch, provider request, persistence or defaults.
New task IDs and asset separation are declared checks, not scientific holdouts.
"""
from copy import deepcopy
import time


def prepare(store, recipe, declaration, authorization):
    from rds_project import digest, file_sha, require
    from rds_project_assembly import _object, compile_recipe

    _object(declaration, {"schema", "task_id", "task_file", "agent_files", "gold_files",
        "prior_task_ids", "model", "response_model", "per_arm_calls", "deadline_epoch",
        "completion_protocol"}, set(), "study declaration")
    require(type(declaration["schema"]) is int and declaration["schema"] == 1,
            "Study schema must be 1")
    prior = declaration["prior_task_ids"]
    require(type(prior) is list and len(prior) <= 4096 and all(type(x) is str for x in prior)
            and len(set(prior)) == len(prior) and {t["id"] for t in authorization["tasks"]} <= set(prior),
            "Prior task inventory must retain authorized batch IDs")
    require(type(declaration["task_id"]) is str and declaration["task_id"].strip()
            and declaration["task_id"] not in prior, "Task ID is missing or already used")
    require(declaration["model"] == authorization["model"]
            and declaration["response_model"] == authorization["response_model"], "Frozen model differs")
    require(type(declaration["per_arm_calls"]) is int
            and declaration["per_arm_calls"] == authorization["per_pair_calls"] // 2,
            "Matched arm call cap differs")
    require(declaration["deadline_epoch"] == authorization["deadline_epoch"]
            and time.time() < declaration["deadline_epoch"], "Original deadline changed or reached")
    require(declaration["completion_protocol"] == "finish-function-or-stop-text-v2",
            "Study must freeze the inspected terminal protocol")
    for field in ("agent_files", "gold_files"):
        require(type(declaration[field]) is list and len(declaration[field]) <= 512,
                "Study assets must be explicit bounded lists")
    exposed = [store._path(p).resolve() for p in declaration["agent_files"]]
    data = {store._path(p).resolve() for p in recipe["files"]["data"]}
    task = store._path(declaration["task_file"]).resolve()
    hidden = {store._path(p).resolve() for p in declaration["gold_files"] + recipe["files"]["evaluator"]}
    require(len(set(exposed)) == len(exposed) and set(exposed) == data | {task}
            and task not in data and not set(exposed).intersection(hidden),
            "Agent assets must be exactly task plus data, with no gold/evaluator alias")
    require(all(p.is_file() for p in [*exposed, *hidden]), "Declared study asset is missing")
    contract, protocol, assembly = compile_recipe(store, recipe)
    inputs = {p: file_sha(store._path(p)) for p in declaration["agent_files"]}
    require(all(file_sha(store._path(b["path"])) == b["sha256"] for b in contract["bindings"]
                if b["role"] != "protocol"), "Native recipe input changed during preflight")
    return {"schema": "rds-matched-study-preflight-v1", "status": "PREPARED_NOT_INITIALIZED",
            "declaration": deepcopy(declaration), "declaration_sha256": digest(declaration),
            "authorization_sha256": digest(authorization), "agent_inputs": inputs,
            "contract": contract, "protocol_bytes": protocol, "assembly": assembly,
            "bare_runs": [deepcopy(r["manifest"]) for r in contract["advisor_policy"]["routes"]],
            "execution_started": False, "execution_authorized": False,
            "task_novelty_scope": "DECLARED_ID_INVENTORY_ONLY", "independent_sources": "UNKNOWN",
            "scientific_support": "UNKNOWN", "whole_rds_efficacy": "UNMEASURED"}
