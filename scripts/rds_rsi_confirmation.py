"""Track exact replay-input exposure; never attest independent test sealing."""
import json
from pathlib import Path
import re

from rds_core import json_digest as digest, require
from rds_workspace import atomic_bytes, graph_lock, load_object

MAX_EVALUATIONS = 128
MAX_INPUTS = 8192


def input_fingerprint(case):
    """Case names, partitions and expected grades cannot refresh the same input."""
    return digest({"engine": case.get("engine", "search"), "context": case["context"],
                   "templates": case.get("templates", [])})


def record_exposure(cases, bindings, campaign, directory):
    """Reserve before grading, including failures; frozen-pair audits may replay.

    Ordinary regressions also expose their inputs when a ledger is supplied.
    This transaction ledger is not conversational memory or an OS sandbox.
    """
    if campaign is not None:
        require(isinstance(campaign, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", campaign),
                "confirmation_campaign must be a nonempty ASCII identifier of at most 64 characters")
        require(directory is not None, "Confirmation requires a persistent RSI exposure ledger")
    if directory is None:
        return None
    fingerprints = [input_fingerprint(case) for case in cases]
    heldout = [fp for case, fp in zip(cases, fingerprints) if case["partition"] == "heldout"]
    development = {fp for case, fp in zip(cases, fingerprints) if case["partition"] == "development"}
    if campaign is not None:
        require(len(set(heldout)) == len(heldout), "Duplicate heldout input payloads are not fresh cases")
        require(not development.intersection(heldout), "Heldout inputs overlap proposal-development inputs")
    identity = {"bindings": bindings, "campaign": campaign}
    claim = digest(identity)
    directory = Path(directory) / "confirmation"
    ledger_path = directory / "exposures.json"
    # ponytail: one lock per project; split only if measured replay contention warrants it.
    with graph_lock(directory):
        if ledger_path.exists():
            ledger = load_object(ledger_path, "RSI exposure ledger")
            signature = ledger.pop("ledger_sha256", None)
            require(signature == digest(ledger) and ledger.get("schema") == 1,
                    "RSI exposure ledger integrity mismatch")
            require(set(ledger) == {"schema", "inputs", "evaluations"}
                    and isinstance(ledger["inputs"], dict) and isinstance(ledger["evaluations"], dict),
                    "Malformed RSI exposure ledger")
            require(all(isinstance(fp, str) and len(fp) == 64 and isinstance(owner, str)
                        and owner in ledger["evaluations"] for fp, owner in ledger["inputs"].items()),
                    "Malformed RSI exposure claims")
            require(all(isinstance(identity, dict) and digest(identity) == key
                        for key, identity in ledger["evaluations"].items()),
                    "RSI exposure evaluation binding mismatch")
        else:
            ledger = {"schema": 1, "inputs": {}, "evaluations": {}}
        require(len(ledger["evaluations"]) <= MAX_EVALUATIONS and len(ledger["inputs"]) <= MAX_INPUTS,
                "RSI exposure ledger exceeds its declared bounds")
        if campaign is not None:
            reused = [fp for fp in heldout if fp in ledger["inputs"] and ledger["inputs"][fp] != claim]
            require(not reused, "Heldout inputs were exposed to another evaluation; use genuinely new confirmation inputs")
        existing = ledger["evaluations"].get(claim)
        require(existing is None or existing == identity, "RSI exposure claim binding mismatch")
        if existing is None:
            require(len(ledger["evaluations"]) < MAX_EVALUATIONS, "RSI evaluation count bound reached")
            require(len(set(ledger["inputs"]).union(fingerprints)) <= MAX_INPUTS, "RSI input count bound reached")
            ledger["evaluations"][claim] = identity
            for fp in fingerprints:
                ledger["inputs"].setdefault(fp, claim)
            ledger["ledger_sha256"] = digest(ledger)
            atomic_bytes(ledger_path, json.dumps(ledger, sort_keys=True, allow_nan=False,
                                                ensure_ascii=False, indent=2).encode("utf-8"))
    if campaign is None:
        return None
    return {"status": "INPUTS_RESERVED_FOR_FROZEN_EVALUATION", "campaign": campaign,
            "claim_sha256": claim, "heldout_input_sha256": sorted(heldout),
            "independent_sealing": "NOT_ATTESTED", "scope": "exact input payloads recorded in this project"}
