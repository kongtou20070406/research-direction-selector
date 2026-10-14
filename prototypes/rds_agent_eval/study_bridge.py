"""Read-only owned-study projection, preserving native authority and negatives."""
import hashlib
from pathlib import Path

from prototypes.memento_rds.observations import read_observations
from prototypes.rds_agent_eval.advisor_view import read_view
from prototypes.rds_agent_eval.plant_verifier import verify


def inspect(store, prepared, *, expected_snapshot_sha256, outputs, source_path, claim):
    """Bind supplied original outputs to the initialized preflight contract.

    This inspects an existing owned project. It neither creates advice nor
    dispatches, repairs, persists assessments or certifies free-text answers.
    """
    from rds_project import digest, require

    require(type(outputs) is list and 1 <= len(outputs) <= 128 and all(
        type(p) is dict and set(p) == {"run_id", "receipt_sha256", "path"} for p in outputs),
        "Require explicit original receipt/output pins")
    with store._db(readonly=True) as db:
        db.execute("BEGIN")
        contract = store._contract(db)
    require(digest(contract) == digest(prepared["contract"]), "Native contract differs from preflight")
    source_file = store._path(source_path).resolve()
    bound = [b for b in contract["bindings"] if b["role"] == "data"
             and store._path(b["path"]).resolve() == source_file]
    require(len(bound) == 1 and source_path in prepared["agent_inputs"]
            and bound[0]["sha256"] == prepared["agent_inputs"][source_path],
            "Source is not the exact frozen agent data binding")
    with source_file.open("rb") as stream:
        source = stream.read(2097153)
    require(len(source) <= 2097152 and hashlib.sha256(source).hexdigest() == bound[0]["sha256"],
            "Frozen original data bytes changed")
    view = read_view(store, expected_snapshot_sha256=expected_snapshot_sha256)
    pins = [{k: p[k] for k in ("run_id", "receipt_sha256")} for p in outputs]
    originals = read_observations(store, pins)
    routes = {r["manifest"]["id"]: r["manifest"] for r in contract["advisor_policy"]["routes"]}
    projected = []
    for requested, observation in zip(outputs, originals["observations"]):
        receipt = observation["receipt"]
        manifest = routes.get(receipt["run_id"])
        require(manifest is not None and receipt["manifest_sha256"] == digest(manifest)
                and receipt["argv"] == manifest["argv"], "Original receipt route differs from frozen study")
        output = store._path(requested["path"], True, contract).resolve()
        require(output in {store._path(p, True, contract).resolve() for p in manifest["outpaths"]},
                "Output does not belong to the pinned native route")
        result = {"run_id": receipt["run_id"], "receipt_sha256": receipt["sha256"],
                  "path": requested["path"], "run_status": receipt["run_status"],
                  "task_correctness": "UNKNOWN", "verification": None}
        if receipt["run_status"] == "SUCCEEDED":
            matched = [a for a in observation["artifacts"] if a["kind"] == "project_output"
                       and (Path(store.root) / a["path"]).resolve() == output]
            require(len(matched) == 1, "Original measured output is missing or ambiguous")
            value = verify(source, matched[0]["bytes"], source_sha256=bound[0]["sha256"],
                           measured_sha256=matched[0]["sha256"], claim=claim)
            result.update(task_correctness=value["task_correctness"], verification=value)
        projected.append(result)
    # The native reader rechecks current input, receipt, CAS and TMS identities.
    require(digest(read_view(store, expected_snapshot_sha256=expected_snapshot_sha256)) == digest(view),
            "Native study view changed during projection")
    return {"schema": "rds-native-study-projection-v1", "advisor": view,
            "original_observations": originals, "domain_outputs": projected,
            "receipt_assurance": "NATIVE_DECODED_BYTES_BOUND", "scope": "SUPPLIED_PINS_AND_STRUCTURED_CLAIM",
            "execution_effect": "NONE", "free_text_review": "UNKNOWN",
            "whole_rds_efficacy": "UNMEASURED", "scientific_support": "UNKNOWN"}
