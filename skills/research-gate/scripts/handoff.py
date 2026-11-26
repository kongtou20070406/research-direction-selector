"""Plan, freeze, pack and verify a research handoff or archive (0 LLM tokens).

Adapted from math-research-solve (MRS) planned export: a handoff is declared in a plan,
the plan is frozen with byte hashes, packing consumes that exact plan, and the recipient
verifies the bundle. Verification is a readback, not a replay of the research.

    handoff.py check  PLAN.json [--previous OLD.frozen.json]   schema and evidence rules
    handoff.py freeze PLAN.json --out PLAN.frozen.json          add sha256 of every file (create-only)
    handoff.py pack   PLAN.frozen.json --expect-plan SHA --out BUNDLE.zip   create-only, verified
    handoff.py verify BUNDLE.zip                                 recipient readback

Plan fields: ``schema`` (handoff-plan/v1), ``project``, ``purpose`` (handoff | archive),
``scope`` (complete | partial, with ``partial_reason``), ``objective``, ``root`` (base of the
relative file paths; default: the plan's folder), ``files`` {relpath: {role, sha256?}},
``claims`` [{id, statement, status, evidence, refuted_by?}], ``failed_approaches``
[{id, what, why, evidence?}], ``running_jobs`` [{name, where, status_file, stop_condition}],
``next_actions``, ``contributors``, ``previous_plan_sha256``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

SCHEMA = "handoff-plan/v1"
ROLES = ("code", "configuration", "input", "result", "ledger", "document", "checkpoint", "optional")
EVIDENCE_ROLES = ("result", "ledger", "checkpoint", "input", "document")
CLAIM_STATUSES = ("proposed", "executed", "verified", "refuted", "needs_review")
_BACKED = ("executed", "verified")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _root(plan: dict, plan_path: Path) -> Path:
    root = Path(plan.get("root") or ".")
    return root if root.is_absolute() else (plan_path.parent / root)


def check_plan(plan: dict, plan_path: Path, previous: dict | None = None, require_hashes: bool = False) -> list[str]:
    errors: list[str] = []
    if plan.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}")
    if plan.get("purpose") not in ("handoff", "archive"):
        errors.append("purpose must be 'handoff' or 'archive'")
    if plan.get("scope") not in ("complete", "partial"):
        errors.append("scope must be 'complete' or 'partial'")
    if plan.get("scope") == "partial" and not str(plan.get("partial_reason", "")).strip():
        errors.append("a partial handoff needs 'partial_reason'")
    if not str(plan.get("objective", "")).strip():
        errors.append("missing 'objective' (the claim or weak proposition the work serves)")

    root = _root(plan, plan_path)
    files = plan.get("files") or {}
    if not files:
        errors.append("'files' is empty")
    for rel, meta in files.items():
        pp = PurePosixPath(rel)
        if pp.is_absolute() or ":" in rel or ".." in pp.parts or "\\" in rel:
            errors.append(f"file {rel!r}: use a relative POSIX path inside root")
            continue
        if (meta or {}).get("role") not in ROLES:
            errors.append(f"file {rel}: role must be one of {list(ROLES)}")
        p = root / rel
        if not p.is_file():
            errors.append(f"file {rel}: missing under {root}")
        elif meta.get("sha256") and sha256_file(p) != meta["sha256"]:
            errors.append(f"file {rel}: bytes changed since the plan was frozen")
        elif require_hashes and not meta.get("sha256"):
            errors.append(f"file {rel}: no sha256 (run freeze)")

    ids: set[str] = set()
    for c in plan.get("claims", []):
        cid = c.get("id", "?")
        if cid in ids:
            errors.append(f"claim {cid}: duplicate id")
        ids.add(cid)
        status = c.get("status")
        if status not in CLAIM_STATUSES:
            errors.append(f"claim {cid}: status must be one of {list(CLAIM_STATUSES)}")
        ev = c.get("evidence") or []
        for e in ev + (c.get("refuted_by") or []):
            if e not in files:
                errors.append(f"claim {cid}: evidence {e!r} is not in 'files'")
        if status in _BACKED and not any((files.get(e) or {}).get("role") in EVIDENCE_ROLES for e in ev):
            errors.append(f"claim {cid}: '{status}' needs evidence with role result, ledger, checkpoint, input or document")
        if status == "refuted" and not c.get("refuted_by"):
            errors.append(f"claim {cid}: refuted claims keep their record and name 'refuted_by'")
    for f in plan.get("failed_approaches", []):
        if not str(f.get("why", "")).strip():
            errors.append(f"failed approach {f.get('id', '?')}: say why it failed")
        for e in f.get("evidence") or []:
            if e not in files:
                errors.append(f"failed approach {f.get('id', '?')}: evidence {e!r} is not in 'files'")
    for j in plan.get("running_jobs", []):
        if not str(j.get("stop_condition", "")).strip() or not str(j.get("status_file", "")).strip():
            errors.append(f"running job {j.get('name', '?')}: needs 'status_file' and 'stop_condition'")
    if plan.get("scope") == "complete" and plan.get("running_jobs"):
        errors.append("a complete handoff cannot leave jobs running; finish them or make the scope partial")

    if previous is not None:
        if plan.get("previous_plan_sha256") != plan_digest(previous):
            errors.append("'previous_plan_sha256' does not match the previous frozen plan")
        for rel, meta in (previous.get("files") or {}).items():
            if meta.get("role") == "result" and rel in files:
                new = (files[rel] or {}).get("sha256") or (sha256_file(root / rel) if (root / rel).is_file() else None)
                if new and new != meta.get("sha256"):
                    errors.append(f"result {rel}: bytes differ from the previous plan; add a correction file instead of editing results")
            if meta.get("role") == "result" and rel not in files and plan.get("scope") == "complete":
                errors.append(f"result {rel}: dropped from a complete handoff")
        prev_claims = {c.get("id"): c for c in previous.get("claims", [])}
        for cid in prev_claims.keys() - ids:
            errors.append(f"claim {cid}: present in the previous plan but missing; keep it with a status")
    return errors


def freeze(plan_path: Path, out: Path) -> str:
    if out.exists():
        raise FileExistsError(f"{out} exists; frozen plans are create-only")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    root = _root(plan, plan_path)
    for rel, meta in plan.get("files", {}).items():
        meta["sha256"] = sha256_file(root / rel)
    plan["frozen_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    plan["root"] = str(root.resolve())
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return plan_digest(plan)


def pack(frozen_path: Path, expect_plan: str, out: Path) -> dict:
    if out.exists():
        raise FileExistsError(f"{out} exists; bundles are create-only")
    plan = json.loads(frozen_path.read_text(encoding="utf-8"))
    digest = plan_digest(plan)
    if digest != expect_plan:
        raise ValueError(f"plan digest {digest} != --expect-plan {expect_plan}")
    errors = check_plan(plan, frozen_path, require_hashes=True)
    if errors:
        raise ValueError("plan check failed:\n  " + "\n  ".join(errors))
    root = Path(plan["root"])
    receipt = {"plan_sha256": digest, "packed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
               "members": {rel: meta["sha256"] for rel, meta in plan["files"].items()}}
    tmp = out.with_suffix(out.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("PLAN.json", json.dumps(plan, indent=2, ensure_ascii=False))
        zf.writestr("RECEIPT.json", json.dumps(receipt, indent=2))
        for rel in plan["files"]:
            zf.write(root / rel, f"files/{rel}")
    result = verify(tmp)
    if not result["ok"]:
        tmp.unlink()
        raise ValueError(f"bundle failed its own readback: {result}")
    tmp.replace(out)
    return result


def verify(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as zf:
        plan = json.loads(zf.read("PLAN.json"))
        receipt = json.loads(zf.read("RECEIPT.json"))
        names = set(zf.namelist())
        missing, mismatched = [], []
        for rel, meta in plan["files"].items():
            name = f"files/{rel}"
            if name not in names:
                missing.append(rel)
            elif hashlib.sha256(zf.read(name)).hexdigest() != meta["sha256"]:
                mismatched.append(rel)
    plan_ok = receipt.get("plan_sha256") == plan_digest(plan)
    return {"ok": plan_ok and not missing and not mismatched, "plan_matches_receipt": plan_ok,
            "members": len(plan["files"]), "missing": missing, "mismatched": mismatched,
            "scope": plan.get("scope"), "replay_performed": False}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("plan", type=Path)
    c.add_argument("--previous", type=Path)
    f = sub.add_parser("freeze")
    f.add_argument("plan", type=Path)
    f.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("pack")
    p.add_argument("plan", type=Path)
    p.add_argument("--expect-plan", required=True)
    p.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("bundle", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "check":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        prev = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None
        errors = check_plan(plan, args.plan, prev)
        for e in errors:
            print(f"  - {e}")
        print("HANDOFF PLAN " + ("FAILED" if errors else "OK"))
        return 1 if errors else 0
    if args.cmd == "freeze":
        print(f"PLAN_SHA256 {freeze(args.plan, args.out)}")
        return 0
    if args.cmd == "pack":
        print(json.dumps(pack(args.plan, args.expect_plan, args.out), indent=2))
        return 0
    result = verify(args.bundle)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
