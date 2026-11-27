"""Load optional user/workspace profile JSON from --profile or RDS_PROFILE."""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_CLI_PROFILE: str | None = None


def set_cli_profile(path: str | Path | None) -> None:
    global _CLI_PROFILE
    _CLI_PROFILE = str(path).strip() if path else None
    _load_cached.cache_clear()


def resolve_profile_path() -> Path | None:
    raw = _CLI_PROFILE or os.environ.get("RDS_PROFILE", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_file() else None


@lru_cache(maxsize=16)
def _load_cached(resolved: str, mtime_ns: int) -> dict[str, Any]:
    if not resolved:
        return {}
    try:
        data = json.loads(Path(resolved).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_profile() -> dict[str, Any]:
    p = resolve_profile_path()
    if p is None:
        return {}
    try:
        rp = p.resolve()
        return _load_cached(str(rp), rp.stat().st_mtime_ns)
    except OSError:
        return {}


def profile_id() -> str:
    p = resolve_profile_path()
    if p is None:
        return "public"
    sha = hashlib.sha256(str(p.resolve()).encode("utf-8")).hexdigest()[:8]
    prof = load_profile()
    pid = prof.get("id")
    if isinstance(pid, str) and pid.strip():
        slug = "".join(c if (c.isalnum() or c in "-_") else "_" for c in pid.strip())[:16]
        return f"{slug}_{sha}"
    return sha


def profile_str(key: str, default: str) -> str:
    val = load_profile().get(key)
    return val if isinstance(val, str) and val != "" else default


def profile_int(key: str, default: int) -> int:
    val = load_profile().get(key)
    return int(val) if isinstance(val, int) and not isinstance(val, bool) else default


def profile_bool(key: str, default: bool) -> bool:
    val = load_profile().get(key)
    return val if isinstance(val, bool) else default


def profile_list(key: str, default: list[str]) -> list[str]:
    val = load_profile().get(key)
    if isinstance(val, list) and all(isinstance(x, str) for x in val):
        return list(val)
    return list(default)
