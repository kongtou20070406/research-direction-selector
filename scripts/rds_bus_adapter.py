"""Python access to the optional rds_bus topology graph and volatile event bus.

Graph ids, enum ranges, byte accounting and traversal order match
native/rds_bus/src/graph.rs at C ABI 2. Native mode calls
rds_graph_create_bounded / rds_graph_unresolved_frontier /
rds_graph_backward_dependencies / rds_graph_forward_closure / rds_graph_clear
and keeps only counters locally, never a second full Python graph. The
pure-Python fallback mirrors the same traversals, including TRUTH_FALSE nodes
and the non-proof edge types (EDGE_OBSTRUCTS, EDGE_ISOMORPHISM).

Stateful graph and bus operations use a per-instance reentrant lock. It spans
native handle access (including both traversal query/fetch calls), fallback
state changes, queue operations, byte accounting and destruction. Input
validation and bounded materialization may happen before acquiring the lock;
the locked open-state recheck protects subsequent handle use. ctypes releases
the GIL during native calls and the C ABI requires that no operation overlap
destruction, so a Python-side lock enforces lifetime safety. Locking serializes
callers of one object; it does not make the object reentrant across processes.

Limits are enforced before adapter-owned allocation: source lists are bounded
before materialization (sized inputs by length, unsized iterables by consuming
at most MAX_EDGE_SOURCES + 1 items) and bytes-like payloads by declared
``nbytes`` before conversion. These logical budgets cover declared payload and
graph bytes only, not Python container overhead, conversion buffers, traversal
workspaces or other in-flight operations.

The event bus is a volatile bounded FIFO, not a durable outbox: it holds no
state across process exit and never substitutes for the SQLite ledger. A full
queue keeps the caller's event; a polled native packet is freed in all cases.

rds_bus also exports a Windows Job Object scaffold (rds_bus_job_create,
rds_bus_job_bind, rds_bus_job_destroy). It is deliberately unused and not
loaded here: RDS owns worker processes through rds_project._WindowsPopen, which
binds the job atomically at CreateProcessW via PROC_THREAD_ATTRIBUTE_JOB_LIST
and therefore avoids the start-then-assign race a separate wrapper has. No
production executor is exported from this module.

Set RDS_REQUIRE_BUS=1 to forbid silent fallback: auto-detection and
use_native=True then raise instead of using the Python mirror.
"""

from __future__ import annotations

import ctypes as c
import os
from pathlib import Path
import sys
import threading
from collections import deque
from collections.abc import Iterable, Sequence
from itertools import islice

NODE_CLAIM, NODE_PREMISE, NODE_OBSTRUCTION, NODE_ANALOGY, NODE_GOAL = range(1, 6)
TRUTH_UNKNOWN, TRUTH_TRUE, TRUTH_FALSE = range(3)
EDGE_AND_DEDUCTION, EDGE_OR_ALTERNATIVE, EDGE_OBSTRUCTS, EDGE_ISOMORPHISM = range(1, 5)

ABI_VERSION = 2
MAX_NODES = 4096
MAX_EDGES = 16384
MAX_GRAPH_BYTES = 8 * 1024 * 1024
MAX_EDGE_SOURCES = 4096
MAX_EVENTS = 4096
MAX_EVENT_PAYLOAD = 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024 * 1024

_BUS_OK, _BUS_INVALID, _BUS_FULL, _BUS_EMPTY = 0, -1, -2, -3
_NODE_BYTES = 128
_EDGE_BYTES = 128
_EDGE_SOURCE_BYTES = 16
_U32_MAX = 2**32 - 1
_ROOT = Path(__file__).resolve().parents[1]


def _library_filename() -> str:
    if os.name == "nt":
        return "rds_bus.dll"
    if sys.platform == "darwin":
        return "librds_bus.dylib"
    return "librds_bus.so"


def _candidate_paths():
    override = os.environ.get("RDS_BUS_LIBRARY")
    if override:
        yield Path(override)
    base = _ROOT / "native" / "rds_bus"
    for directory in (base / "target" / "release", base / "target" / "debug", base):
        yield directory / _library_filename()


