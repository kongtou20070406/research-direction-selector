"""Lightweight session board for parallel conversations and Lead-managed swarms (0 LLM tokens)."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import posixpath
import re
import sys
import time
import uuid
from pathlib import Path

sys.dont_write_bytecode = True

SCHEMA = "rds-sessions/v1"
DEFAULT_TTL_HOURS = 12.0
ROLES = ("lead", "worker", "reviewer")
READ_ONLY_TOKENS = frozenset({"read-only", "readonly", "ro", "none"})
_GLOB_RE = re.compile(r"[*?[]")


def _utcnow(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(dt.timezone.utc)


def _fmt_utc(ts: dt.datetime) -> str:
    return _utcnow(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)


def resolve_shared_root(project_root: Path) -> Path:
    """Resolve a linked git worktree back to the main checkout root so all worktrees share one board."""
    root = Path(project_root).resolve()
    dot_git = root / ".git"
    if dot_git.is_file():
        try:
            first_line = dot_git.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
        except (OSError, IndexError):
            return root
        if first_line.lower().startswith("gitdir:"):
            gitdir = Path(first_line.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (root / gitdir).resolve()
            commondir_file = gitdir / "commondir"
            if commondir_file.is_file():
                try:
                    rel_common = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
                    common = Path(rel_common)
                    if not common.is_absolute():
                        common = (gitdir / common).resolve()
                    if common.name == ".git":
                        return common.parent
                except OSError:
                    pass
    return root


def find_project_root(cwd: str | Path | None = None) -> Path:
    start = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    for parent in (start, *start.parents):
        if (parent / ".agents" / "brief.json").is_file() or (parent / ".git").exists():
            return parent
    return start


def board_path(project_root: Path) -> Path:
    return resolve_shared_root(project_root) / ".agents" / "sessions.json"


def _norm_scope(raw: str) -> str:
    import posixpath

    s = raw.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    if s in ("", ".", "*"):
        return s
    normed = posixpath.normpath(s.rstrip("/"))
    return "." if normed == "." else normed


def is_readonly(scope: str) -> bool:
    return _norm_scope(scope).lower() in READ_ONLY_TOKENS


def _dir_overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def path_overlaps(a: str, b: str) -> bool:
    if is_readonly(a) or is_readonly(b):
        return False
    na, nb = _norm_scope(a), _norm_scope(b)
    if na in ("", ".", "*") or nb in ("", ".", "*"):
        return True
    has_glob_a = bool(_GLOB_RE.search(na))
    has_glob_b = bool(_GLOB_RE.search(nb))
    if has_glob_a or has_glob_b:
        if os.name == "nt":
            na_cmp, nb_cmp = na.lower(), nb.lower()
        else:
            na_cmp, nb_cmp = na, nb
        if fnmatch.fnmatchcase(na_cmp, nb_cmp) or fnmatch.fnmatchcase(nb_cmp, na_cmp):
            return True
        raw_pa = _GLOB_RE.split(na_cmp, maxsplit=1)[0]
        raw_pb = _GLOB_RE.split(nb_cmp, maxsplit=1)[0]
        pa = raw_pa.rstrip("/")
        pb = raw_pb.rstrip("/")
        if not pa or not pb:
            return True
        if raw_pa.endswith("/") and raw_pb.endswith("/"):
            return _dir_overlaps(pa, pb)
        if raw_pa.endswith("/"):
            return _dir_overlaps(pa, posixpath.dirname(pb) or pb) if not raw_pb.endswith("/") else _dir_overlaps(pa, pb)
        if raw_pb.endswith("/"):
            return _dir_overlaps(posixpath.dirname(pa) or pa, pb)
        dir_a, dir_b = posixpath.dirname(pa), posixpath.dirname(pb)
        if dir_a == dir_b:
            return pa == pb or pa.startswith(pb) or pb.startswith(pa)
        # If wildcard sits inside a directory segment (e.g. pkg-*/src/*.py vs pkg-one/src), compare prefixes safely
        if "*" in na_cmp.split("/")[0] or "?" in na_cmp.split("/")[0] or "[" in na_cmp.split("/")[0]:
            return fnmatch.fnmatchcase(nb_cmp, na_cmp.split("/")[0] + "*") or _dir_overlaps(pa, pb)
        if "*" in nb_cmp.split("/")[0] or "?" in nb_cmp.split("/")[0] or "[" in nb_cmp.split("/")[0]:
            return fnmatch.fnmatchcase(na_cmp, nb_cmp.split("/")[0] + "*") or _dir_overlaps(pa, pb)
        return _dir_overlaps(dir_a or pa, dir_b or pb)
    if os.name == "nt":
        return _dir_overlaps(na.lower(), nb.lower())
    return _dir_overlaps(na, nb)


def _norm_resources(resources: list[str] | None) -> list[str]:
    return [r.strip() for r in (resources or []) if isinstance(r, str) and r.strip()]


def _norm_scopes(scopes: list[str] | None) -> list[str]:
    cleaned = [_norm_scope(s) for s in (scopes or []) if isinstance(s, str) and s.strip()]
    return cleaned or ["read-only"]


def _is_parent_child(a: dict, b: dict) -> bool:
    aid, bid = a.get("id"), b.get("id")
    return bool(aid and b.get("owner") == aid) or bool(bid and a.get("owner") == bid)


def _pair_overlap(
    scopes_a: list[str], res_a: list[str], scopes_b: list[str], res_b: list[str]
) -> tuple[list[str], list[str]]:
    scope_hits: list[str] = []
    for sa in scopes_a:
        for sb in scopes_b:
            if path_overlaps(sa, sb):
                pair = f"{sa} <-> {sb}" if sa != sb else sa
                if pair not in scope_hits:
                    scope_hits.append(pair)
    res_b_lower = {r.lower(): r for r in res_b}
    res_hits = [ra for ra in res_a if ra.lower() in res_b_lower]
    return scope_hits, res_hits


def find_conflicts(
    sessions: list[dict],
    *,
    candidate_id: str | None = None,
    owner: str | None = None,
    scope: list[str] | None = None,
    resources: list[str] | None = None,
) -> list[dict]:
    if candidate_id is None and owner is None and scope is None and resources is None:
        conflicts = []
        for i, a in enumerate(sessions):
            for b in sessions[i + 1 :]:
                if _is_parent_child(a, b):
                    continue
                s_hits, r_hits = _pair_overlap(
                    a.get("scope", []), a.get("resources", []), b.get("scope", []), b.get("resources", [])
                )
                if s_hits or r_hits:
                    conflicts.append({"a": a["id"], "b": b["id"], "scopes": s_hits, "resources": r_hits})
        return conflicts

    cand = {"id": candidate_id, "owner": owner.strip() if isinstance(owner, str) and owner.strip() else None}
    cand_scopes = _norm_scopes(scope)
    cand_res = _norm_resources(resources)
    conflicts = []
    for other in sessions:
        if other.get("id") == candidate_id or _is_parent_child(cand, other):
            continue
        s_hits, r_hits = _pair_overlap(cand_scopes, cand_res, other.get("scope", []), other.get("resources", []))
        if s_hits or r_hits:
            conflicts.append(
                {"with": other["id"], "task": other.get("task", ""), "scopes": s_hits, "resources": r_hits}
            )
    return conflicts


def _read_raw_entries(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    entries = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"].strip()]


def load_active(
    project_root: Path,
    *,
    now: dt.datetime | None = None,
    default_ttl_hours: float = DEFAULT_TTL_HOURS,
) -> list[dict]:
    ref_now = _utcnow(now)
    active = []
    for entry in _read_raw_entries(board_path(project_root)):
        updated = _parse_utc(entry.get("updated_at", ""))
        if updated is None:
            continue
        ttl_raw = entry.get("ttl_hours", default_ttl_hours)
        try:
            ttl_hours = float(ttl_raw)
        except (TypeError, ValueError):
            ttl_hours = default_ttl_hours
        if ttl_hours <= 0:
            continue
        if (ref_now - updated).total_seconds() > ttl_hours * 3600.0:
            continue
        active.append(entry)
    active.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return active


def _board_lock(path: Path, timeout_s: float = 2.0):
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        deadline = time.monotonic() + timeout_s
        fd = None
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                fd = None
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 30.0:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for board lock: {lock}")
                time.sleep(0.02)
        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            lock.unlink(missing_ok=True)

    return _ctx()


def _save_atomic(path: Path, sessions: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema": SCHEMA, "sessions": sessions}, ensure_ascii=False, indent=2) + "\n"
    tmp = path.parent / f".sessions.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def claim(
    project_root: Path,
    session_id: str,
    *,
    task: str,
    scope: list[str] | None = None,
    resources: list[str] | None = None,
    worktree: str | None = None,
    owner: str | None = None,
    role: str | None = None,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    force: bool = False,
    now: dt.datetime | None = None,
) -> tuple[bool, dict | list[dict]]:
    sid = session_id.strip()
    if not sid:
        raise ValueError("session_id must be non-empty")
    if ttl_hours <= 0:
        raise ValueError("ttl_hours must be positive")
    clean_owner = owner.strip() if isinstance(owner, str) and owner.strip() else None
    clean_role = role or ("worker" if clean_owner else "lead")
    if clean_role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    with _board_lock(board_path(project_root)):
        active = load_active(project_root, now=now)
        norm_scopes = _norm_scopes(scope)
        norm_res = _norm_resources(resources)
        if not force:
            conflicts = find_conflicts(active, candidate_id=sid, owner=clean_owner, scope=norm_scopes, resources=norm_res)
            if conflicts:
                return False, conflicts
        ts = _fmt_utc(_utcnow(now))
        existing = next((s for s in active if s["id"] == sid), None)
        entry: dict = {
            "id": sid,
            "role": clean_role,
            "task": task.strip(),
            "scope": norm_scopes,
            "resources": norm_res,
            "started_at": existing.get("started_at", ts) if existing else ts,
            "updated_at": ts,
            "ttl_hours": float(ttl_hours),
        }
        if clean_owner:
            entry["owner"] = clean_owner
        if worktree and worktree.strip():
            entry["worktree"] = Path(worktree.strip()).as_posix()
        updated = [entry, *(s for s in active if s["id"] != sid)]
        _save_atomic(board_path(project_root), updated)
        return True, entry


def heartbeat(
    project_root: Path,
    session_id: str,
    *,
    task: str | None = None,
    scope: list[str] | None = None,
    resources: list[str] | None = None,
    role: str | None = None,
    ttl_hours: float | None = None,
    force: bool = False,
    now: dt.datetime | None = None,
) -> dict | None:
    sid = session_id.strip()
    with _board_lock(board_path(project_root)):
        active = load_active(project_root, now=now)
        existing = next((s for s in active if s["id"] == sid), None)
        if existing is None:
            return None
        entry = dict(existing)
        if task is not None:
            entry["task"] = task.strip()
        if scope is not None:
            entry["scope"] = _norm_scopes(scope)
        if resources is not None:
            entry["resources"] = _norm_resources(resources)
        if role is not None:
            if role not in ROLES:
                raise ValueError(f"role must be one of {', '.join(ROLES)}")
            entry["role"] = role
        if ttl_hours is not None:
            if ttl_hours <= 0:
                raise ValueError("ttl_hours must be positive")
            entry["ttl_hours"] = float(ttl_hours)
        if not force and (scope is not None or resources is not None):
            conflicts = find_conflicts(
                active,
                candidate_id=sid,
                owner=entry.get("owner"),
                scope=entry.get("scope"),
                resources=entry.get("resources"),
            )
            if conflicts:
                raise ValueError(f"heartbeat conflict with active session(s): {', '.join(c['with'] for c in conflicts)}")
        entry["updated_at"] = _fmt_utc(_utcnow(now))
        updated = [entry, *(s for s in active if s["id"] != sid)]
        _save_atomic(board_path(project_root), updated)
        return entry


def release(
    project_root: Path,
    session_id: str,
    *,
    cascade: bool = False,
    now: dt.datetime | None = None,
) -> bool:
    sid = session_id.strip()
    path = board_path(project_root)
    if not path.is_file():
        return False
    with _board_lock(path):
        active = load_active(project_root, now=now)
        to_remove = {sid}
        if cascade:
            changed = True
            while changed:
                changed = False
                for s in active:
                    if s.get("owner") in to_remove and s["id"] not in to_remove:
                        to_remove.add(s["id"])
                        changed = True
        remaining = [s for s in active if s["id"] not in to_remove]
        existed = len(remaining) != len(active)
        if existed:
            _save_atomic(path, remaining)
        return existed


def prune(
    project_root: Path,
    *,
    now: dt.datetime | None = None,
    default_ttl_hours: float = DEFAULT_TTL_HOURS,
) -> int:
    path = board_path(project_root)
    with _board_lock(path):
        raw = _read_raw_entries(path)
        if not raw:
            return 0
        active = load_active(project_root, now=now, default_ttl_hours=default_ttl_hours)
        removed = len(raw) - len(active)
        if removed > 0:
            _save_atomic(path, active)
        return removed


def _format_age(updated_at: str, ref_now: dt.datetime) -> str:
    parsed = _parse_utc(updated_at)
    if parsed is None:
        return "unknown"
    secs = max(0, int((ref_now - parsed).total_seconds()))
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    return f"{mins // 60}h ago"


def _format_entry(s: dict, ref_now: dt.datetime, prefix: str = "- ") -> str:
    role = s.get("role") or ("worker" if s.get("owner") else "lead")
    scopes = ", ".join(s.get("scope") or ["read-only"])
    parts = [f"{s['id']} [{role}]", f"scope: {scopes}"]
    if s.get("resources"):
        parts.append(f"res: {', '.join(s['resources'])}")
    if s.get("worktree"):
        parts.append(f"wt: {Path(s['worktree']).name}")
    parts.append(_format_age(s.get("updated_at", ""), ref_now))
    if s.get("task"):
        parts.append(s["task"])
    return prefix + " | ".join(parts)


def brief_lines(project_root: Path, *, now: dt.datetime | None = None, limit: int = 8) -> list[str]:
    ref_now = _utcnow(now)
    active = load_active(project_root, now=ref_now)
    if not active:
        return []
    active_ids = {s["id"] for s in active}
    children: dict[str, list[dict]] = {}
    top_level: list[dict] = []
    for s in active:
        owner = s.get("owner")
        if owner and owner in active_ids:
            children.setdefault(owner, []).append(s)
        else:
            top_level.append(s)

    lines: list[str] = []
    for lead in top_level:
        if len(lines) >= max(0, limit):
            break
        lines.append(_format_entry(lead, ref_now, "- "))
        for child in children.get(lead["id"], []):
            if len(lines) >= max(0, limit):
                break
            lines.append(_format_entry(child, ref_now, "  ↳ "))
    for conflict in find_conflicts(active)[:3]:
        details = [*conflict["scopes"], *conflict["resources"]]
        lines.append(f"- ⚠ CONFLICT: {conflict['a']} <-> {conflict['b']} ({', '.join(details)})")
    return lines


def _format_conflict(c: dict) -> str:
    details = [*c.get("scopes", []), *c.get("resources", [])]
    peer = c.get("with") or f"{c.get('a')} <-> {c.get('b')}"
    task = f" [{c['task']}]" if c.get("task") else ""
    return f"CONFLICT with {peer}{task}: {', '.join(details)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_project(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", type=Path, default=None, help="project root or subdir")

    p_claim = sub.add_parser("claim", help="register or update a session claim")
    p_claim.add_argument("id")
    p_claim.add_argument("--task", required=True)
    p_claim.add_argument("--scope", nargs="+", default=None)
    p_claim.add_argument("--resource", nargs="+", default=None)
    p_claim.add_argument("--worktree", default=None)
    p_claim.add_argument("--owner", default=None, help="parent Lead session id for swarm workers")
    p_claim.add_argument("--role", choices=ROLES, default=None)
    p_claim.add_argument("--ttl-hours", type=float, default=DEFAULT_TTL_HOURS)
    p_claim.add_argument("--force", action="store_true")
    add_project(p_claim)

    p_hb = sub.add_parser("heartbeat", help="refresh TTL and optionally update fields")
    p_hb.add_argument("id")
    p_hb.add_argument("--task", default=None)
    p_hb.add_argument("--scope", nargs="+", default=None)
    p_hb.add_argument("--resource", nargs="+", default=None)
    p_hb.add_argument("--role", choices=ROLES, default=None)
    p_hb.add_argument("--ttl-hours", type=float, default=None)
    add_project(p_hb)

    p_rel = sub.add_parser("release", help="remove a session claim")
    p_rel.add_argument("id")
    p_rel.add_argument("--cascade", action="store_true", help="also release workers owned by this session")
    add_project(p_rel)

    p_prune = sub.add_parser("prune", help="drop expired session claims")
    p_prune.add_argument("--ttl-hours", type=float, default=DEFAULT_TTL_HOURS)
    add_project(p_prune)

    p_list = sub.add_parser("list", help="list active sessions and conflicts")
    p_list.add_argument("--json", action="store_true")
    add_project(p_list)

    p_check = sub.add_parser("check", help="check candidate scopes/resources against active sessions")
    p_check.add_argument("--id", default=None)
    p_check.add_argument("--owner", default=None)
    p_check.add_argument("--scope", nargs="+", default=None)
    p_check.add_argument("--resource", nargs="+", default=None)
    add_project(p_check)

    args = parser.parse_args(argv)
    root = find_project_root(args.project)

    if args.cmd == "claim":
        ok, result = claim(
            root,
            args.id,
            task=args.task,
            scope=args.scope,
            resources=args.resource,
            worktree=args.worktree,
            owner=args.owner,
            role=args.role,
            ttl_hours=args.ttl_hours,
            force=args.force,
        )
        if not ok:
            for item in result:
                print(_format_conflict(item))
            return 2
        print(f"CLAIMED {result['id']} -> {board_path(root)}")
        return 0

    if args.cmd == "heartbeat":
        try:
            updated = heartbeat(
                root,
                args.id,
                task=args.task,
                scope=args.scope,
                resources=args.resource,
                role=args.role,
                ttl_hours=args.ttl_hours,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if updated is None:
            print(f"NOT FOUND: {args.id}")
            return 1
        print(f"HEARTBEAT {updated['id']}")
        return 0

    if args.cmd == "release":
        before = len(load_active(root))
        existed = release(root, args.id, cascade=args.cascade)
        if not existed:
            print(f"ABSENT {args.id}")
        else:
            removed = before - len(load_active(root))
            extra = f" (+{removed - 1} workers)" if args.cascade and removed > 1 else ""
            print(f"RELEASED {args.id}{extra}")
        return 0

    if args.cmd == "prune":
        removed = prune(root, default_ttl_hours=args.ttl_hours)
        print(f"PRUNED {removed}")
        return 0

    if args.cmd == "list":
        active = load_active(root)
        if args.json:
            print(json.dumps({"schema": SCHEMA, "sessions": active, "conflicts": find_conflicts(active)}, ensure_ascii=False, indent=2))
            return 0
        lines = brief_lines(root, limit=50)
        print("\n".join(lines) if lines else "(no active sessions)")
        return 0

    if args.cmd == "check":
        active = load_active(root)
        conflicts = find_conflicts(
            active, candidate_id=args.id, owner=args.owner, scope=args.scope, resources=args.resource
        )
        if conflicts:
            for item in conflicts:
                print(_format_conflict(item))
            return 1
        print("OK (no conflicts)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
