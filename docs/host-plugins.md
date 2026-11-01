# Conversation plugins: Claude Code, Codex and OMP

The plugins add a short invocation indication, such as `RDS | advise: running`.
They use the same Skill and Python kernel as a normal checkout. They do not run
experiments, change permissions, or create a research ledger to refresh a display.
The [execution admission hook](host-hook.md) is a separate feature.

## Build and load

Prerequisites: Git and Python 3.11+ available as `python`; a supported host.
From a Git checkout, build into a **new directory outside the checkout**:

```text
python -B scripts/build_plugins.py --output ../rds-host-packages
```

The builder uses files in the Git index and their current working-tree contents.
Developers must stage new package files first. Untracked files, private `.rds/`
state, caches and user configuration are excluded. Existing output is never
overwritten. This is a local build, not a release or an installation.

Each host gets an independent, relocatable package containing
`skills/research-direction-selector/SKILL.md`, its Python scripts and resources.
There are no symlinks back to the checkout and no copied user configuration.
Separate packages prevent OMP from also loading the Claude lifecycle hooks.

### Claude Code

```text
claude plugin validate ../rds-host-packages/claude/research-direction-selector
claude --plugin-dir ../rds-host-packages/claude/research-direction-selector
```

For persistent discovery, add `../rds-host-packages/claude` as a local plugin
marketplace, then install `research-direction-selector@rds-local` in Claude Code.
The package uses `.claude-plugin/plugin.json`, a standard `skills/` directory,
and its explicitly referenced `integrations/hooks.json`. It intentionally does
not also create the default `hooks/hooks.json`, which would duplicate hooks.

### Codex

```text
codex plugin marketplace add ../rds-host-packages/codex
codex plugin add research-direction-selector@rds-local
```

