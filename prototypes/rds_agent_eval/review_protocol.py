"""Prospective rubric freeze only; identities and hashes do not establish independence."""
import hashlib
import json

from rds_offline_contract_values import bounded, digest, fields, require, sha256, text

CRITERIA = {"answer_relevance", "measurement_faithfulness", "claim_scope",
            "evidence_independence", "uncertainty_consistency"}
EVIDENCE = ["full_final_answer_and_extra_fields", "original_request_response_summary_task",
            "original_measured_bytes_and_pins", "failed_unknown_attempts_and_costs",
            "frozen_independent_verifier_outputs"]


def freeze_protocol(raw, *, expected_sha256, task_id, used_task_ids):
    """Return a pinned protocol for an unused declared task, never a review score.

    No original answer, verifier, reviewer, native ledger or execution is read
    here. Future study admission must bind concrete reviewers/verifiers separately.
    """
    require(type(raw) is bytes and len(raw) <= 16384, "Require bounded original rubric bytes")
    sha256(expected_sha256, "rubric identity")
    require(hashlib.sha256(raw).hexdigest() == expected_sha256, "Original rubric bytes differ")
    text(task_id, "new task identity", 256)
    used = bounded(used_task_ids)
    require(type(used) is list and len(used) <= 4096 and all(type(t) is str for t in used)
            and len(set(used)) == len(used) and task_id not in used,
            "Require complete declared prior IDs; no retrospective protocol")
    spec = bounded(json.loads(raw))
    fields(spec, "schema scope criteria required_evidence reviewer verifier verdicts", "review rubric")
    require(spec["schema"] == "rds-prospective-semantic-rubric-v1"
            and spec["scope"] == "FULL_FINAL_ANSWER_AND_EXTRA_FIELDS"
            and spec["required_evidence"] == EVIDENCE and set(spec["criteria"]) == CRITERIA
            and spec["verdicts"] == ["PASS", "FAIL", "UNKNOWN"], "Incomplete prospective rubric")
    for name, rule in spec["criteria"].items():
        fields(rule, "question pass_rule fail_rule unknown_rule", name)
        for field, value in rule.items():
            text(value, name + " " + field, 1024)
    for name in ("reviewer", "verifier"):
        entry = spec[name]
        fields(entry, "identity source_sha256", name)
        text(entry["identity"], name + " identity", 256)
        if entry["identity"] == "UNKNOWN":
            require(entry["source_sha256"] is None, "Unknown implementation cannot have an inferred pin")
        else:
            sha256(entry["source_sha256"], name + " source")
    return {"schema": "rds-semantic-protocol-freeze-v1", "protocol": spec,
            "raw_rubric_sha256": expected_sha256, "protocol_sha256": digest(spec), "task_id": task_id,
            "used_task_ids": used, "status": "FROZEN_PROTOCOL_ONLY", "study_readiness": "UNKNOWN",
            "identity_assurance": "DECLARATION_ONLY", "independence": "UNKNOWN",
            "task_novelty_scope": "DECLARED_ID_INVENTORY_ONLY", "free_text_review": "UNKNOWN",
            "scientific_support": "UNKNOWN", "execution_effect": "NONE", "assessment_effect": "NONE"}
