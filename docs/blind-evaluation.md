# Opt-in blinded benchmark workers

`scripts/rds_blind_runner.py` is a separate Linux benchmark runner. It does not
change `ProjectStore`, ordinary project execution, `codex exec`, or the default
autonomy worker into an OS sandbox. Folder names such as `sealed/`, omitted prompt
text, Python audit hooks, and a provider's read-only filesystem setting are not
answer-access isolation. Existing historical replay cases remain development
cases, not independent holdouts.

The runner accepts an explicitly public request, a Python worker, and a reviewed
clean runtime file inventory. It collects one bounded JSON proposal. It neither
adopts returned source nor executes an evaluator. Evaluator code, labels, held-out
outcomes and private project records stay in the trusted controller, outside the
worker filesystem and environment.

## Admission and trust boundary

The controller and installed system bubblewrap are trusted. The candidate worker
is not. Runtime code is also trusted: use a clean, independently reviewed Python
runtime, not a directory containing the benchmark, custom startup hooks, or
credentials. An inventory's hash proves identity, not that its contents are safe.
The runner cannot determine whether a caller has deliberately put an answer into
a field named `prompt`, a public file, worker source, or the runtime itself.

Before any candidate dispatch, the runner:

1. Validates explicit input/file identities and bounds. Public JSON contains only
   selected scalar leaves; no whole RDS request or nested evaluator/policy object
   is copied automatically. Public file exports require individual paths and
   SHA-256 identities; recursive directory exports and globs are unsupported.
2. Rejects declared private-root exports, including symlinks resolving into them,
   and rejects `/proc`, `/sys` and `/dev` source files. Opens canonical components
   with `O_NOFOLLOW`, verifies the exact bytes, and copies them into new snapshots.
   Original host source directories are never mounted.
3. Requires an unprivileged Linux host account and the installed `/usr/bin/bwrap`
   or `/bin/bwrap`. Windows, missing tools, disallowed namespaces, old unsupported
   flags and failed canaries produce a refusal. There is no unsandboxed fallback,
   privileged mode, host-security reconfiguration, or automatic installation.
4. Uses mandatory user, PID, network, IPC and UTS namespaces, disables nested user
   namespaces, drops capabilities, clears the environment, and closes inherited
   file descriptors. A mandatory architecture-checked seccomp filter blocks
   socket/socketpair creation (including non-IP families), kernel keyring APIs
   and io_uring APIs, plus ptrace/process-memory and pidfd descriptor access.
   Other audit architectures and x32 syscall numbers are
   rejected; only little-endian x86_64 and aarch64 are supported. Kernel key
   metadata paths `/proc/keys` and `/proc/key-users` are masked by a new empty,
   read-only file; `/proc/1` is masked with a new empty directory to prevent
   reaper memory/descriptor access. The hostname is fixed to `rds-blind`. The
   only directory mounts are the newly staged runtime,
   public input and runner code snapshots, all read-only. There is a fresh proc
   filesystem and minimal device filesystem; `/dev` and `/dev/shm` are read-only
   for regular-file creation. Only `/work` is writable, on a 32-MiB tmpfs.
5. Runs trusted live canaries with the same sandbox command before dispatching
   worker code: outside private-file/proc-root access, an intentionally
   inheritable secret file descriptor, secret environment, PID/user/network
   namespace identity, host-loopback connectivity, host routes, correct public
   input and read-only mounts. Mount flags are checked, so file permissions alone
   cannot satisfy a read-only canary. The check accepts bubblewrap's fixed
   `PWD=/work`, but rejects unexpected environment variables or values.
   Additional canaries verify immutable anonymous stdin, filter descriptor
   closure, blocked socket/keyring/io_uring/process-inspection APIs, and
   key-metadata/reaper mask identity.
   Key API canaries use only invalid arguments; they do not inspect real keys.

