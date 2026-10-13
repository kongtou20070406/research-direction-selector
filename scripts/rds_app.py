"""L4 composition root wiring ProjectStore, read services, lifecycle hooks and worker entrypoints.

All dependencies point strictly downward (L4 -> L3/L2/L1/L0/SHARED). Existing
module entrypoints remain thin compatibility forwarders.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rds_code_diff import encode_code_diff, score_candidate_diff
from rds_decision_packet import DecisionPacket
from rds_outbox import Outbox
from rds_packet_graph import project_packet
from rds_predictor_worker import PredictionWorker
from rds_project import ProjectStore
from rds_project_hooks import _modules, provider as lifecycle_provider, register as register_hook
from rds_project_read_services import ProjectReadServices
from rds_project_values import require
from rds_research_advisor import advise as advise_packet
from rds_research_import import import_research
from rds_transition import export_transitions

__all__ = [
    "RDSApp",
    "advise_packet",
    "bootstrap_hooks",
    "create_app",
    "encode_code_diff",
    "export_transitions",
    "import_packet",
    "lifecycle_provider",
    "open_store",
    "outbox",
    "project_packet",
    "read_services",
    "score_candidate_diff",
    "shadow_worker",
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


def outbox(store, db):
    """Construct the transactional Outbox over a borrowed SQLite connection."""
    return Outbox(store, db)


def import_packet(root, **kwargs) -> DecisionPacket:
    """Import explicit files/texts under root into an immutable F1 DecisionPacket."""
    raw = import_research(root, **kwargs)
    return DecisionPacket.from_json(raw)


def shadow_worker(request, *, adapter="unavailable", snapshot_sha256):
    """Construct an owned one-shot CPU shadow prediction worker."""
    return PredictionWorker(request, adapter=adapter, snapshot_sha256=snapshot_sha256)


@dataclass(frozen=True, slots=True)
class RDSApp:
    """Immutable application composition root for one project workspace."""
    root: Path
    store: ProjectStore

    def read_services(self, db):
        return ProjectReadServices(db)

    def outbox(self, db):
        return Outbox(self.store, db)

    def provider(self, name: str):
        return lifecycle_provider(name)

    def bootstrap_hooks(self, *names: str):
        return bootstrap_hooks(*names)

    def import_packet(self, **kwargs) -> DecisionPacket:
        return import_packet(self.root, **kwargs)

    def advise(self, packet: DecisionPacket, observations=(), language="zh"):
        return advise_packet(packet, observations=observations, language=language)

    def encode_code_diff(self, base_code: str, candidate_code: str):
        return encode_code_diff(base_code, candidate_code)

    def shadow_worker(self, request, *, adapter="unavailable", snapshot_sha256):
        return shadow_worker(request, adapter=adapter, snapshot_sha256=snapshot_sha256)


def create_app(root, *, hooks=()):
    """Create a composed RDSApp instance for root."""
    store = open_store(root, hooks=hooks)
    return RDSApp(root=store.root, store=store)

