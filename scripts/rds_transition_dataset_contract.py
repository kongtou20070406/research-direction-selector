"""L2 pure transition dataset validation; no ingestion, splitting or training.

Declared family separation cannot establish independence of unseen exposure.
Receipt locators remain inert metadata; the kernel owns original evidence.
"""
from rds_offline_contract_values import (
    bounded, costs, fields, identity, provenance, require, text, token,
)

SCHEMA = "rds-transition-dataset-v1"
RUN_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"})


def validate_dataset(value):
    value = bounded(value)
    fields(value, "schema dataset versions splits transitions evidence_status", "transition dataset")
    require(value["schema"] == SCHEMA, "Unsupported transition dataset schema")
    identity(value["dataset"], "dataset")
    require(value["evidence_status"] == "UNKNOWN", "Dataset cannot establish scientific gain")
    fields(value["versions"], "engine features policy model", "dataset versions")
    for name, version in value["versions"].items():
        text(version, name + " version", 64)
    fields(value["splits"], "train confirmation", "dataset splits")
    families = {}
    for name, entries in value["splits"].items():
        require(type(entries) is list and 1 <= len(entries) <= 128, "Split needs 1..128 problem families")
        for entry in entries:
            token(entry, "problem family")
        require(len(entries) == len(set(entries)), "Duplicate split family")
        families[name] = set(entries)
    require(not families["train"] & families["confirmation"], "Train/confirmation problem families overlap")
    rows = value["transitions"]
    require(type(rows) is list and len(rows) <= 256, "Transition count exceeds bound")
    seen = set()
    for row in rows:
        fields(row, "id problem_family split decision_input forecasts selection observation before action after run_status failure costs versions provenance",
               "transition")
        token(row["id"], "transition id")
        require(row["id"] not in seen, "Duplicate transition identity")
        seen.add(row["id"])
        token(row["problem_family"], "problem family")
        require(type(row["split"]) is str and row["split"] in families
                and row["problem_family"] in families[row["split"]], "Transition family/split mismatch")
        # Separate envelopes prevent forecasts or selected actions from becoming observations.
        identity(row["decision_input"], "decision input")
        require(type(row["forecasts"]) is list and len(row["forecasts"]) <= 128,
                "Transition forecasts exceed bound")
        for forecast in row["forecasts"]:
            identity(forecast, "forecast reference")
        fields(row["selection"], "candidate_id receipt", "selection")
        token(row["selection"]["candidate_id"], "selected candidate")
        identity(row["selection"]["receipt"], "selection receipt")
        for name in ("observation", "after"):
            if row[name] is not None:
                identity(row[name], name)
        identity(row["before"], "before state")
        fields(row["action"], "candidate_id parameters", "action")
        token(row["action"]["candidate_id"], "action candidate")
        require(row["action"]["candidate_id"] == row["selection"]["candidate_id"],
                "Action differs from recorded selection")
        require(type(row["action"]["parameters"]) is dict, "Action parameters must be JSON values")
        require(type(row["run_status"]) is str and row["run_status"] in RUN_STATUSES,
                "Transition must retain a terminal run status")
        if row["run_status"] == "SUCCEEDED":
            require(row["failure"] is None and row["after"] is not None and row["observation"] is not None,
                    "Successful transition needs after/observation and no failure")
        else:
            fields(row["failure"], "reason receipt", "failure")
            text(row["failure"]["reason"], "failure reason", 2048)
            identity(row["failure"]["receipt"], "failure receipt")
        costs(row["costs"])
        require(row["versions"] == value["versions"], "Transition version lineage mismatch")
        provenance(row["provenance"])
    return value