A failed probe retains its actual stdout/stderr and writes `status: refused`,
without candidate dispatch intent. Passing canaries exercise these declared
boundaries; they are not a proof against kernel exploits or all side channels.
Host administrators, compromised runtimes, concurrent trusted-controller
modification and model pretraining contamination are outside this boundary.

## Public input and RDS integration

The trusted controller prepares a public decision-time view before launching the
runner. A minimal Python example is:

```python
from rds_blind_runner import project_request, run_blind

public_request = project_request(full_request, {
    "run_id": ("run_id",),
    "objective": ("task", "public_objective"),
    "base_source": ("base_source",),
    "observation": ("observations", 0, "public_summary"),
})
result = run_blind(
    request=public_request,
    worker=worker_file_spec,
    runtime=reviewed_runtime_inventory,
    public_files=reviewed_public_file_specs,
    private_roots=[private_evaluator_directory, private_labels_directory],
    output_dir=new_attempt_directory,
    timeout_seconds=30,
)
```

Each projection value is a sequence of dictionary keys/list indices identifying
one scalar leaf. Missing fields, non-finite numbers and nested container exports
are rejected. The allowlist is the controller's publication decision; it is not a
heuristic search-and-redact operation. Complex public policy exports should be
prepared and reviewed separately, then supplied as an individually pinned public
file. Do not include private labels in that policy or turn private evaluator
feedback into a public explanation accidentally.

The worker receives canonical public JSON on a sealed anonymous memfd as stdin
and at `/public/request.json`. It cannot reopen stdin to modify a host evidence
file, and the stdin descriptor reveals no caller-selected host path.
Approved public files appear at `/public/<path>`. The worker code is
`/rds/worker.py`; execution is Python `-I -S -B` in `/work`. No host path or
provider configuration is forwarded automatically. The trusted probe's host
canary paths and namespace identities are never given to the candidate.

Stdout must contain exactly one UTF-8 JSON object with these string fields:

```json
{"status":"proposed","source":"...","policy_json":"...","reason":"..."}
```

The allowed statuses are `proposed`, `no_feasible_method` and
`needs_authorization`. The latter two require empty source/policy strings. The
optional `jump_use_json` string supports the existing RDS proposal envelope.
Duplicate keys, extra fields, non-finite JSON, malformed UTF-8 and trailing output
are rejected. Policy/jump strings and returned code still require the normal RDS
validation, independent evaluation and adoption gates. A `completed` runner
result means only that a bounded, schema-valid proposal was collected.

## File inventory and command line

Every file specification has exactly:

```json
{"source":"/absolute/approved/source.py","path":"source.py","sha256":"<64 lowercase hex digits>"}
```

`source` is an absolute path to the individually approved regular file. Public
paths are relative, canonical POSIX paths; the worker's path must be `worker.py`.
Runtime paths are absolute and restricted to `/usr`, `/lib`, `/lib64`, or
`/runtime`. For example, a distro Python runtime inventory includes the exact
interpreter, its required standard-library files/extensions, dynamic loader, and
required shared libraries, each at its expected in-sandbox path. All symlinked
runtime dependencies must be materialized as individually pinned files; no
ambient library or host `/etc` access is available. A missing dependency causes a
probe refusal, never an automatic broader bind mount.

Runtime shape:

```json
{"python":"/usr/bin/python3.13","files":["<individual file specifications>"]}
```

The string above is a schematic placeholder, not a valid inventory. Build and
review a complete inventory on an approved clean installation. Do not use `ldd`
on untrusted candidate binaries. The test suite's `system_test_runtime()` helper
inventories only its trusted distro Python and is a regression fixture, not a
production runtime provenance check.

A CLI manifest contains `schema: 1`, the already projected `request`, `worker`
specification and `runtime` inventory. Optional fields are `public_files`,
`private_roots`, `timeout_seconds`, `max_output_bytes` and `memory_bytes`.

```text
python -B scripts/rds_blind_runner.py --manifest public-run.json --output-dir ../blind-attempt-001
```

