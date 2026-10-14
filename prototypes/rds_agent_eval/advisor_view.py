"""Isolated, read-only expansion of native retained Advisor advice.

Caller supplies ProjectStore and its plan snapshot pin. No new collection,
selection, persistence, execution route or model call is introduced here.
"""


def read_view(store, *, expected_snapshot_sha256):
    """Keep native missing/stale diagnostics; expand only compatible CAS advice.

    A None pin explicitly covers an uninitialized native plan. It never supplies
    a missing goal/budget or makes a new project ready. This view is not admission.
    """
    from rds_dialogue import _report
    from rds_project import digest
    from rds_steering import plan

    result = plan(store, dialogue=True)
    if result.get("snapshot_sha256") != expected_snapshot_sha256:
        raise ValueError("Native plan snapshot pin differs; retain this diagnostic", result)
    dialogue = result["dialogue"]
    original = None
    if dialogue["status"] == "COMPATIBLE_RECORDED_ADVICE":
        original = _report(store, dialogue["source_report"])
        # The native view rechecks report/event/TMS/receipt/input identities.
        # Retain the first coherent read instead of accepting a mixed expansion.
        if digest(plan(store, dialogue=True)) != digest(result):
            raise ValueError("Native advice changed during expansion; retain this diagnostic", result)
    return {"schema": "rds-agent-advisor-view-v1", "status": dialogue["status"],
            "native_plan": result, "native_report": original,
            "snapshot_sha256": result.get("snapshot_sha256"),
            "execution_started": False, "execution_authorized": False,
            "admission": "RECHECK_REQUIRED_BEFORE_DISPATCH", "selection_effect": "NONE",
            "scientific_support": "UNKNOWN", "whole_rds_efficacy": "UNMEASURED"}
