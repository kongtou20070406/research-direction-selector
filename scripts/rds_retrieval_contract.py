"""L3 offline retrieval envelopes; similarity proposes, never proves isomorphism.

All locators and model descriptors are inert data. No provider implementation,
file access, embeddings, graph proof, admission or state mutation is supplied.
"""
from rds_offline_contract_values import (
    bounded, costs, digest, fields, identity, number, provenance, require, text, token,
)

REQUEST_SCHEMA = "rds-retrieval-request-v1"
RESULT_SCHEMA = "rds-retrieval-result-v1"
FILTERS = frozenset({"problem_families", "kinds", "tags", "source_ids"})


def validate_request(value):
    value = bounded(value)
    fields(value, "schema corpus model query top_k filters provenance", "retrieval request")
    require(value["schema"] == REQUEST_SCHEMA, "Unsupported retrieval request schema")
    identity(value["corpus"], "corpus")
    identity(value["model"], "model")
    text(value["query"], "query")
    require(type(value["top_k"]) is int and 1 <= value["top_k"] <= 100,
            "top_k must be an integer in 1..100")
    filters = value["filters"]
    require(type(filters) is dict and set(filters) <= FILTERS, "Unsupported retrieval filters")
    for key, entries in filters.items():
        require(type(entries) is list and len(entries) <= 100, "Filter list exceeds bound")
        for entry in entries:
            token(entry, key)
        require(len(entries) == len(set(entries)), "Duplicate filter value")
    provenance(value["provenance"])
    return value


def request_digest(value):
    return digest(validate_request(value))


def validate_result(request, value):
    request = validate_request(request)
    value = bounded(value)
    fields(value, "schema request_sha256 corpus model status reason hits costs evidence_status",
           "retrieval result")
    require(value["schema"] == RESULT_SCHEMA, "Unsupported retrieval result schema")
    require(value["request_sha256"] == digest(request), "Retrieval request binding mismatch")
    require(value["corpus"] == request["corpus"] and value["model"] == request["model"],
            "Retrieval corpus/model version mismatch")
    require(value["status"] in ("PROPOSED", "UNAVAILABLE"), "Invalid retrieval status")
    require(value["evidence_status"] == "UNKNOWN", "Retrieval cannot promote scientific truth")
    require(type(value["reason"]) is str and len(value["reason"]) <= 2048,
            "Invalid retrieval reason")
    hits = value["hits"]
    require(type(hits) is list and len(hits) <= request["top_k"], "Results exceed top_k")
    if value["status"] == "UNAVAILABLE":
        text(value["reason"], "Unavailable reason", 2048)
        require(not hits, "Unavailable retrieval cannot contain hits")
    seen = set()
    for hit in hits:
        fields(hit, "artifact score relation_status provenance", "retrieval hit")
        identity(hit["artifact"], "artifact")
        key = tuple(hit["artifact"][k] for k in ("id", "version", "sha256"))
        require(key not in seen, "Duplicate retrieval hit")
        seen.add(key)
        number(hit["score"], "retrieval score")
        require(hit["relation_status"] == "PROPOSED", "Similarity is not an isomorphism proof")
        provenance(hit["provenance"])
    costs(value["costs"])
    return value


def unavailable(request, reason="Offline retrieval provider is not implemented"):
    request = validate_request(request)
    return validate_result(request, {
        "schema": RESULT_SCHEMA, "request_sha256": digest(request),
        "corpus": request["corpus"], "model": request["model"], "status": "UNAVAILABLE",
        "reason": reason, "hits": [], "evidence_status": "UNKNOWN",
        "costs": {"wall_seconds": None, "cpu_seconds": None, "status": "UNKNOWN"},
    })
