"""Pure decoder for retained project run records."""
from __future__ import annotations

import json
import math


def decode_run_row(row, RunIntegrityError, canonical, digest, TERMINAL):
    """Validate retained writer values before displaying, dispatching or settling them.

    No schema migration, content repair or new evidence claim. Optional later
    aliases/policy fields are not required, and hot-loop reads remain keyed by id.
    """
    prefix = f"Project run integrity failure: run {row['id']!r} body "
    suffix = "; inspect retained state"

    def reject(reason):
        raise RunIntegrityError(prefix + reason + suffix)

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                reject("repeats a JSON key")
            result[key] = value
        return result

    try:
        run = json.loads(row["body"], object_pairs_hook=unique)
    except RunIntegrityError:
        raise
    except (ValueError, TypeError, RecursionError):
        reject("is not valid JSON")
    if not isinstance(run, dict):
        reject("is not an object")
    if run.get("id") != row["id"]:
        reject("names a different run")
    if not isinstance(run.get("status"), str) or run["status"] not in {"RESERVED", "RUNNING", *TERMINAL} or run["status"] != row["status"]:
        reject("does not match its recorded status")
    if not isinstance(run.get("manifest"), dict) or run["manifest"].get("id") != row["id"]:
        reject("has no matching manifest")
    try:
        canonical(run).encode("utf-8")
        manifest_sha = digest(run["manifest"])
    except (ValueError, RecursionError):
        reject("has no canonical encoding")
    if manifest_sha != run.get("manifest_sha256"):
        reject("has a Manifest integrity failure")
    required = {"resource_estimates", "protocol", "executor", "executor_sha256", "attempt_id",
                "worker_pid", "pid", "started_at", "finished_at", "observed_wall_seconds", "scheduler"}
    if not required <= run.keys():
        reject("is missing execution state")
    if (not isinstance(run["resource_estimates"], dict)
            or not isinstance(run["protocol"], dict)
            or not isinstance(run["executor"], str) or not run["executor"]
            or not isinstance(run["executor_sha256"], str)
            or (run["attempt_id"] is not None and not isinstance(run["attempt_id"], str))
            or (run["scheduler"] is not None and not isinstance(run["scheduler"], dict))):
        reject("has invalid execution state")
    for value in [run["observed_wall_seconds"], *run["resource_estimates"].values()]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            reject("has invalid recorded costs")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            reject("has invalid recorded costs")
    for key in ("worker_pid", "pid"):
        if run[key] is not None and (type(run[key]) is not int or run[key] <= 0):
            reject("has an invalid process identity")
    return run
