"""Parity, boundedness and lifecycle checks for the optional rds_bus adapter."""

from pathlib import Path
import ctypes as c
import os
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rds_bus_adapter as bus

NATIVE = bus.native_available()
LIBRARY = bus._library_filename()


def _require_native(case):
    """Fail (never skip) where required mode demands native; skip only in the optional local run."""
    if not NATIVE and bus.native_required():
        case.fail(f"RDS_REQUIRE_BUS=1 but {LIBRARY} is not built")
    if not NATIVE:
        case.skipTest(f"{LIBRARY} not built")


def _modes(case):
    """Both modes when native is available; required mode must not silently drop native."""
    if NATIVE:
        return (False, True)
    if bus.native_required():
        case.fail(f"RDS_REQUIRE_BUS=1 but {LIBRARY} is not built")
    return (False,)


class GraphContract(unittest.TestCase):
    def exercise_graph(self, use_native):
        with bus.TopologyGraph(use_native=use_native) as graph:
            self.assertEqual(graph.native, use_native)
            p0 = graph.add_node(bus.NODE_PREMISE, bytes(32), bus.TRUTH_TRUE)
            p1 = graph.add_node(bus.NODE_PREMISE, bytes([1]) * 32)
            lemma = graph.add_node(bus.NODE_CLAIM, bytes([2]) * 32)
            goal = graph.add_node(bus.NODE_GOAL, bytes([3]) * 32)
            self.assertEqual((p0, p1, lemma, goal), (0, 1, 2, 3))
            self.assertEqual(graph.add_edge(bus.EDGE_AND_DEDUCTION, [p0, p1], lemma), 0)
            self.assertEqual(graph.add_edge(bus.EDGE_AND_DEDUCTION, [lemma], goal), 1)
            self.assertEqual(graph.forward_closure([p0]), [])
            self.assertEqual(graph.forward_closure([p0, p1]), [lemma, goal])
            self.assertEqual(graph.backward_dependencies(goal), [lemma, p0, p1])
            self.assertEqual(graph.unresolved_frontier(goal), [lemma])
            # A satisfied premise supports lemma and goal; the frontier empties.
            graph.set_truth(p1, bus.TRUTH_TRUE)
            self.assertEqual(graph.unresolved_frontier(goal), [])
            graph.clear()
            self.assertEqual(graph.add_node(bus.NODE_GOAL, bytes(32)), 0)
            self.assertEqual(graph.unresolved_frontier(0), [])
            self.assertEqual(graph.forward_closure([]), [])
            self.assertEqual(graph.add_node(bus.NODE_CLAIM, bytes(32)), 1)
            self.assertEqual(graph.add_edge(bus.EDGE_OR_ALTERNATIVE, [0], 1), 0)
            self.assertEqual(graph.forward_closure([0]), [1])

    def test_python_fallback(self):
        self.exercise_graph(False)

    def test_auto_fallback_when_library_absent(self):
        # The absence path is scoped to optional mode instead of skipping the test.
        with mock.patch.dict(os.environ, {"RDS_REQUIRE_BUS": "0"}):
            with mock.patch.object(bus, "_candidate_paths", return_value=iter(())):
                with bus.TopologyGraph() as graph:
                    self.assertFalse(graph.native)
                    self.assertEqual(graph.add_node(bus.NODE_PREMISE, bytes(32)), 0)
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    bus.TopologyGraph(use_native=True)

    def test_required_mode_rejects_missing_library(self):
        with mock.patch.object(bus, "_candidate_paths", return_value=iter(())):
            with mock.patch.dict(os.environ, {"RDS_REQUIRE_BUS": "1"}):
                with self.assertRaisesRegex(RuntimeError, "RDS_REQUIRE_BUS"):
                    bus.TopologyGraph()
                with self.assertRaisesRegex(RuntimeError, "RDS_REQUIRE_BUS"):
                    bus.EventBus(use_native=None)

    def test_nonproof_edges_never_derive(self):
        # EDGE_OBSTRUCTS / EDGE_ISOMORPHISM produce neither closure nor dependency.
        for use_native in _modes(self):
            with self.subTest(use_native=use_native):
                with bus.TopologyGraph(use_native=use_native) as graph:
                    premise = graph.add_node(bus.NODE_PREMISE, bytes(32), bus.TRUTH_TRUE)
                    claim = graph.add_node(bus.NODE_CLAIM, bytes([1]) * 32)
                    goal = graph.add_node(bus.NODE_GOAL, bytes([2]) * 32)
                    graph.add_edge(bus.EDGE_OBSTRUCTS, [premise], claim)
                    graph.add_edge(bus.EDGE_ISOMORPHISM, [premise], claim)
                    graph.add_edge(bus.EDGE_AND_DEDUCTION, [claim], goal)
                    self.assertEqual(graph.forward_closure([premise]), [])
                    self.assertEqual(graph.backward_dependencies(goal), [claim])
                    self.assertEqual(graph.unresolved_frontier(goal), [claim])

    def test_false_nodes_never_derive(self):
        for use_native in _modes(self):
            with self.subTest(use_native=use_native):
                with bus.TopologyGraph(use_native=use_native) as graph:
                    premise = graph.add_node(bus.NODE_PREMISE, bytes(32), bus.TRUTH_TRUE)
                    refuted = graph.add_node(bus.NODE_CLAIM, bytes([1]) * 32, bus.TRUTH_FALSE)
                    graph.add_edge(bus.EDGE_AND_DEDUCTION, [premise], refuted)
                    self.assertEqual(graph.forward_closure([premise]), [])

    def test_obstruction_cut_name_removed(self):
        self.assertFalse(hasattr(bus.TopologyGraph, "minimal_obstruction_cut"))

    def test_bounds_rejected_before_mutation(self):
        for use_native in _modes(self):
            with self.subTest(use_native=use_native):
                with bus.TopologyGraph(use_native=use_native, max_nodes=2, max_edges=1,
                                       max_bytes=400) as graph:
                    graph.add_node(bus.NODE_PREMISE, bytes(32))
                    graph.add_node(bus.NODE_CLAIM, bytes([1]) * 32)
                    with self.assertRaises(OverflowError):
                        graph.add_node(bus.NODE_PREMISE, bytes(32))  # over node bound
                    graph.add_edge(bus.EDGE_AND_DEDUCTION, [0], 1)
                    with self.assertRaises(OverflowError):
                        graph.add_edge(bus.EDGE_AND_DEDUCTION, [0], 1)  # over edge bound
                    with self.assertRaises(OverflowError):
                        graph.add_edge(bus.EDGE_AND_DEDUCTION, [0] * 4097, 1)  # over source bound
                    # Rejected operations left no partial native/fallback state.
                    graph.set_truth(1, bus.TRUTH_TRUE)
                    self.assertEqual(graph.forward_closure([0]), [1])
                with bus.TopologyGraph(use_native=use_native, max_bytes=200) as tight:
                    tight.add_node(bus.NODE_PREMISE, bytes(32))
                    with self.assertRaises(OverflowError):
                        tight.add_node(bus.NODE_PREMISE, bytes(32))  # over byte bound
                    with self.assertRaises(OverflowError):
                        tight.add_edge(bus.EDGE_AND_DEDUCTION, [0], 0)  # over byte bound

    def test_oversize_sources_bounded_before_materialization(self):
        consumed = []

        def endless():
            while True:
                consumed.append(1)
                yield 0

        class SizedOnly:
            def __len__(self):
                return bus.MAX_EDGE_SOURCES + 1

            def __iter__(self):
                raise AssertionError("sized input must be rejected before iteration")

        class MislabeledFinite:
            def __len__(self):
                return 1

            def __iter__(self):
                for _ in range(bus.MAX_EDGE_SOURCES + 1):
                    consumed.append(1)
                    yield 0

        for use_native in _modes(self):
            with self.subTest(use_native=use_native):
                consumed.clear()
                with bus.TopologyGraph(use_native=use_native) as graph:
                    graph.add_node(bus.NODE_PREMISE, bytes(32), bus.TRUTH_TRUE)
                    graph.add_node(bus.NODE_CLAIM, bytes([1]) * 32)
                    graph.add_edge(bus.EDGE_AND_DEDUCTION, [0], 1)
                    before = (graph._edge_count, graph._bytes, graph.forward_closure([0]))
                    with self.assertRaises(OverflowError):
                        graph.add_edge(bus.EDGE_AND_DEDUCTION, endless(), 0)
                    self.assertEqual(len(consumed), bus.MAX_EDGE_SOURCES + 1,
                                     "iterable not consumption-bounded")
                    with self.assertRaises(OverflowError):
                        graph.add_edge(bus.EDGE_AND_DEDUCTION, SizedOnly(), 0)
                    with self.assertRaises(OverflowError):
                        graph.add_edge(bus.EDGE_AND_DEDUCTION, MislabeledFinite(), 0)
                    self.assertEqual(len(consumed), 2 * (bus.MAX_EDGE_SOURCES + 1),
                                     "mis-sized iterable was not consumption-bounded")
                    self.assertEqual((graph._edge_count, graph._bytes, graph.forward_closure([0])), before,
                                     "rejected sources mutated the graph")
                    with self.assertRaises(OverflowError):
                        graph.forward_closure(endless())
                    self.assertEqual(len(consumed), 2 * (bus.MAX_EDGE_SOURCES + 1) + bus.MAX_NODES + 1,
                                     "seed iterable not consumption-bounded")

    def test_exact_source_limit_is_accepted(self):
        for use_native in _modes(self):
            with self.subTest(use_native=use_native):
                with bus.TopologyGraph(use_native=use_native) as graph:
                    graph.add_node(bus.NODE_PREMISE, bytes(32), bus.TRUTH_TRUE)
                    self.assertEqual(graph.add_edge(bus.EDGE_OR_ALTERNATIVE,
                                                    iter([0] * bus.MAX_EDGE_SOURCES), 0), 0)
                    self.assertEqual(graph.forward_closure(iter([0] * bus.MAX_NODES)), [])

    def test_out_of_range_enums_rejected(self):
        with bus.TopologyGraph(use_native=False) as graph:
            graph.add_node(bus.NODE_PREMISE, bytes(32))
            for kind in (0, 6, 255):
                with self.assertRaises(ValueError):
                    graph.add_node(kind, bytes(32))
            for kind in (0, 5, 255):
                with self.assertRaises(ValueError):
                    graph.add_edge(kind, [0], 0)
            for truth in (-1, 3, 255):
                with self.assertRaises(ValueError):
                    graph.set_truth(0, truth)
            with self.assertRaises(ValueError):
                graph.add_node(bus.NODE_PREMISE, b"short")
            with self.assertRaises(ValueError):
                graph.add_edge(bus.EDGE_AND_DEDUCTION, [1], 0)
            with self.assertRaises(ValueError):
                graph.forward_closure([1])

    def test_closed_graph_rejects_operations(self):
        graph = bus.TopologyGraph(use_native=False)
        graph.close()
        graph.close()  # idempotent, no second destroy
        for call in (lambda: graph.add_node(bus.NODE_PREMISE, bytes(32)),
                     lambda: graph.add_edge(bus.EDGE_AND_DEDUCTION, [], 0),
                     lambda: graph.set_truth(0, bus.TRUTH_TRUE),
                     lambda: graph.clear(),
                     lambda: graph.unresolved_frontier(0),
                     lambda: graph.backward_dependencies(0),
                     lambda: graph.forward_closure([])):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                call()

    def test_native_graph(self):
        _require_native(self)
        self.exercise_graph(True)
        with bus.TopologyGraph(use_native=True) as graph:
            graph.add_node(bus.NODE_PREMISE, bytes(32))
            graph.add_edge(bus.EDGE_AND_DEDUCTION, [0], 0)
            self.assertEqual(graph._nodes, [], "native mode must not mirror the graph")
            self.assertEqual(graph._edges, [], "native mode must not mirror the graph")

    def test_loaded_library_reports_declared_abi(self):
        _require_native(self)
        self.assertEqual(bus._load_native().rds_bus_abi_version(), bus.ABI_VERSION)