The output directory must not exist, and its parent must already exist. Exit code 0 means response collection
completed; exit code 2 means refusal or worker error. Malformed manifests produce
a structured refusal before creating an attempt. A valid attempted run retains:

- `attempt.json`, the public `request.json`, and `result.json`
- `probe.stdout.bin` / `probe.stderr.bin`, including original setup failures
- `dispatch.json` only after passing canaries and before candidate launch
- `stdout.bin` / `stderr.bin`, including empty originals for an unlaunched worker

Attempt, dispatch and result records use atomic publication and fsync, including
the new attempt directory's entry in its parent. An
existing attempt directory is never overwritten or retried. A dispatch marker
without a result is unresolved evidence, not permission to repeat an expensive
model request. This standalone runner does not replace the owning RDS ledger's
attempt reservation, accounting or recovery. `worker_dispatched` records dispatch
intent; it does not prove that a second sandbox startup successfully reached the
worker. The response/process record establishes what actually returned.

## Bounds and error evidence

Public input plus exported public files is capped at 2 MiB; worker source has its
own 2-MiB cap. At most 64 public and 5,000 runtime files are accepted, with at most
128 MiB of runtime contents. Per-stream output is capped at 2 MiB by default;
overflow kills the process tree and retains the original bounded prefix, labelled
truncated. It is never trimmed into an acceptable proposal. Nonzero exits,
timeouts and invalid output similarly retain originals and have no response to
adopt.

The worker wall allowance is at most 120 seconds, default 30. The separate probe
has at most 10 seconds. Collection allows bounded termination overhead. The
runner sets CPU, address-space, file-size, file-descriptor and process-count hard
rlimits before launching bubblewrap. Address-space/CPU limits are per process;
Linux `RLIMIT_NPROC` is per real-user accounting, not an isolated cgroup quota,
and may conservatively refuse a busy host. This is not an aggregate CPU/RAM
meter or a reservation of those resources. The default address-space limit is
512 MiB. The result records measured worker/probe/total wall time; CPU, GPU and
API usage are not measured or silently substituted with wall time.

## Networked models need a separate credential boundary

This runner currently has no networked-provider adapter. Network isolation must
not be disabled to make a provider CLI work. Do not mount home directories,
credential stores, host sockets, or API keys into the worker, and do not execute
model-generated code on a credentialed transport process.

A future model integration needs a separately trusted, credentialed transport
that accepts only the approved public request, talks to explicitly authorized
provider endpoints under a bounded budget, and returns bounded response data.
That transport must not have evaluator/label access or expose a general URL,
filesystem or command proxy to the worker. Provider authentication authorization,
network/data sharing approval, RDS dispatch intent/accounting and model/version
recording are separate requirements. Existing provider read-only sandbox flags do
not establish this architecture.

## Verification and honest reporting

```text
python -B -m unittest discover -s tests -p test_rds_blind_runner.py -v
```

Tests cover scalar projection, private-root/symlink and changed-hash rejection,
fail-closed dispatch, strict output (including hostile Unicode), anonymous input
immutability, repeated filter-FD use, architecture/syscall filter instructions,
unsupported-platform refusal, real stream overflow/nonzero exit/timeout,
and the actual CLI. Successful live canaries explicitly skip when the host
cannot supply the required namespaces; a separate test verifies the real refusal
and absence of dispatch. On a designated isolation-capable Linux test host, run:

```text
RDS_REQUIRE_BLIND_ISOLATION=1 python -B -m unittest discover -s tests -p test_rds_blind_runner.py -v
```

That setting makes unavailable live isolation a failure, not a skip. Install and
configure official bubblewrap through the host's approved administration process;
these tests never relax host security or bypass a denied namespace operation.

On the development cloud host, the live API and CLI probes encountered
`bwrap: loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted`.
The candidate was refused, and successful-isolation testing was skipped. Those
results establish fail-closed behavior on that host, not a successful isolated
model trial, blinded scientific exploration, or an RDS performance improvement.