def _load_native():
    """First candidate exposing the full C ABI 2 surface, or None."""
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            lib = c.CDLL(str(path))
            signatures = {
                "rds_bus_abi_version": ([], c.c_uint32),
                "rds_bus_init_bounded": ([c.c_size_t, c.c_size_t], c.c_void_p),
                "rds_bus_destroy": ([c.c_void_p], None),
                "rds_bus_post_event": ([c.c_void_p, c.c_uint32, c.POINTER(c.c_uint8), c.c_size_t], c.c_int32),
                "rds_bus_poll_event": ([c.c_void_p, c.POINTER(c.c_void_p)], c.c_int32),
                "rds_bus_packet_kind": ([c.c_void_p], c.c_uint32),
                "rds_bus_packet_len": ([c.c_void_p], c.c_size_t),
                "rds_bus_packet_data": ([c.c_void_p], c.c_void_p),
                "rds_bus_packet_free": ([c.c_void_p], None),
                "rds_graph_create_bounded": ([c.c_size_t, c.c_size_t, c.c_size_t], c.c_void_p),
                "rds_graph_destroy": ([c.c_void_p], None),
                "rds_graph_add_node": ([c.c_void_p, c.c_uint8, c.POINTER(c.c_uint8), c.c_uint8], c.c_uint32),
                "rds_graph_add_edge": ([c.c_void_p, c.c_uint8, c.POINTER(c.c_uint32), c.c_size_t, c.c_uint32], c.c_uint32),
                "rds_graph_set_truth": ([c.c_void_p, c.c_uint32, c.c_uint8], c.c_int32),
                "rds_graph_unresolved_frontier": ([c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_size_t)], c.c_int32),
                "rds_graph_backward_dependencies": ([c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_size_t)], c.c_int32),
                "rds_graph_forward_closure": ([c.c_void_p, c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_uint32), c.c_size_t, c.POINTER(c.c_size_t)], c.c_int32),
                "rds_graph_clear": ([c.c_void_p], c.c_int32),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(lib, name)
                fn.argtypes, fn.restype = args, result
        except (OSError, AttributeError):
            continue
        found = lib.rds_bus_abi_version()
        if found != ABI_VERSION:
            raise RuntimeError(f"rds_bus at {path} reports ABI {found}, adapter requires {ABI_VERSION}")
        return lib
    return None


def native_required() -> bool:
    """True when RDS_REQUIRE_BUS=1 forbids using the Python mirror."""
    return os.environ.get("RDS_REQUIRE_BUS") == "1"


def native_available() -> bool:
    return _load_native() is not None


def _select_native(use_native: bool | None):
    if use_native not in (None, True, False):
        raise ValueError("use_native must be True, False, or None")
    if use_native is False:
        return None
    lib = _load_native()
    if lib is None and (use_native is True or native_required()):
        detail = "RDS_REQUIRE_BUS=1" if native_required() else "use_native=True"
        raise RuntimeError(f"rds_bus native library is unavailable ({detail})")
    return lib


