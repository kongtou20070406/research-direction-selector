"""L4 composition root wiring ProjectStore, read services, lifecycle hooks and worker entrypoints.

All dependencies point strictly downward (L4 -> L3/L2/L1/L0/SHARED). Existing
module entrypoints remain thin compatibility forwarders.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rds_project import ProjectStore
from rds_project_hooks import _modules, provider as lifecycle_provider, register as register_hook
from rds_project_read_services import ProjectReadServices
from rds_project_values import require

__all__ = [
    "RDSApp",
    "bootstrap_hooks",
    "create_app",
    "lifecycle_provider",
    "open_store",
    "read_services",
]


def bootstrap_hooks(*names: str):
    """Explicitly bind named lifecycle providers (or all known providers if omitted)."""
    selected = names or tuple(sorted(_modules))
    bound = {}
    for name in selected:
        require(name in _modules, f"Unknown project hook provider: {name}")
        if name == "advisor":
            import rds_owned_advisor as mod
        elif name == "quick":
            import rds_quick_ledger as mod
        else:
            raise ValueError(f"Unknown project hook provider: {name}")
        register_hook(name, mod)
        bound[name] = lifecycle_provider(name)
    return bound


def open_store(root, *, hooks=()):
    """Construct ProjectStore at root and optionally pre-bind lifecycle hooks."""
    if hooks:
        bootstrap_hooks(*hooks)
    return ProjectStore(root)


def read_services(db):
    """Construct the read-only ProjectReadServices port over a borrowed SQLite connection."""
    return ProjectReadServices(db)


@dataclass(frozen=True, slots=True)
class RDSApp:
    """Immutable application composition root for one project workspace."""
    root: Path
    store: ProjectStore

    def read_services(self, db):
        return ProjectReadServices(db)

    def provider(self, name: str):
        return lifecycle_provider(name)

    def bootstrap_hooks(self, *names: str):
        return bootstrap_hooks(*names)


def create_app(root, *, hooks=()):
    """Create a composed RDSApp instance for root."""
    store = open_store(root, hooks=hooks)
    return RDSApp(root=store.root, store=store)
