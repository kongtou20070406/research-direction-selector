# Fixed-campaign host broker

`scripts/rds_host_broker.py` is an opt-in stdio MCP adapter over the original
campaign ledger and program-owned Advisor. It exposes four structured tools:

| Tool | Arguments | Effect |
| --- | --- | --- |
| `rds_status` | none | Read campaign identity, original budget and run identities |
| `rds_next` | none | Collect native evidence and return the current selected run/hash |
| `rds_execute_selected` | `expected_run_id`, `expected_manifest_sha256` | Admit that exact selected manifest through native RDS; an existing attempt is observed/recovered |
| `rds_recover` | same identity fields | Recover the same existing run without rerunning it |

The operator supplies an absolute project root and an existing campaign binding
pointer at startup. The project must have a program-owned `advisor_policy`.
The broker does not initialize, bind, migrate or reset a project. Model calls
cannot supply commands, manifests, roots, environment variables, output paths,
new policies, imports, background scheduling or budgets. Extra fields fail.

```text
python -B /trusted/rds/scripts/rds_host_broker.py --root /campaign/project --binding /campaign/.rds-campaign.json
```

This is an operator configuration example, not an installation command. Use the
actual absolute Python, script, project and marker paths on Windows. Register
this executable and its fixed arguments as an MCP stdio server in the intended
host. Keep the interpreter, Python import environment, code, config, policy,
ledger, receipts and marker under the trusted operator's control. Launch from a
trusted directory. No host configuration or permissions are changed by this
repository feature. Stdio possession authorizes the four tools within the
existing policy; there is no network listener or authentication service.

## Native state and replay

The broker verifies the marker's native genesis and nonce event, pins the entire
campaign identity, and checks it on every operation. It also checks any inherited
`RDS_CAMPAIGN_BINDING`. A missing, conflicting or replaced identity fails instead
of falling back to an unbound ledger. Native admission rechecks selection,
original inputs, live budget and dependencies; an additional callback checks the
pinned campaign under the admission transaction. There is no second ledger,
scientific state machine, receipt format, balance or capability-ticket database.

Call `rds_next`, then copy its `selection` object unchanged to
`rds_execute_selected`. After a timeout, disconnect or ambiguous response,
retry/recover **that same run/hash**. Calling `rds_next` and executing a different
selection is a new action, not a retry. Completed attempts return the original
receipt; active or uncertain attempts retain native recovery status. Recovery
never reruns the job. An unstarted reservation can still execute when it remains
the current selection; recovery alone does not dispatch it. Concurrent clients
may receive a native admission conflict and must inspect/recover the same run.

Failed work retains its original failed receipt and charged costs. `isError=false`
means the broker returned an operation result, not that the run or research
succeeded. Read native `run_status`, assessment and Advisor information. A broker
error after dispatch can mean the action finished but its observation failed.
Never infer non-execution from a transport error. Human intervention uses the
original ledger's recovery procedures, not deleting state or changing roots.

The adapter implements newline-delimited UTF-8 JSON-RPC, MCP initialization,
`ping`, `tools/list` and `tools/call` for protocol `2025-11-25`. It rejects duplicate
JSON keys, non-finite numbers, batches, unknown arguments, malformed messages and
input over 64 KiB. Tool notifications never dispatch. Responses above 4 MiB return
an explicit uncertainty error without undoing execution. There is no streaming,
client-requested cancellation, background task API, resource server or sampling.
Disconnect is not cancellation. Native workload timeouts remain authoritative.
See the [MCP stdio specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

## Preventing action after a model jailbreak

The security boundary must remain outside the model. A malicious prompt can
change what a model asks for; it cannot grant an operation absent from this
broker's API. This is only an API admission property. To make it an execution
boundary, the operator must provide the following properties independently:

1. The model has no alternate shell, file-write, subprocess, remote execution,
   network credential or inherited interactive-session route to the workload.
2. The model and the executed workload cannot change broker code, its startup
   configuration, policy, canonical ledger, receipt storage or binding pointer.
   Same-user unrestricted Python or shell does not satisfy this requirement.
3. Untrusted workloads run in an independently restricted execution environment
   with only their approved inputs, output area and bounded resources. Freezing
   an arbitrary script's hash does not make that script safe or sandbox it.
4. Removing or breaking the broker leaves the model without execution authority.
   An operator can repair service health without clearing prior costs or trials.

This module does **not** implement those OS/container/VM controls, protect against
same-user SQL edits or backups being rolled back, or promise prevention of all
jailbreaks. It cannot constrain the model's final natural-language answer. It
does not certify scientific benefit, complete resource isolation, workload
network egress control, or unavailable/unknown host integrations.

## Optional Codex PreToolUse filter

`scripts/rds_broker_hook.py` can supplement a scoped host configuration. Supply
each exact, host-observed broker MCP tool name using repeated `--allow-tool`
arguments. For example, if the configured host reports this exact name:

```text
python -B /trusted/rds/scripts/rds_broker_hook.py --allow-tool mcp__rds_broker__rds_next
```

Configure the host's `PreToolUse` matcher for all supported tools and list all
four actual broker names. The handler returns `{}` for an exact match, deferring
to existing permissions; it never returns an approval. Other names and malformed
input produce the documented `permissionDecision=deny`. A bad operator allowlist
uses blocking exit code 2. The handler never dispatches research itself. It must
run as a trusted command hook with bounded input/timeout; do not expose it as a
model-editable permission service. This file is not installed automatically.

Coverage remains **PARTIAL / host integration UNVERIFIED** until actual host
callback receipts demonstrate coverage on that host/version. According to the
[Codex hook contract](https://learn.chatgpt.com/docs/hooks), hosted tools and some
specialized routes can bypass hooks, `write_stdin` does not trigger a new
`PreToolUse`, and hook errors/timeouts or unsupported outputs can fail without
blocking. A unit test feeding `write_stdin` to this handler proves only the
handler's decision if invoked, not that Codex invokes it. Names inside code-mode
requests must be checked by the host for their nested actual tools. Existing
interactive sessions must be treated as an alternate execution capability.

Tests in `test_rds_host_broker.py` start a real stdio process and inspect native
attempts, receipts and costs for tiny synthetic CPU fixtures. Hook tests execute
the handler as a subprocess. They do not run a model jailbreak benchmark or
demonstrate deployed containment. The existing [host hook](host-hook.md) is a
separate native launch validation feature with its own coverage limits.