def _int_in(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _bounded_sources(sources, limit: int, name: str) -> list[int]:
    """Materialize at most limit+1 ids, using len only as an early rejection."""
    try:
        count = len(sources)
    except TypeError:
        pass
    else:
        if count > limit:
            raise OverflowError(f"{name} limit reached ({limit})")
    items = list(islice(iter(sources), limit + 1))
    if len(items) > limit:
        raise OverflowError(f"{name} limit reached ({limit})")
    return items


def _bytes_from_view(payload) -> bytes:
    """Single conversion point from a bytes-like object to bytes."""
    return bytes(payload)


def _bounded_payload(payload) -> bytes:
    """``payload`` as bytes; the declared size is checked before any copy."""
    if isinstance(payload, (bytes, bytearray)):
        size = len(payload)
    elif isinstance(payload, memoryview):
        size = payload.nbytes
    else:
        raise ValueError("payload must be bytes-like")
    if size > MAX_EVENT_PAYLOAD:
        raise OverflowError(f"event payload limit reached ({MAX_EVENT_PAYLOAD} bytes)")
    data = _bytes_from_view(payload)
    if len(data) > MAX_EVENT_PAYLOAD:
        raise OverflowError(f"event payload limit reached ({MAX_EVENT_PAYLOAD} bytes)")
    return data


class TopologyGraph:
    """Bounded in-memory hypergraph; use_native=None auto-detects the library."""

    def __init__(self, *, use_native: bool | None = None, max_nodes: int = MAX_NODES,
                 max_edges: int = MAX_EDGES, max_bytes: int = MAX_GRAPH_BYTES):
        _int_in(max_nodes, 1, MAX_NODES, "max_nodes")
        _int_in(max_edges, 1, MAX_EDGES, "max_edges")
        _int_in(max_bytes, 1, MAX_GRAPH_BYTES, "max_bytes")
        self._lib = _select_native(use_native)
        self._lock = threading.RLock()
        self._max_nodes, self._max_edges, self._max_bytes = max_nodes, max_edges, max_bytes
        self._node_count = self._edge_count = self._bytes = 0
        self._open = False
        self._handle = None
        self._nodes: list[tuple[int, int]] = []
        self._edges: list[tuple[int, tuple[int, ...], int]] = []
        self._incoming: list[list[int]] = []
        if self._lib:
            handle = self._lib.rds_graph_create_bounded(max_nodes, max_edges, max_bytes)
            if not handle:
                raise RuntimeError("rds_graph_create_bounded failed")
            self._handle = handle
        self._open = True

    @property
    def native(self) -> bool:
        return self._lib is not None

    def _ensure_open(self):
        if not self._open:
            raise RuntimeError("graph is closed")

    def add_node(self, kind: int, fact_hash: bytes, truth: int = TRUTH_UNKNOWN) -> int:
        _int_in(kind, NODE_CLAIM, NODE_GOAL, "kind")
        _int_in(truth, TRUTH_UNKNOWN, TRUTH_FALSE, "truth")
        if not isinstance(fact_hash, bytes) or len(fact_hash) != 32:
            raise ValueError("fact_hash must be exactly 32 bytes")
        with self._lock:
            self._ensure_open()
            if self._node_count >= self._max_nodes:
                raise OverflowError("graph node limit reached")
            if _NODE_BYTES > self._max_bytes - self._bytes:
                raise OverflowError("graph byte limit reached")
            node_id = self._node_count
            if self._lib:
                digest = (c.c_uint8 * 32).from_buffer_copy(fact_hash)
                if self._lib.rds_graph_add_node(self._handle, kind, digest, truth) != node_id:
                    raise RuntimeError("native graph add_node failed")
            else:
                self._nodes.append((kind, truth))
                self._incoming.append([])
            self._node_count += 1
            self._bytes += _NODE_BYTES
            return node_id

    def add_edge(self, kind: int, sources: Sequence[int] | Iterable[int], target: int) -> int:
        _int_in(kind, EDGE_AND_DEDUCTION, EDGE_ISOMORPHISM, "kind")
        with self._lock:
            self._ensure_open()
            _int_in(target, 0, self._node_count - 1, "target")
        ids = _bounded_sources(sources, MAX_EDGE_SOURCES, "edge source")
        with self._lock:
            self._ensure_open()
            _int_in(target, 0, self._node_count - 1, "target")
            for source in ids:
                _int_in(source, 0, self._node_count - 1, "source")
            if self._edge_count >= self._max_edges:
                raise OverflowError("graph edge limit reached")
            cost = _EDGE_BYTES + _EDGE_SOURCE_BYTES * len(ids)
            if cost > self._max_bytes - self._bytes:
                raise OverflowError("graph byte limit reached")
            edge_id = self._edge_count
            if self._lib:
                src = (c.c_uint32 * len(ids))(*ids)
                if self._lib.rds_graph_add_edge(self._handle, kind, src, len(ids), target) != edge_id:
                    raise RuntimeError("native graph add_edge failed")
            else:
                self._edges.append((kind, tuple(ids), target))
                self._incoming[target].append(edge_id)
            self._edge_count += 1
            self._bytes += cost
            return edge_id

    def set_truth(self, node_id: int, truth: int) -> None:
        _int_in(truth, TRUTH_UNKNOWN, TRUTH_FALSE, "truth")
        with self._lock:
            self._ensure_open()
            _int_in(node_id, 0, self._node_count - 1, "node_id")
            if self._lib:
                if self._lib.rds_graph_set_truth(self._handle, node_id, truth) != 0:
                    raise RuntimeError("native graph set_truth failed")
            else:
                kind, _ = self._nodes[node_id]
                self._nodes[node_id] = (kind, truth)

    def _native_ids(self, function, prefix, name) -> list[int]:
        """Two-call query then fetch; callers hold ``self._lock`` for both."""
        length = c.c_size_t()
        if function(self._handle, *prefix, None, 0, c.byref(length)) != 0:
            raise RuntimeError(f"native graph {name} failed")
        count = length.value
        if count > self._node_count:
            raise RuntimeError(f"native graph {name} returned {count} ids")
        if not count:
            return []
        buf = (c.c_uint32 * count)()
        if function(self._handle, *prefix, buf, count, c.byref(length)) != 0:
            raise RuntimeError(f"native graph {name} failed")
        return list(buf)[:length.value]

    def unresolved_frontier(self, target: int) -> list[int]:
        """Unresolved prerequisite frontier; not a minimum cut or proof obligation set."""
        with self._lock:
            self._ensure_open()
            _int_in(target, 0, self._node_count - 1, "target")
            if self._lib:
                return self._native_ids(self._lib.rds_graph_unresolved_frontier, (target,),
                                        "unresolved_frontier")
            seeds = [node_id for node_id, (_, truth) in enumerate(self._nodes) if truth == TRUTH_TRUE]
            supported = set(seeds)
            supported.update(self._derive(seeds))
            visited = {target}
            queue = deque([target])
            frontier = []
            while queue:
                current = queue.popleft()
                if current in supported:
                    continue
                if current != target and self._nodes[current][1] != TRUTH_TRUE:
                    frontier.append(current)
                    continue
                for edge_id in self._incoming[current]:
                    kind, sources, _ = self._edges[edge_id]
                    if kind not in (EDGE_AND_DEDUCTION, EDGE_OR_ALTERNATIVE):
                        continue
                    for source in sources:
                        if source not in visited:
                            visited.add(source)
                            queue.append(source)
            return frontier

    def backward_dependencies(self, target: int) -> list[int]:
        """Backward reachable prerequisites; non-proof edges are not traversed."""
        with self._lock:
            self._ensure_open()
            _int_in(target, 0, self._node_count - 1, "target")
            if self._lib:
                return self._native_ids(self._lib.rds_graph_backward_dependencies, (target,),
                                        "backward_dependencies")
            visited = {target}
            queue = deque([target])
            dependencies = []
            while queue:
                current = queue.popleft()
                for edge_id in self._incoming[current]:
                    kind, sources, _ = self._edges[edge_id]
                    if kind not in (EDGE_AND_DEDUCTION, EDGE_OR_ALTERNATIVE):
                        continue
                    for source in sources:
                        if source not in visited:
                            visited.add(source)
                            dependencies.append(source)
                            queue.append(source)
            return dependencies

    def _derive(self, known) -> list[int]:
        """Nodes newly derived from the given true set, mirroring native ordering."""
        true_set = {node_id for node_id in known
                    if node_id < self._node_count and self._nodes[node_id][1] != TRUTH_FALSE}
        derived = []
        changed = True
        while changed:
            changed = False
            for kind, sources, target in self._edges:
                if target in true_set or self._nodes[target][1] == TRUTH_FALSE:
                    continue
                satisfied = (kind == EDGE_AND_DEDUCTION and all(source in true_set for source in sources)) or (
                    kind == EDGE_OR_ALTERNATIVE and any(source in true_set for source in sources))
                if satisfied:
                    true_set.add(target)
                    derived.append(target)
                    changed = True
        return derived

    def forward_closure(self, known_true: Sequence[int]) -> list[int]:
        """Nodes deductively derived from known_true; TRUTH_FALSE targets never derive."""
        seeds: list[int] = []
        for node_id in _bounded_sources(known_true, MAX_NODES, "known_true node"):
            _int_in(node_id, 0, _U32_MAX, "known_true node")
            if node_id not in seeds:
                seeds.append(node_id)
        with self._lock:
            self._ensure_open()
            for node_id in seeds:
                _int_in(node_id, 0, self._node_count - 1, "known_true node")
            if self._lib:
                src = (c.c_uint32 * len(seeds))(*seeds)
                return self._native_ids(self._lib.rds_graph_forward_closure,
                                        (src, len(seeds)), "forward_closure")
            return self._derive(seeds)

    def clear(self) -> None:
        """Drop the derived view; retained capacities stay bounded, no ledger writes."""
        with self._lock:
            self._ensure_open()
            if self._lib:
                if self._lib.rds_graph_clear(self._handle) != 0:
                    raise RuntimeError("native graph clear failed")
            else:
                self._nodes.clear()
                self._edges.clear()
                self._incoming.clear()
            self._node_count = self._edge_count = self._bytes = 0

    def close(self) -> None:
        if not getattr(self, "_open", False):
            return
        with self._lock:
            if not self._open:
                return
            handle = self._handle
            self._handle = None
            self._open = False
            self._nodes.clear()
            self._edges.clear()
            self._incoming.clear()
            if self._lib and handle:
                self._lib.rds_graph_destroy(handle)

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()


class EventBus:
    """Volatile bounded FIFO of (kind, payload); native C ABI 2 or Python mirror.

    Item and byte limits match native MAX_CAPACITY/MAX_BYTES. A full queue keeps
    the caller's event and returns False; a polled event is freed in all cases.
    """

    def __init__(self, capacity: int = MAX_EVENTS, *, max_bytes: int = MAX_EVENT_BYTES,
                 use_native: bool | None = None):
        _int_in(capacity, 1, MAX_EVENTS, "capacity")
        _int_in(max_bytes, 1, MAX_EVENT_BYTES, "max_bytes")
        self._lib = _select_native(use_native)
        self._lock = threading.RLock()
        self._capacity = capacity
        self._max_bytes = max_bytes
        self._bytes = 0
        self._open = False
        self._handle = None
        # Unbounded deque with an explicit capacity check under the lock: a
        # maxlen queue would silently evict an already accepted event.
        self._events: deque[tuple[int, bytes]] = deque()
        if self._lib:
            handle = self._lib.rds_bus_init_bounded(capacity, max_bytes)
            if not handle:
                raise RuntimeError("rds_bus_init_bounded failed")
            self._handle = handle
        self._open = True

    @property
    def native(self) -> bool:
        return self._lib is not None

    def _ensure_open(self):
        if not self._open:
            raise RuntimeError("event bus is closed")

    def post(self, kind: int, payload: bytes = b"") -> bool:
        """Append one event; False when the queue is full and nothing was taken."""
        _int_in(kind, 0, _U32_MAX, "kind")
        self._ensure_open()
        data = _bounded_payload(payload)
        with self._lock:
            self._ensure_open()
            if self._lib:
                buffer_ = (c.c_uint8 * len(data)).from_buffer_copy(data) if data else None
                code = self._lib.rds_bus_post_event(self._handle, kind, buffer_, len(data))
                if code == _BUS_OK:
                    return True
                if code == _BUS_FULL:
                    return False
                raise RuntimeError(f"native event post failed ({code})")
            if len(self._events) >= self._capacity or len(data) > self._max_bytes - self._bytes:
                return False
            self._events.append((kind, data))
            self._bytes += len(data)
            return True

    def poll(self):
        """Remove the oldest event as (kind, payload), or None when empty."""
        with self._lock:
            self._ensure_open()
            if self._lib:
                packet = c.c_void_p()
                code = self._lib.rds_bus_poll_event(self._handle, c.byref(packet))
                if code == _BUS_EMPTY:
                    return None
                if code != _BUS_OK or not packet.value:
                    raise RuntimeError(f"native event poll failed ({code})")
                try:
                    return (self._lib.rds_bus_packet_kind(packet),
                            c.string_at(self._lib.rds_bus_packet_data(packet),
                                        self._lib.rds_bus_packet_len(packet)))
                finally:
                    self._lib.rds_bus_packet_free(packet)
            if not self._events:
                return None
            kind, payload = self._events.popleft()
            self._bytes -= len(payload)
            return kind, payload

    def close(self) -> None:
        if not getattr(self, "_open", False):
            return
        with self._lock:
            if not self._open:
                return
            handle = self._handle
            self._handle = None
            self._open = False
            self._events.clear()
            self._bytes = 0
            if self._lib and handle:
                self._lib.rds_bus_destroy(handle)

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()


__all__ = ["TopologyGraph", "EventBus", "native_available", "native_required",
           "ABI_VERSION", "MAX_NODES", "MAX_EDGES", "MAX_GRAPH_BYTES",
           "MAX_EDGE_SOURCES", "MAX_EVENTS", "MAX_EVENT_PAYLOAD", "MAX_EVENT_BYTES",
           "NODE_CLAIM", "NODE_PREMISE", "NODE_OBSTRUCTION", "NODE_ANALOGY",
           "NODE_GOAL", "TRUTH_UNKNOWN", "TRUTH_TRUE", "TRUTH_FALSE",
           "EDGE_AND_DEDUCTION", "EDGE_OR_ALTERNATIVE", "EDGE_OBSTRUCTS",
           "EDGE_ISOMORPHISM"]