class _BlockingGraphLib:
    """Fake C ABI 2 graph whose first query blocks while holding the live handle."""

    def __init__(self):
        self.log = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.destroyed = 0

    def rds_graph_create_bounded(self, nodes, edges, bytes_):
        return 0xDEF

    def rds_graph_add_node(self, handle, kind, digest, truth):
        return 0

    def rds_graph_set_truth(self, handle, node_id, truth):
        self.log.append("truth")
        return 0

    def rds_graph_unresolved_frontier(self, handle, target, buf, cap, length):
        if not buf:
            self.log.append("query:enter")
            self.entered.set()
            assert self.release.wait(5)
            c.cast(length, c.POINTER(c.c_size_t)).contents.value = 1
            return 0
        buf[0] = 0
        c.cast(length, c.POINTER(c.c_size_t)).contents.value = 1
        self.log.append("query:exit")
        return 0

    def rds_graph_clear(self, handle):
        self.log.append("clear")
        return 0

    def rds_graph_destroy(self, handle):
        self.log.append("destroy")
        self.destroyed += 1


class _BlockingBusLib:
    """Fake C ABI 2 bus whose post blocks while holding the live handle."""

    def __init__(self):
        self.log = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.destroyed = 0
        self.used_handle = None

    def rds_bus_init_bounded(self, capacity, max_bytes):
        return 0xABC

    def rds_bus_destroy(self, handle):
        self.log.append("destroy")
        self.destroyed += 1

    def rds_bus_post_event(self, handle, kind, data, length):
        self.used_handle = handle
        self.log.append("post:enter")
        self.entered.set()
        assert self.release.wait(5)
        self.log.append("post:exit")
        return 0


