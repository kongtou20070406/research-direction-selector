"""Receipt-bound model feedback; full native originals remain separately available."""
import json

from prototypes.memento_rds.observations import read_observations
from prototypes.rds_agent_eval.advisor_view import read_view


def read_feedback(store, *, expected_snapshot_sha256, outputs):
    """Read supplied owned-route outputs, including failed/unknown observations.

    Model feedback is a projection, not admission or verification. The returned
    originals include every supplied receipt/artifact and the complete CAS report.
    No collection, dispatch, assessment write, context truncation or retry occurs.
    """
    from rds_project import digest, require

    require(type(outputs) is list and 1 <= len(outputs) <= 128 and all(
        type(p) is dict and set(p) == {"run_id", "receipt_sha256", "path"} for p in outputs),
        "Require explicit original receipt/output pins")
    with store._db(readonly=True) as db:
        db.execute("BEGIN")
        contract = store._contract(db)
    view = read_view(store, expected_snapshot_sha256=expected_snapshot_sha256)
    pins = [{k: p[k] for k in ("run_id", "receipt_sha256")} for p in outputs]
    originals = read_observations(store, pins)
    routes = {r["manifest"]["id"]: r["manifest"] for r in contract["advisor_policy"]["routes"]}
    entries = []
    for requested, observation in zip(outputs, originals["observations"]):
        receipt = observation["receipt"]
        manifest = routes.get(receipt["run_id"])
        require(manifest is not None and digest(manifest) == receipt["manifest_sha256"]
                and manifest["argv"] == receipt["argv"], "Original receipt differs from native route")
        output = store._path(requested["path"], True, contract).resolve()
        require(output in {store._path(p, True, contract).resolve() for p in manifest["outpaths"]},
                "Output does not belong to original route")
        entry = {k: receipt[k] for k in (
            "run_id", "attempt_id", "run_status", "process_status", "sha256", "manifest_sha256",
            "effective_contract_sha256", "process_started", "exit_code", "timeout",
            "resources", "errors", "assessment")}
        entry["artifacts"] = [{k: v for k, v in a.items() if k != "bytes"}
                              for a in observation["artifacts"]]
        entry["measurement"] = None
        if receipt["run_status"] == "SUCCEEDED":
            matched = [a for a in observation["artifacts"] if a["kind"] == "project_output"
                       and store._path(a["path"], True, contract).resolve() == output]
            require(len(matched) == 1, "Original measured output is missing or ambiguous")
            try:
                value = json.loads(matched[0]["bytes"])
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Original output is not JSON; no repair", observation) from exc
            entry["measurement"] = {"artifact": {k: v for k, v in matched[0].items() if k != "bytes"},
                                    "value": value, "verification": "UNKNOWN"}
        entries.append(entry)
    require(digest(read_view(store, expected_snapshot_sha256=expected_snapshot_sha256)) == digest(view),
            "Native feedback view changed during projection")
    feedback = {"schema": "rds-model-feedback-v1", "observations": entries,
                "advisor": {"snapshot_sha256": view["snapshot_sha256"],
                            "dialogue": view["native_plan"]["dialogue"],
                            "selected_manifest": view["native_report"].get("selected_manifest")
                            if view["native_report"] is not None else None},
                "scope": "SUPPLIED_PINS_ONLY", "execution_effect": "NONE",
                "scientific_support": "UNKNOWN", "free_text_review": "UNKNOWN"}
    return {"schema": "rds-feedback-view-v1", "model_feedback": feedback,
            "original_observations": originals, "original_advisor": view}
