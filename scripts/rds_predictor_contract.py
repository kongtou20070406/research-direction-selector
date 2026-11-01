"""L3 pure shadow prediction contract. Caller supplies trusted time/bindings.

Validation never loads a model or executes a provider. Elapsed observations are
caller inputs, not an enforced CPU timeout or an OS isolation claim.
"""
from rds_offline_contract_values import (
    bounded, costs, digest, fields, identity, number, provenance, require, sha256, text, token,
)

REQUEST_SCHEMA = "rds-prediction-request-v1"
RESULT_SCHEMA = "rds-prediction-result-v1"
MAX_CANDIDATES = 128


def validate_request(value):
    value = bounded(value)
    fields(value, "schema model snapshot candidates context versions provenance issued_at expires_at timeout_seconds",
           "prediction request")
    require(value["schema"] == REQUEST_SCHEMA, "Unsupported prediction request schema")
    identity(value["model"], "model")
    identity(value["snapshot"], "snapshot")
    require(type(value["context"]) is dict, "Prediction context must be a JSON object")
    fields(value["versions"], "engine features policy", "prediction versions")
    for key, version in value["versions"].items():
        text(version, key + " version", 64)
    entries = value["candidates"]
    require(type(entries) is list and 1 <= len(entries) <= MAX_CANDIDATES,
            "Prediction candidate count must be 1..128")
    seen = set()
    for entry in entries:
        fields(entry, "id parameters", "candidate")
        token(entry["id"], "candidate id")
        require(entry["id"] not in seen, "Duplicate candidate")
        seen.add(entry["id"])
        require(type(entry["parameters"]) is dict, "Candidate parameters must be a JSON object")
    provenance(value["provenance"])
    number(value["issued_at"], "issued_at", 0)
    number(value["expires_at"], "expires_at", 0)
    require(0 < value["expires_at"] - value["issued_at"] <= 300,
            "Prediction lifetime must be positive and at most 300 seconds")
    number(value["timeout_seconds"], "timeout_seconds", 0.001, 30)
    return value


def request_digest(value):
    return digest(validate_request(value))


def validate_result(request, value):
    request = validate_request(request)
    value = bounded(value)
    fields(value, "schema request_sha256 model snapshot_sha256 status reason reason_code forecasts costs selection_effect evidence_status",
           "prediction result")
    require(value["schema"] == RESULT_SCHEMA, "Unsupported prediction result schema")
    require(value["request_sha256"] == digest(request), "Prediction request binding mismatch")
    require(value["model"] == request["model"], "Prediction model version mismatch")
    require(value["snapshot_sha256"] == request["snapshot"]["sha256"],
            "Prediction snapshot binding mismatch")
    require(value["selection_effect"] == "NONE", "Prediction cannot change selection")
    require(value["evidence_status"] == "UNKNOWN", "Prediction cannot promote scientific truth")
    require(value["status"] in ("SCORED", "ABSTAINED"), "Invalid prediction status")
    require(type(value["reason"]) is str and len(value["reason"]) <= 2048,
            "Invalid prediction reason")
    entries = value["forecasts"]
    require(type(entries) is list and len(entries) <= MAX_CANDIDATES, "Forecast count exceeds bound")
    if value["status"] == "ABSTAINED":
        require(not entries, "Abstention must discard all forecasts")
        text(value["reason"], "Abstention reason", 2048)
        require(value["reason_code"] in ("INVALID", "TIMEOUT", "STALE", "UNAVAILABLE", "PROVIDER_ABSTAINED"),
                "Invalid abstention reason code")
    else:
        require(value["reason_code"] == "NONE", "Scored prediction reason code must be NONE")
        require([entry.get("candidate_id") if type(entry) is dict else None for entry in entries]
                == [entry["id"] for entry in request["candidates"]],
                "Forecasts must cover the original candidate order exactly")
    for entry in entries:
        fields(entry, "candidate_id score uncertainty", "forecast")
        token(entry["candidate_id"], "forecast candidate")
        number(entry["score"], "forecast score")
        if entry["uncertainty"] is not None:
            number(entry["uncertainty"], "forecast uncertainty", 0, 1)
    costs(value["costs"])
    return value


def abstain(request, reason, reason_code="UNAVAILABLE"):
    """Construct a bound abstention for an already valid request."""
    request = validate_request(request)
    return validate_result(request, {
        "schema": RESULT_SCHEMA, "request_sha256": digest(request), "model": request["model"],
        "snapshot_sha256": request["snapshot"]["sha256"], "status": "ABSTAINED",
        "reason": reason, "reason_code": reason_code, "forecasts": [],
        "costs": {"wall_seconds": None, "cpu_seconds": None, "status": "UNKNOWN"},
        "selection_effect": "NONE", "evidence_status": "UNKNOWN",
    })


def _unbound_invalid(reason):
    return {"schema": RESULT_SCHEMA, "request_sha256": None, "model": None,
            "snapshot_sha256": None, "status": "ABSTAINED", "reason_code": "INVALID",
            "reason": reason, "forecasts": [], "selection_effect": "NONE",
            "evidence_status": "UNKNOWN",
            "costs": {"wall_seconds": None, "cpu_seconds": None, "status": "UNKNOWN"}}


def assess(request, response, *, now, snapshot_sha256, elapsed_seconds, observed_costs=None):
    """Fail closed on invalid, timed-out or stale values; no provider is invoked.

Invalid requests produce an unbound abstention (null identities). The caller
    may supply independently observed costs, which survive discarded forecasts.
    This does not settle/refund the existing ledger or verify provider costs.
"""
    measured = None
    try:
        if observed_costs is not None:
            measured = bounded(observed_costs)
            costs(measured)
    except ValueError as exc:
        return _unbound_invalid("Invalid caller cost observations: " + str(exc))

    def finish(report):
        if measured is not None:
            report["costs"] = measured
        return report

    try:
        request = validate_request(request)
    except ValueError as exc:
        return finish(_unbound_invalid(str(exc)))
    try:
        number(now, "caller now", 0)
        sha256(snapshot_sha256, "caller snapshot hash")
        number(elapsed_seconds, "caller elapsed seconds", 0)
        if elapsed_seconds >= request["timeout_seconds"]:
            return finish(abstain(request, "Caller observed prediction deadline exhaustion", "TIMEOUT"))
        if not request["issued_at"] <= now < request["expires_at"] or snapshot_sha256 != request["snapshot"]["sha256"]:
            return finish(abstain(request, "Prediction snapshot or time window is stale", "STALE"))
        if response is None:
            return finish(abstain(request, "Trusted CPU prediction infrastructure is not implemented", "UNAVAILABLE"))
        return finish(validate_result(request, response))
    except ValueError as exc:
        return finish(abstain(request, str(exc), "INVALID"))