The package uses the native `.codex-plugin/plugin.json` manifest. Codex 0.160.0
loads Skills but skips bundled hooks when a portable root `plugin.json` is
present, including hooks declared in its `com.openai` extension. The native-only
package exposes both resources; do not add a portable root manifest to this
package. This was confirmed by an isolated native loader comparison and the
[0.160.0 loader](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/core-plugins/src/loader.rs#L952-L962).
The generated local marketplace is `.agents/plugins/marketplace.json`. In the
desktop app, restart and select **RDS local** in Plugins to install it.

Installation does not trust hooks. Review the current definitions through the
host's hooks controls (`/hooks` in Codex CLI); untrusted/disabled hooks do not
run. This package never changes that decision. A later package edit can require
another review. Both hook commands read the host-provided plugin-root environment
variable in Python so installation paths never become interpolated shell code.

### OMP (oh-my-pi)

```text
omp --extension ../rds-host-packages/omp/research-direction-selector
```

Alternatively, `omp plugin link ../rds-host-packages/omp/research-direction-selector`
registers the local package. OMP loads `package.json`'s `omp.extensions` entry and
the sibling Skill. The extension observes native tool execution events and calls
`ctx.ui.setStatus` with one short line. It keeps per-session invocation IDs so
concurrent calls and session changes do not leave an old “running” indication.
It uses Python only for the shared bounded invocation recognizer; `RDS_PYTHON`
can select a Python executable if `python` is not on the host's PATH.

OMP also handles `tool_result` for bash and `before_agent_start` for reminders.
The result handler supplies `additionalContext`; before a prompt the extension
supplies a hidden custom message. Exit status comes from structured details or
`isError`. Calls are serialized and deduplicated by tool call ID; the footer's
completion check is read-only. Helper failures are silent and the timeout is
three seconds. The project must already contain a valid `.rds` ledger.

Claude Code and OMP reminder delivery passed real host trials. Codex has package
and protocol tests only; real delivery has not been verified. Codex registers
`UserPromptSubmit` and shell `PostToolUse` using the documented
[`hookSpecificOutput.additionalContext` protocol](https://developers.openai.com/codex/hooks).
The user must trust the hooks in `/hooks` before they can run.

Automatic attempts record only a route name and outcome: script file names,
Python module names (`python -m X`), `pytest`, `uv run`, `poetry run`, and
`npm test` / `pnpm test`. Arguments and tool output are not recorded. Three
attempts without a goal yield the lowest-priority `missing_goal` reminder; it
asks for a goal and expected result. A goal removes the condition. Existing
answer/dismiss and reminder cooldown rules still apply.

## What the line means

- Loading a plugin or reading its Skill is not a kernel invocation.
- Claude/Codex `PreToolUse` says **pending** (about to call), since execution can
  still be refused. OMP's actual execution-start event says **running**.
- A structured zero exit code says **returned** (the CLI call finished); nonzero
  exit says **nonzero exit**, and a host error says **error**. For example, formal
  verification uses nonzero exits for checked FAIL and UNKNOWN, not just software
  errors. None of these invocation labels is a scientific verdict.
- Missing structured completion data says **result unknown**. Background
  jobs are not declared finished just because their dispatch command returned.
- The Skill asks the agent to show a short line at meaningful changes. Hooks
  supply context; they cannot guarantee that a model follows the wording. OMP
  additionally renders a native footer in UI modes that support `setStatus`.

Only direct `python`, `python3` or `py -3` calls to **this installed copy's**
`scripts/rds_cli.py` are automatically recognized. Absolute paths and quoted
paths with spaces work. `echo`, grep, other files named `rds_cli.py`, `python -c`,
shell programs with `cd`/pipelines, or JavaScript orchestrators such as
`functions.exec` are deliberately not interpreted as invocations. For those
wrappers the Skill must report the actual tool result; absence of a hook line is
not evidence that RDS was unused. Aliases can display the generic `CLI` label.

Hook input is bounded; malformed input, timeouts and display failures add no
authority or research-state changes. Hooks never copy raw commands, prompts,
logs, paths or retrieved content into their additional instructions.

## Session-start status for the person

The session switch is off by default, so a `SessionStart` hook would otherwise
be invisible: the person would have no way to tell whether RDS was participating
in the conversation. Two hosts have a documented field that renders text to the
**person rather than the model**, and the hook uses only those:

| Host | Channel | Evidence (observed 2026-10-11) | State |
| --- | --- | --- | --- |
| Claude Code | top-level `systemMessage`, "Warning message shown to the user"; `SessionStart` is context-only and is not in the dropped-field list | [Claude hooks](https://code.claude.com/docs/en/hooks) | implemented |
| Codex | top-level `systemMessage`, "surfaced as a warning in the UI or event stream"; `SessionStart` is a documented event for shared output fields | [Codex hooks](https://developers.openai.com/codex/hooks) | implemented |
| OMP | `ctx.ui.setStatus("rds", text)` exists in the extension API, but display was not observed | [OMP extensions](https://github.com/can1357/oh-my-pi/blob/main/docs/extensions.md) | UNKNOWN |

The emitted object is a bare `{"systemMessage": ...}` with exactly one field.
This matters for Codex: a hook run that returns unsupported fields is marked
failed, so `continue`, `stopReason` and `suppressOutput` are never added, and no
model-facing `additionalContext` is produced on this event. The text is the
same short ASCII line on both verified host surfaces:

```text
RDS | session not enabled (default off); hook context not injected
```

This is an operator surface, not model context and not a permission decision. An
enabled-session path does not exist in this checkout yet, so "not enabled" is
the truthful state rather than a placeholder. A future enablement change must
keep the field set to one supported key and must not turn this line into a
claim that RDS was used. The context claim applies only to this hook; host Skill
loading and automatic application remain UNKNOWN and are not prevented here.

## Using Advisor before a choice

Calling Advisor on one preselected direction does not establish a comparison.
The Skill now keeps this distinction in the entry point: for a material choice,
use serious available alternatives and a decision-changing observation, inspect
`selection_review`, and identify missing comparisons before claiming a route
was selected by RDS. A fixed task or one scoped proof needs no invented rivals.
This is behavioral guidance, not a claim that a hook can judge scientific depth.

At this revision, saved dependency maps require `advise --saved-dependencies`;
the plugin does not silently change the kernel's selection/admission policy.
See [agent entry](agent-entry.md) for dependency binding and incomplete results.

## Verification and limits

The Python tests build all packages, relocate them, invoke the packaged CLI and
Advisor resources, and run the manifest hook commands using synthetic host
payloads. OMP's Node tests exercise event ordering, errors and session cleanup.
These are package/runtime regression checks, not an evaluation of a model's
research decisions or proof that every host UI displays context identically.

Native checks on 2026-10-03 used isolated configuration without model turns:

| Host | Observed result | Boundary |
| --- | --- | --- |
| Claude Code 2.1.205 | `plugin validate` accepted the manifest | No live lifecycle or model display test |
| Codex 0.160.0 | Plugin installed/enabled; `skills/list` found the Skill; `hooks/list` found all three native-manifest hooks as `source: plugin` | Hooks remained `untrusted`; discovery does not prove execution or model display |
| OMP 18.4.3 | Isolated RPC loaded the native extension | No model turn or interactive footer display test |

The Codex comparison first returned zero hooks with the portable root manifest;
removing only that manifest exposed all three hooks while retaining the Skill.
No hook trust setting was changed. Normal host trust and enablement still apply.

```text
python -B -m unittest discover -s tests -p test_rds_conversation_hook.py -v
python -B -m unittest discover -s tests -p test_rds_plugin_package.py -v
node --test tests/test_omp_status.mjs
```

Upstream contracts checked on 2026-10-03:
[Claude plugin manifest](https://code.claude.com/docs/en/plugins-reference),
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Codex packaging](https://developers.openai.com/plugins/build/plugins),
[Codex hooks](https://learn.chatgpt.com/docs/hooks), and
[OMP extensions](https://github.com/can1357/oh-my-pi/blob/main/docs/extensions.md).

Re-checked on 2026-10-11: the Codex hooks page is reachable at
`https://developers.openai.com/codex/hooks` (plain HTTP clients without a user
agent can get 403), and it serves byte-identical content to
`learn.chatgpt.com/docs/hooks`, so the two URLs are one source, not two.
