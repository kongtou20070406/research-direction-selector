"""Pure interface reservation for reflective world models; no dispatch or storage.

Replay acceptance is scoped to supplied transitions. It never verifies their
receipt contents, authorizes actions, or promotes scientific support.
"""
from rds_offline_contract_values import bounded, digest, fields, identity, require, sha256, text

SCHEMA = "rds-world-model-candidate-v1"


def history_digest(transitions):
    transitions = bounded(transitions)
    require(type(transitions) is list and 1 <= len(transitions) <= 128,
            "History must contain 1..128 measured transitions")
    seen = set()
    for row in transitions:
        fields(row, "id before action after receipt_sha256", "transition")
        text(row["id"], "transition id", 128)
        require(row["id"] not in seen, "Duplicate transition identity")
        seen.add(row["id"])
        for key in ("before", "action", "after"):
            require(type(row[key]) is dict, key + " must be a JSON object")
        sha256(row["receipt_sha256"], "original receipt identity")
    return digest(transitions)


def validate_candidate(value):
    value = bounded(value)
    fields(value, "schema model revision parent_sha256 rulebook executable history_sha256",
           "world model candidate")
    require(value["schema"] == SCHEMA, "Unsupported world model schema")
    identity(value["model"], "frozen model")
    require(type(value["revision"]) is int and 1 <= value["revision"] <= 100000,
            "Revision must be a positive integer")
    if value["revision"] == 1:
        require(value["parent_sha256"] is None, "First revision cannot have a parent")
    else:
        sha256(value["parent_sha256"], "parent candidate")
    for kind in ("rulebook", "executable"):
        fields(value[kind], "sha256 format", kind)
        sha256(value[kind]["sha256"], kind + " identity")
        text(value[kind]["format"], kind + " format", 64)
    sha256(value["history_sha256"], "history identity")
    return value


def assess_replay(candidate, transitions, predictions, *, semantic_review,
                  current_parent_sha256, receipt_identities):
    """Trusted caller supplies original retained receipt identities and review.

    A matching hash is a binding check, not a replacement for receipt decoding.
    No callbacks, code imports, model calls or ledger writes occur here.
    """
    candidate = validate_candidate(candidate)
    transitions, predictions = bounded(transitions), bounded(predictions)
    require(candidate["history_sha256"] == history_digest(transitions), "History binding mismatch")
    require(candidate["parent_sha256"] == current_parent_sha256, "Stale parent revision")
    fields(semantic_review, "candidate_sha256 verdict", "semantic review")
    require(semantic_review["candidate_sha256"] == digest(candidate), "Review binding mismatch")
    require(semantic_review["verdict"] in ("PASS", "FAIL", "UNKNOWN"), "Invalid review verdict")
    expected_ids = [row["id"] for row in transitions]
    require(type(predictions) is list, "Predictions must be a list")
    for row in predictions:
        fields(row, "id after", "prediction")
    require([row["id"] for row in predictions] == expected_ids, "Replay must cover full history in order")
    require(type(receipt_identities) is dict and set(receipt_identities) == set(expected_ids),
            "Trusted receipt denominator mismatch")
    require(all(receipt_identities[row["id"]] == row["receipt_sha256"] for row in transitions),
            "Original receipt binding mismatch")
    mismatches = [row["id"] for row, pred in zip(transitions, predictions)
                  if digest(row["after"]) != digest(pred["after"])]
    accepted = semantic_review["verdict"] == "PASS" and not mismatches
    return {"schema": "rds-world-model-replay-v1", "candidate_sha256": digest(candidate),
            "history_sha256": candidate["history_sha256"], "transitions": len(transitions),
            "mismatches": mismatches, "status": "ACCEPTED" if accepted else "REJECTED",
            "scope": "SUPPLIED_HISTORY_ONLY", "receipt_assurance": "CALLER_BOUND",
            "selection_effect": "NONE", "evidence_status": "UNKNOWN"}
