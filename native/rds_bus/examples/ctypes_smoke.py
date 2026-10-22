"""Run after cargo build; validates the actual DLL ABI and ownership round trip."""
import ctypes as c
from pathlib import Path

lib = c.CDLL(str(Path(__file__).resolve().parents[1] / "target/debug/rds_bus.dll"))
ptr = c.c_void_p
signatures = {
    "version": ([], c.c_char_p),
    "abi_version": ([], c.c_uint32),
    "init": ([c.c_size_t], ptr),
    "destroy": ([ptr], None),
    "packet_new": ([c.c_uint32, c.c_size_t], ptr),
    "packet_data": ([ptr], ptr),
    "packet_len": ([ptr], c.c_size_t),
    "packet_kind": ([ptr], c.c_uint32),
    "packet_free": ([ptr], None),
    "post_packet": ([ptr, ptr], c.c_int32),
    "poll_event": ([ptr, c.POINTER(ptr)], c.c_int32),
}
for name, (args, result) in signatures.items():
    fn = getattr(lib, "rds_bus_" + name)
    fn.argtypes, fn.restype = args, result

assert lib.rds_bus_abi_version() == 2
bus = lib.rds_bus_init(2)
assert bus
packet = lib.rds_bus_packet_new(7, 5)
assert packet
data = lib.rds_bus_packet_data(packet)
c.memmove(data, b"hello", 5)
assert lib.rds_bus_post_packet(bus, packet) == 0
out = ptr()
assert lib.rds_bus_poll_event(bus, c.byref(out)) == 0
assert out.value == packet
assert lib.rds_bus_packet_data(out) == data
assert lib.rds_bus_packet_kind(out) == 7
assert c.string_at(data, lib.rds_bus_packet_len(out)) == b"hello"
lib.rds_bus_packet_free(out)
assert lib.rds_bus_poll_event(bus, c.byref(out)) == -3
assert out.value is None
lib.rds_bus_destroy(bus)
print("ctypes DLL smoke: PASS (ABI 2, zero-copy pointer identity, FIFO, free)")
