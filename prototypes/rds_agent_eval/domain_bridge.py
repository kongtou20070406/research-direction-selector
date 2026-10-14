"""Isolated survey verifier slot; native receipts/CAS/TMS retain all authority."""
import hashlib
import json
from pathlib import Path

from prototypes.rds_agent_eval.advisor_view import read_view
from prototypes.rds_agent_eval.review_inputs import prepare_review
from prototypes.rds_agent_eval import survey_verifier

WORKER_SHA256 = "b1c13284db63d258edb23fbe22285bfcc20a4df7b0ce32824b5e7bfe0b08817e"
VERIFIER_SHA256 = "d62788f5f2c191145dbef380c6b8c30a0711ced21b99a3cc33766781cd8154db"
CLAIM_FIELDS = ("top_roles", "proportion_basis", "bootstrap_ci")


def specification():
    return {"schema": "rds-domain-verifier-slot-v1", "domain": "recorded-survey-q8-v1",
            "worker_sha256": WORKER_SHA256, "verifier_sha256": VERIFIER_SHA256,
            "required_final_fields": ["answer", *CLAIM_FIELDS], "extra_fields": "RETAIN_ALL",
            "final_instruction": "Finish with a JSON object containing answer, top_roles (list or null), "
                "proportion_basis=QUOTED_OVER_QUOTED_PLUS_NOT_QUOTED, bootstrap_ci (null when uncomputed). "
                "Retain evidence/limitations. Query-provided CIs are givens, not independently measured. "
                "Do not infer missingness, population/status filters, bootstrap parameters or significance.",
            "execution_effect": "NONE", "scientific_support": "UNKNOWN"}


def parse_final(text):
    if type(text) is not str or len(text.encode("utf-8")) > 65536:
        raise ValueError("Require bounded original final text")
    value = json.loads(text)
    if (type(value) is not dict or not {"answer", *CLAIM_FIELDS} <= set(value)
            or type(value["answer"]) is not str or not value["answer"].strip()):
        raise ValueError("Explicit full answer and scoped fields required; no defaults", value)
    return {"full_final": value, "claim": {k: value[k] for k in CLAIM_FIELDS}, "free_text_review": "UNKNOWN"}


def inspect(store, prepared, *, expected_snapshot_sha256, outputs, source_path, answer_pins):
    """Read an initialized frozen survey study, without dispatch or assessment write."""
    from rds_project import digest, file_sha, require

    with store._db(readonly=True) as db:
        db.execute("BEGIN")
        contract = store._contract(db)
    require(digest(contract) == digest(prepared["contract"]), "Native contract differs from preflight")
    for role, identity in (("code", WORKER_SHA256), ("evaluator", VERIFIER_SHA256)):
        require(any(b["role"] == role and b["sha256"] == identity
                    and file_sha(store._path(b["path"])) == identity for b in contract["bindings"]),
                "Native survey implementation is not the frozen S22 source")
    implementation = Path(survey_verifier.__file__).resolve()
    require(file_sha(implementation) == VERIFIER_SHA256, "Loaded verifier source differs from declared slot")
    source_file = store._path(source_path).resolve()
    bound = [b for b in contract["bindings"] if b["role"] == "data"
             and store._path(b["path"]).resolve() == source_file]
    require(len(bound) == 1 and source_path in prepared["agent_inputs"]
            and bound[0]["sha256"] == prepared["agent_inputs"][source_path], "Source binding differs")
    with source_file.open("rb") as stream:
        source = stream.read(2097153)
    require(len(source) <= 2097152 and hashlib.sha256(source).hexdigest() == bound[0]["sha256"],
            "Original survey source bytes changed")
    declaration = prepared["declaration"]
    require(answer_pins["requested_model"] == declaration["model"]
            and answer_pins["resolved_model"] == declaration["response_model"]
            and store._path(answer_pins["task"]["path"]).resolve() == store._path(declaration["task_file"]).resolve()
            and answer_pins["task"]["sha256"] == prepared["agent_inputs"][declaration["task_file"]],
            "Original answer task/model identities differ from preflight")
    review = prepare_review(store, expected_snapshot_sha256=expected_snapshot_sha256,
                            outputs=outputs, answer_pins=answer_pins)
    final = parse_final(review["answer"]["final_text"]) if review["answer"]["final_source_status"] == "BOUND_COMPLETE_FINAL" else None
    feedback = review["native_evidence"]
    projected = []
    for observation, entry in zip(feedback["original_observations"]["observations"], feedback["model_feedback"]["observations"]):
        receipt = observation["receipt"]
        if receipt["effective_contract_sha256"] != digest(contract):
            raise ValueError("Original receipt belongs to a different effective contract", review)
        result = {"run_id": receipt["run_id"], "receipt_sha256": receipt["sha256"],
                  "run_status": receipt["run_status"], "measurement_correctness": "UNKNOWN",
                  "structured_claim_correctness": "UNKNOWN", "task_correctness": "UNKNOWN", "verification": None}
        if receipt["run_status"] == "SUCCEEDED" and final is not None:
            artifact = entry["measurement"]["artifact"]
            original = next(a for a in observation["artifacts"] if a["kind"] == "project_output"
                            and store._path(a["path"], True, contract).resolve() == store._path(artifact["path"], True, contract).resolve())
            value = survey_verifier.verify(source, original["bytes"], source_sha256=bound[0]["sha256"],
                                           measured_sha256=artifact["sha256"], claim=final["claim"])
            result.update(measurement_correctness=value["measurement_correctness"],
                          structured_claim_correctness=value["structured_claim_correctness"], verification=value)
        projected.append(result)
    require(file_sha(implementation) == VERIFIER_SHA256
            and digest(read_view(store, expected_snapshot_sha256=expected_snapshot_sha256))
            == digest(feedback["original_advisor"]), "Verifier or native view changed during projection")
    return {"schema": "rds-native-domain-projection-v1", "specification": specification(),
            "original_review_inputs": review, "final": final, "domain_outputs": projected,
            "scope": "SUPPLIED_ORIGINAL_PINS_AND_SCOPED_SURVEY_CLAIM", "execution_effect": "NONE",
            "free_text_review": "UNKNOWN", "scientific_support": "UNKNOWN", "whole_rds_efficacy": "UNMEASURED"}
