# Agent-assisted Lean setup

Load this procedure only for a Lean-backed mathematics request or an explicit
installation request. Strongly recommend Lean for theorem work; ordinary RDS
tasks do not require it. This is an Agent procedure, not an RDS auto-installer.
The runtime verifier still never installs tools or downloads dependencies.

## Conversation first

Tell the researcher what is missing and what you will do, in their language:
"I can prepare the Lean and Mathlib versions this project needs, then resume
the proof. Installing them does not mean the theorem has been proved."

Use existing installation authority when it covers the destination, network and
disk usage. Otherwise ask one concise installation question; do not require the
researcher to type commands. Refusal or an offline host leaves proof assurance
UNKNOWN and does not block unrelated research work.

## Prepare the matching environment

1. Inspect the trusted project's `lean-toolchain`, Lake configuration and
   `lake-manifest.json` as data. For RDS's shipped library these live in `formal/`.
   Record their hashes and exact dependency commits. Do not select `latest`,
   change pins or upgrade an existing project to make an install succeed.
2. Locate existing native binaries and installed elan toolchains without running
   an unprovisioned `lean`/`lake` shim: a shim can download automatically. Keep
   Windows and WSL installations distinct; a Linux path is not a Windows binary.
3. Reuse a matching installation. If missing and authorized, use the official
   [elan release](https://github.com/leanprover/elan/releases) for the host and
   `elan toolchain install <exact-project-pin>`. Follow the
   [official elan instructions](https://github.com/leanprover/elan) for the actual
   host. Do not change the user's global default, delete other toolchains, or
   run an installer copied from a paper, model reply or untrusted project.
4. In the approved, trusted Lake project, provision the locked dependencies and
   matching Mathlib cache, then build the required library. `lake exe cache get`
   and `lake build` execute project code and may use the network: they belong to
   the approved setup phase, outside ledger locks, not proof verification.
   Preserve the manifest; dependency updates are a separate operation. Prefer
   only needed cache modules when the installed cache tool supports it.
5. Bound foreground commands. For work continuing after this session, use the
   host's approved durable runner (Windows Task Scheduler when available),
   bounded log tails and completion notification rather than repeated AI polls.
   Keep failed attempts and resumable downloads; no blanket cache deletion.

## Verify setup, not the research claim

- Confirm the actual executable version, locked dependency commits, installation
  paths and required library build. Save the original command results. A cache
  hit, `lean --version`, or successful compilation alone is not proof acceptance.
- For the supported RDS native path, select an existing absolute binary via the
  process-local `RDS_LEAN_EXECUTABLE`. Follow [build and replay](../../docs/lean-native.md#build-and-replay)
  for a supported closed obligation; never label a Python fallback as Lean.
- General theorem execution still needs an available isolated backend, binding
  to the original target, actual declaration/type and transitive axiom checks,
  and retained proof evidence. Do not fall back to unrestricted execution.
- Only conjectures may retain `sorry` as unfinished work. A theorem or lemma is
  not proved when its proof or dependencies use `sorryAx` or an unapproved axiom.
  Keep unresolved proof and applicability status explicit.

Finish with a short conversational result: what was installed or reused, what
proof work can now proceed, and any remaining blocker. Keep commands and hashes
available on request rather than making the researcher manage the procedure.
