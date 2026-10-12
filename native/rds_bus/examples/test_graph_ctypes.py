import ctypes
import os
import sys

dll_path = os.path.join(os.path.dirname(__file__), "..", "target", "debug", "rds_bus.dll")
if not os.path.exists(dll_path):
    print(f"DLL not found at {dll_path}")
    sys.exit(1)

bus = ctypes.CDLL(dll_path)
bus.rds_graph_create_bounded.argtypes = [ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t]
bus.rds_graph_create_bounded.restype = ctypes.c_void_p
bus.rds_graph_destroy.argtypes = [ctypes.c_void_p]
bus.rds_graph_add_node.argtypes = [
    ctypes.c_void_p, ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint8
]
bus.rds_graph_add_node.restype = ctypes.c_uint32
bus.rds_graph_set_truth.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8]
bus.rds_graph_set_truth.restype = ctypes.c_int32
bus.rds_graph_add_edge.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint8,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_size_t,
    ctypes.c_uint32,
]
bus.rds_graph_add_edge.restype = ctypes.c_uint32
bus.rds_graph_unresolved_frontier.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
bus.rds_graph_unresolved_frontier.restype = ctypes.c_int32


def unresolved_frontier(graph, target):
    out_buf = (ctypes.c_uint32 * 16)()
    out_len = ctypes.c_size_t()
    rc = bus.rds_graph_unresolved_frontier(
        graph, target, out_buf, len(out_buf), ctypes.byref(out_len)
    )
    assert rc == 0
    return list(out_buf[:out_len.value])


g = bus.rds_graph_create_bounded(4096, 16384, 8 * 1024 * 1024)
assert g, "Failed to create graph"
try:
    # Node types: PREMISE = 2, CLAIM = 1, GOAL = 5; truth TRUE = 1, UNKNOWN = 0.
    hash_dummy = (ctypes.c_uint8 * 32)()
    p0 = bus.rds_graph_add_node(g, 2, hash_dummy, 1)
    p1 = bus.rds_graph_add_node(g, 2, hash_dummy, 0)
    l2 = bus.rds_graph_add_node(g, 1, hash_dummy, 0)
    t3 = bus.rds_graph_add_node(g, 5, hash_dummy, 0)
    assert p0 == 0 and p1 == 1 and l2 == 2 and t3 == 3

    srcs = (ctypes.c_uint32 * 2)(p0, p1)
    assert bus.rds_graph_add_edge(g, 1, srcs, 2, l2) == 0
    src_l2 = (ctypes.c_uint32 * 1)(l2)
    assert bus.rds_graph_add_edge(g, 1, src_l2, 1, t3) == 1

    assert unresolved_frontier(g, t3) == [l2]
    assert bus.rds_graph_set_truth(g, l2, 1) == 0
    assert unresolved_frontier(g, t3) == []

    assert bus.rds_graph_set_truth(g, p1, 1) == 0
    assert unresolved_frontier(g, t3) == []
finally:
    bus.rds_graph_destroy(g)

print("PASS: Python ctypes -> Rust rds_bus ABI 2 graph integration verified!")