class LifecycleContract(unittest.TestCase):
    def test_graph_query_blocks_clear_and_close(self):
        fake = _BlockingGraphLib()
        with mock.patch.object(bus, "_select_native", return_value=fake):
            graph = bus.TopologyGraph(use_native=None)
        graph.add_node(bus.NODE_PREMISE, bytes(32))
        result = []
        query = threading.Thread(target=lambda: result.append(graph.unresolved_frontier(0)))
        query.start()
        self.assertTrue(fake.entered.wait(5), "query never reached the native call")
        closer = threading.Thread(target=lambda: (graph.set_truth(0, bus.TRUTH_TRUE), graph.clear(),
                                                  graph.close()))
        closer.start()
        time.sleep(0.2)
        self.assertEqual(fake.log, ["query:enter"], "mutation/destroy overlapped the live query")
        fake.release.set()
        query.join(5)
        closer.join(5)
        self.assertEqual(fake.log, ["query:enter", "query:exit", "truth", "clear", "destroy"])
        self.assertEqual(result, [[0]])
        self.assertEqual(fake.destroyed, 1)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            graph.clear()

    def test_bus_post_blocks_close(self):
        fake = _BlockingBusLib()
        with mock.patch.object(bus, "_select_native", return_value=fake):
            events = bus.EventBus(capacity=1, use_native=None)
        results = []
        poster = threading.Thread(target=lambda: results.append(events.post(1, b"x")))
        poster.start()
        self.assertTrue(fake.entered.wait(5), "post never reached the native call")
        self.assertEqual(fake.used_handle, 0xABC, "post used a handle that was already revoked")
        closer = threading.Thread(target=events.close)
        closer.start()
        time.sleep(0.2)
        self.assertEqual(fake.destroyed, 0, "destroy overlapped the in-flight post")
        fake.release.set()
        poster.join(5)
        closer.join(5)
        self.assertEqual(fake.log, ["post:enter", "post:exit", "destroy"])
        self.assertEqual(results, [True])
        events.close()  # idempotent, no second destroy
        self.assertEqual(fake.destroyed, 1)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            events.post(2, b"y")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            events.poll()


