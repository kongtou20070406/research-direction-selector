"""Host command hook: bind research dispatch to a ledger-issued admission identity.

This module is the second enforcement layer from docs/5.8-vision.md lines 60-62.
The native controller (rds_project.py) owns admitted processes and budget
reservations; this hook lets a supported host (the Windows Task Scheduler
registration used by `project execute --background`) verify a **bound
request/admission identity** before a worker claims its attempt. It never
matches the command string: a forged request with the right argv but no live
ledger admission is refused.

Installed state lives in ``<root>/.rds/host-guard.json``. An installed hook is
advisory coverage for one host mechanism; it is not an OS sandbox and does not
claim protection against direct shell execution. Where no hook is installed,
`coverage` reports ``HOST_GUARD_MISSING`` and claims nothing.
"""
import json
from pathlib import Path
import sys

from rds_project import ProjectStore, canonical, digest, file_sha
from rds_mutation import mutation

SCHEMA = 1
ASSURANCE = "BOUND_ADMISSION_IDENTITY_CHECK_NOT_OS_SANDBOX"
GUARD_NAME = "host-guard.json"
SUPPORTED_HOSTS = {"windows-task-scheduler": {
    "controller": "scripts/rds_project.py::ProjectStore._schedule",
    "worker": "scripts/rds_project_worker.py::main",
    "identity_fields": ["run_id", "attempt_id"],
}}


def _guard_path(root):
    return Path(root) / ".rds" / GUARD_NAME


@mutation()
def install(root, strict=True):
    """Record the supported-host hook next to an existing project ledger."""
    from rds_workspace import check_admission
    check_admission(root)
    store = ProjectStore(root)
    if not store.path.is_file():
        raise ValueError("Install the host hook after the project ledger exists")
    guard = {"schema": SCHEMA, "assurance": ASSURANCE, "strict": bool(strict),
             "hosts": {name: {"identity_fields": list(meta["identity_fields"]),
                              "controller": meta["controller"], "worker": meta["worker"]}
                       for name, meta in SUPPORTED_HOSTS.items()},
             "installed_at_digest": None}
    guard["installed_at_digest"] = digest({"guard": guard, "ledger": file_sha(store.path)})
    path = _guard_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(guard), encoding="utf-8")
    return guard


def _load_guard(root):
    path = _guard_path(root)
    if not path.is_file():
        return None
    guard = json.loads(path.read_text(encoding="utf-8"))
    if guard.get("schema") != SCHEMA or guard.get("assurance") != ASSURANCE:
        raise ValueError("Unrecognized host guard record")
    return guard


def coverage(root):
    """Report what the installed hook covers and explicitly what it does not."""
    guard = _load_guard(root)
    if guard is None:
        return {"schema": SCHEMA, "status": "HOST_GUARD_MISSING", "assurance": ASSURANCE,
                "hosts": {}, "covered": [], "uncovered": [
                    "direct shell execution", "other host mechanisms",
                    "processes started outside this project root"],
                "claims_protection_against_direct_shell": False}
    strict = bool(guard.get("strict"))
    hosts = sorted(guard.get("hosts", {}))
    return {"schema": SCHEMA, "status": "INSTALLED", "assurance": ASSURANCE, "strict": strict,
            "hosts": hosts, "covered": [f"{host}: worker admission revalidated against the "
                                        f"ledger before any attempt is claimed" for host in hosts],
            "uncovered": ["direct shell execution", "other host mechanisms",
                          "processes started outside this project root"],
            "claims_protection_against_direct_shell": False}


def validate_request(root, request):
    """Check a dispatch request against the live ledger admission identity.

    The request must name the run, carry the ledger-issued attempt id, and match
    the frozen executor digest and argv of that run's manifest. Any mismatch,
    unknown run or terminal status refuses the request before dispatch.
    """
    for field in ("host", "run_id", "attempt_id"):
        if not isinstance(request.get(field), str) or not request[field]:
            _refuse(f"Request must declare nonempty host, run_id and attempt_id: missing {field}")
    host = request["host"]
    guard = _load_guard(root)
    if guard is None:
        _refuse("HOST_GUARD_MISSING")
    if host not in guard.get("hosts", {}):
        _refuse(f"Host {host!r} is not covered by the installed hook")
    store = ProjectStore(root)
    if not store.path.is_file():
        _refuse("Project ledger is missing")
    with store._db(True) as db:
        try:
            run = store._run(db, request["run_id"])
        except ValueError as exc:
            _refuse(f"Unknown admission identity: {exc}")
        if run["attempt_id"] != request["attempt_id"]:
            _refuse("Attempt id does not match the ledger-issued admission")
        if run["status"] not in ("RESERVED", "RUNNING"):
            _refuse(f"Admission is terminal (status {run['status']}); reruns are refused")
        expected_argv = run["manifest"]["argv"]
    if request.get("argv") != expected_argv:
        _refuse("Request argv does not match the admitted manifest")
    if not isinstance(request.get("executor_sha256"), str) or \
            request["executor_sha256"].lower() != run["executor_sha256"]:
        _refuse("Executor digest does not match the admitted run")
    return {"schema": SCHEMA, "status": "ADMITTED", "assurance": ASSURANCE,
            "host": host, "run_id": run["id"], "attempt_id": run["attempt_id"],
            "bound_manifest_sha256": digest(run["manifest"])}


def _refuse(message):
    raise ValueError(f"Host hook refusal: {message}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "coverage", "validate"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--request", help="JSON file with the dispatch request to validate")
    parser.add_argument("--permissive", action="store_true",
                        help="Record advisory (non-strict) coverage")
    args = parser.parse_args()
    if args.action == "install":
        result = install(args.root, strict=not args.permissive)
    elif args.action == "coverage":
        result = coverage(args.root)
    else:
        if not args.request:
            parser.error("validate requires --request")
        result = validate_request(args.root, json.loads(Path(args.request).read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result.get("status") == "HOST_GUARD_MISSING" else 0


if __name__ == "__main__":
    sys.exit(main())
