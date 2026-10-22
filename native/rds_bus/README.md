# rds_bus

Standard-library-only Rust prototype, producing both an rlib and a C-compatible
dynamic library. ABI version is 2 (`rds_bus_abi_version`); crate version is 0.2.0.
Build/test with `cargo build --offline` and `cargo test --offline`. On Windows,
`python -B examples/ctypes_smoke.py` validates ABI 2 and the ownership round trip
against the built debug DLL.

## Event ownership

The mutex-protected ring has fixed capacity (1..4096), FIFO ordering, and rejects
new events when full. Each payload is limited to 1 MiB. Slots allocate once at
initialization; packets allocate separately. This is bounded, not lock-free or
allocation-free. `rds_bus_init_bounded` also limits queued payload bytes to a
positive budget of at most 64 MiB (the default). Polling releases that budget.
The byte counter excludes slot/packet metadata, allocator overhead, temporary
allocations and caller-held packets. These are accounting caps, not a strict
process RSS cap; `post_event` allocates before attempting queue admission.

`post_event` copies caller bytes once. For zero-copy use `packet_new`, write through
`packet_data`, then `post_packet`: success transfers ownership to the bus, failure
leaves ownership with the caller. `poll_event` transfers ownership to the consumer,
which must call `packet_free`. Payload addresses remain stable across enqueue and
dequeue. No payload serialization or copying occurs on this path.

All handles are opaque C pointers; see `include/rds_bus.h`. Use ctypes `CDLL`,
explicit `argtypes`/`restype`, `c_void_p` handles and `c_size_t` lengths. Data views
must not outlive their packet or be used after publishing it. Double free, invalid
pointers and concurrent destruction are caller errors. Bus posting/polling is
thread-safe; individual packet mutation must be exclusive. Destroying a bus frees
queued packets, but not packets already polled by the caller. Null destruction is
a no-op. Version returns a static NUL-terminated string and must not be freed.

## Derived topology graph

The graph is an in-memory relationship view with no ledger writes or execution
authority. Defaults and hard maxima are 4096 nodes, 16384 edges, and an 8 MiB
accounting budget. Bounded creation accepts positive limits up to these maxima.
Admission charges 128 bytes per node and 128 + 16 times source count per edge;
each edge accepts at most 4096 sources. This accounting does not measure vector
capacity, allocator overhead, traversal scratch space or process RSS. Rejection
does not consume the budget. `clear` resets nodes, edges and accounting; some
outer vector capacities are retained, and identifiers restart at zero.

AND edges require every source; OR edges require any source. Empty AND edges
are vacuously satisfied; empty OR edges are not. Obstruction and isomorphism
(analogy) edges are relation-only: they neither prove targets nor participate in
backward prerequisite traversal. Node kind is metadata, not a proof rule.
Explicit FALSE seeds and targets cannot propagate support. `forward_closure`
uses the supplied seeds, not all stored TRUE nodes, and returns newly derived
IDs only (excluding accepted seeds), without mutating stored truth.

`backward_dependencies` returns reachable prerequisites through AND/OR edges.
`unresolved_frontier` first computes support from all stored TRUE nodes, then
stops backward traversal at unsupported prerequisites. An already proved lemma
supports downstream goals without requiring its upstream premises to be proved
again. This is not a minimum cut or a sufficient proof-obligation set: it can
include multiple OR alternatives, and an empty frontier can also mean a target
has no prerequisite edges. In particular an empty frontier does not certify an
isolated, relation-only or contradicted target as true.

Graph calls must be serialized by the caller. See the header for all exports,
constants and buffer contracts. Node/edge insertion returns `UINT32_MAX` on
rejection; mutation/query status is 0 on success and -1 on invalid arguments.
Queries accept a null output buffer to obtain the required length. A non-null
buffer with insufficient capacity returns -2 and writes no partial IDs.
Forward query seed IDs must exist and seed count cannot exceed node count.
Allocation failure and invalid foreign pointers are not recoverable API errors.

## Windows process guard

`job_create` creates an unnamed Job Object and enables KILL_ON_JOB_CLOSE.
`job_bind` assigns a live process handle (PROCESS_SET_QUOTA and PROCESS_TERMINATE
rights); returns zero or the Windows error code, with -1 for invalid arguments.
The caller retains the process handle. `job_destroy` closes the owned job handle;
bound processes are terminated when the last job handle closes. Creation failure
returns null. The safe Rust wrapper returns `io::Result` with the OS error.
Non-Windows C creation returns null and binding returns -1.

This is a guard scaffold, not a process launcher or restart supervisor. Assignment
of an already-running process has a race before binding; a production launcher
should create suspended, assign to the job, then resume. Existing job membership
and host policy can reject assignment. The Windows unit test binds a disposable
child, verifies it is alive, and checks termination after closing the job.

Allocation exhaustion follows Rust's allocator behavior (may abort). Arbitrary
foreign pointer faults cannot be converted to return codes. The crate does not
provide a persistent log, subscriptions, background workers, or crash recovery.