class EventBusContract(unittest.TestCase):
    def exercise_bus(self, use_native):
        with bus.EventBus(capacity=2, use_native=use_native) as events:
            self.assertEqual(events.native, use_native)
            self.assertTrue(events.post(7, b"first"))
            self.assertTrue(events.post(8))
            self.assertFalse(events.post(9, b"rejected"))
            self.assertEqual(events.poll(), (7, b"first"))
            # The rejected event stayed with the caller; the freed slot accepts it.
            self.assertTrue(events.post(9, b"rejected"))
            self.assertEqual(events.poll(), (8, b""))
            self.assertEqual(events.poll(), (9, b"rejected"))
            self.assertIsNone(events.poll())

    def test_python_fallback(self):
        self.exercise_bus(False)

    def test_byte_limit_and_capacity_reuse_after_drain(self):
        for use_native in _modes(self):
            with self.subTest(use_native=use_native):
                with bus.EventBus(capacity=2, max_bytes=3, use_native=use_native) as events:
                    for _ in range(2):  # drained capacity and bytes are fully reusable
                        self.assertTrue(events.post(1, b"abc"))
                        self.assertFalse(events.post(2, b"xy"))  # byte budget exhausted
                        self.assertEqual(events.poll(), (1, b"abc"))
                        self.assertTrue(events.post(2, b"xy"))  # capacity and bytes released
                        self.assertEqual(events.poll(), (2, b"xy"))
                        self.assertIsNone(events.poll())

    def test_payload_size_checked_before_conversion(self):
        converted = []
        original = bus._bytes_from_view
        with mock.patch.object(bus, "_bytes_from_view",
                               side_effect=lambda payload: converted.append(1) or original(payload)):
            with bus.EventBus(capacity=1, use_native=False) as events:
                oversized = bytearray(b"x" * (bus.MAX_EVENT_PAYLOAD + 1))
                with self.assertRaises(OverflowError):
                    events.post(1, oversized)
                with self.assertRaises(OverflowError):
                    events.post(1, memoryview(oversized))
                self.assertEqual(converted, [], "oversize payload was converted before the bound check")
                self.assertTrue(events.post(2, b"ok"))
                self.assertEqual(converted, [1])
                # A mutable caller buffer is snapshotted, not retained.
                payload = bytearray(b"live")
                self.assertEqual(events.poll(), (2, b"ok"))
                self.assertTrue(events.post(3, payload))
                payload[:] = b"gone"
                self.assertEqual(events.poll(), (3, b"live"))
            with self.assertRaises(ValueError):
                bus.EventBus(capacity=1, use_native=False).post(1, "text")

    def test_concurrent_producers_accept_equals_deliver(self):
        rounds = 200
        for _ in range(rounds):
            with bus.EventBus(capacity=1, max_bytes=1, use_native=False) as events:
                accepted = []
                barrier = threading.Barrier(2)

                def produce():
                    barrier.wait(5)
                    accepted.append(events.post(1, b"x"))

                threads = [threading.Thread(target=produce) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(5)
                self.assertEqual(len(accepted), 2)
                delivered = [events.poll() for _ in range(2)]
                self.assertEqual(sum(accepted), sum(1 for item in delivered if item is not None),
                                 "an acknowledged event was dropped")
                self.assertEqual(delivered[0][1] if delivered[0] else None, b"x")
                self.assertEqual(events._bytes, 0, "byte accounting leaked after drain")
                self.assertIsNone(events.poll())

    def test_concurrent_consumers_exhaust_queue(self):
        capacity = 8
        with bus.EventBus(capacity=capacity, use_native=False) as events:
            for index in range(capacity):
                self.assertTrue(events.post(index, b"e"))
            collected = []
            guard = threading.Lock()

            def consume():
                while True:
                    item = events.poll()
                    if item is None:
                        return
                    with guard:
                        collected.append(item)

            threads = [threading.Thread(target=consume) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertEqual(len(collected), capacity, "consumer lost or duplicated an event")
            self.assertEqual(sorted(kind for kind, _ in collected), list(range(capacity)))
            self.assertEqual(events._bytes, 0)

    def test_native_bus(self):
        _require_native(self)
        self.exercise_bus(True)


class NativeBuildContract(unittest.TestCase):
    def test_library_filename_covers_all_platforms(self):
        self.assertEqual(bus.ABI_VERSION, 2)
        self.assertEqual(LIBRARY, _expected_filename(os.name, sys.platform))
        self.assertEqual(bus._library_filename(), LIBRARY)
        for name, platform, expected in (("nt", "win32", "rds_bus.dll"),
                                         ("posix", "darwin", "librds_bus.dylib"),
                                         ("posix", "linux", "librds_bus.so")):
            with self.subTest(os_name=name, platform=platform):
                with mock.patch.object(bus.os, "name", name), \
                        mock.patch.object(bus.sys, "platform", platform):
                    self.assertEqual(bus._library_filename(), expected)
                    self.assertEqual(list(bus._candidate_paths())[-1].name, expected)


def _expected_filename(os_name, platform):
    if os_name == "nt":
        return "rds_bus.dll"
    return "librds_bus.dylib" if platform == "darwin" else "librds_bus.so"


if __name__ == "__main__":
    unittest.main()
