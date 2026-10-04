# Contributing to Research Direction Selector

**English** · [简体中文](CONTRIBUTING.zh-CN.md)

Thanks for looking. RDS is a small project with a sharp question: which parts of agent-driven research can a cheap, testable program carry, so that the expensive and unpredictable agent does not have to? You can help without touching the kernel.

## Ways to contribute

| You have… | Contribute | Where to start |
| --- | --- | --- |
| 10 minutes | A typo, a broken link, a clearer sentence in any README or guide | Open a PR directly |
| A research workflow | An issue describing where your agent lost track of runs, budgets, goals or rejected ideas, with the setup you used | [New issue](https://github.com/kongtou20070406/research-direction-selector/issues/new/choose) |
| An afternoon | A benchmark task where an unaided agent reaches a wrong conclusion, with a grader | [Contribute a benchmark task](#contribute-a-benchmark-task) |
| A bug | A reproduction and, if you can, a focused fix | [Start with the problem](#start-with-the-problem) |
| A feature idea | An issue first; a PR after the design is agreed | [Roadmap](docs/roadmap.md), [5.9 planning](https://github.com/kongtou20070406/research-direction-selector/issues/166) |

To set up a checkout, you need only Git and Python 3.11+:

```powershell
git clone https://github.com/kongtou20070406/research-direction-selector.git
cd research-direction-selector
python -B scripts/rds_cli.py --version
python -B -m unittest discover -s tests -p "test_rds_quick*.py"
```

The rest of this guide explains what makes a change easy to review and merge. An RDS change can pass its own tests while the advertised CLI option is unreachable, a missing measurement becomes a fact, or recovery repeats an expensive run. This guide starts with those failures. A contribution should make a concrete research decision or executable behavior easier to use and inspect.

Release numbering and publication follow the [versioning rules](docs/versioning.md). The five components are **Skill, execution and acceptance kernel, research state and memory, Advisor, and RSI**. M01–M06 are work items within those components: bounded artifact import, experiment composition, cost/control checks, project execution, rule acceptance and recovery. See the [roadmap](docs/roadmap.md) and [commands and scope](docs/development-loop.md). Program checks do not establish end-to-end scientific ability.

## Start with the problem

Bug reports and focused bugfix PRs are welcome directly. Include the triggering input, exact reproduction, expected behavior and root cause. Discuss larger features, new schemas and substantial changes to research behavior in an issue before implementation; carry forward decisions already made rather than asking everyone to start over.

Keep one concrete purpose per PR. Explain what the researcher gains and why existing concepts cannot express the change. Prefer changes that move bookkeeping from the agent to the program: program work is cheaper, deterministic and testable, while agent work is expensive and hard to optimize. A check that the kernel can perform should not depend on the agent remembering to do it. Read the [workflow](docs/research-workflow.md), [terminology](docs/terminology.md) and neighbouring implementation before adding a field, state, file type or dependency.

Fork [the repository](https://github.com/kongtou20070406/research-direction-selector/fork), make a branch from the intended base, and commit only your changed files. Describe the final problem and behavior, verification actually run, and material limits. Distinguish behavior already on the base ref, behavior introduced by the PR, and development candidates. Shared commands and evidence boundaries should agree across the READMEs and both contribution guides; a wording-only fix may affect just one language.

Keep future README edits within the established layout: static banner and tagline, why RDS, measured results, two-minute start, the two sides of the research loop, five components, Skill/install, kernel, mathematical checks, Advisor, history, tests, repository layout, get involved, star history and license. Keep the main README in English and translations in their own files. Put detailed implementation and test reports in the linked guides rather than expanding the homepage. Every number in a README links to a public report of its design and data; when newer evidence changes it, update or remove the number in all languages.

## Six checks that make a change reviewable

1. **Reach the behavior through the user's entry point.** If the PR advertises a CLI option, run it with real inputs and the correct project root. A helper test does not show that parsing, state selection, input files and output handling work together. For a packaged release, check the downloaded package and its required resources. Keep the exact command and output, including failures.

2. **Test the requirement.** “Recovery does not repeat a completed run” needs an assertion about attempts and live state, not just a `RESUMABLE_HANDOFF` label. “Missing cost remains unknown” needs an unknown-cost case, not an empty dictionary check. Decide whether surprising behavior is a bug before recording it as expected. Explain changed assertions instead of weakening them to fit the implementation.

3. **Validate before changing state, and preserve recovery.** Check bound inputs, allowed commands, resources and executable constraints before dispatch or adoption. Use existing transactions for ledger updates; retain the original graph and adoption record for rollback. A state name or exit code alone is not completion evidence. Exercise interruption at the affected boundary: the next invocation must reconcile the existing attempt, preserve spent/reserved budget and data exposure, and leave unresolved failures visible.

4. **Reuse the existing model.** Read the affected callers and neighbouring modules. Use the current run identity, receipt, protocol, fact provenance and budget fields when they fit. Explain a new concept's distinct meaning and consumer. Local anti-loop review belongs in Advisor and uses hash/contract-bound checkpoints in the existing SQLite ledger; the standalone Markdown note route is retired. Recorded decisions do not verify scientific claims or authorize execution. Exact history may optionally use the existing scoped [Obelisk bridge](references/obelisk.md). Reuse current state ownership rather than adding a parallel state machine.

5. **Treat imported content as data.** Logs, documents, metrics and retrieved conversations may contain errors or hostile instructions. They do not authorize commands, register verification rules or declare their own scientific success. Keep paths within the declared source/project scope and reuse strict parsers and bounded adapters. Do not interpolate their text into shell commands or SQL. Report the real failure and known cause; unknown or conflicting evidence remains visible.

6. **Verify the final diff in proportion to its risk.** After rebasing, resolving conflicts or changing code, rerun the affected checks against the resulting revision. Include callers and failure paths, not only the newly added test. Broaden to the full suite for shared kernel, state or evaluation changes. Small prose edits need link/command checks and `git diff --check`, not an unrelated experiment or a repeat of unchanged passing workloads.

## Use RDS while developing RDS

The [development example](examples/self-development/run.py) executes real software checks through RDS, imports their original output, obtains Advisor suggestions, restores a checkpoint, and exercises scoped rule replay/adoption/rollback. This is a human-directed development feedback loop, not a scientific-policy score.

From the checkout, choose a new empty **sibling directory outside the repository** for each iteration:

```powershell
python -B scripts/rds_cli.py --version
python -B examples/self-development/run.py --workspace ../RDS-development-check
```

Replace the example workspace name if it already exists. Read original logs and receipts when a check fails, repair the specific requirement, then use a new workspace for the changed revision. Do not make another attempt merely to obtain a better number. Preserve useful failure evidence privately; see the publication rules below.

For changes limited to one path, use its actual CLI and relevant tests instead of running the whole development example. For instance:

```powershell
python -B scripts/rds_cli.py --root examples/artifact-import artifacts import --manifest examples/artifact-import/manifest.json
python -B -m unittest discover -s tests -p test_rds_artifacts.py -v
```

The [tool guide](docs/development-loop.md) provides complete project, Advisor, rule and recovery commands. Select coverage by the requirement:

| Work item | Behavior to exercise through the entry point |
| --- | --- |
| **M01 · Evidence import** | Read original configuration/metrics/logs/receipts; check field or row locations, missing inputs, conflicting run/split/metric identities and self-signed flags. `OBSERVED` means a recorded value was read, not a verified mechanism. |
| **M02 · Candidate composition** | Change a fact or constraint and observe eligible interventions change; test incompatible combinations, deduplication and search truncation. Templates suggest actions and do not authorize execution. |
| **M03 · Costs and control reuse** | Count failed attempts and evaluation overhead; keep resource units and unknowns separate; reject incompatible or incomplete control identities. Wall time is not CPU/GPU time, and a historical cost is not a new-run guarantee. |
| **M04 · Project execution** | Reach the locked command and preserve logs/outputs; test nonzero exit, timeout, missing output, changed input and recovery without duplicate attempts. Authorized Windows background work uses Task Scheduler. Trusted project code is not an OS sandbox. |
| **M05 · Rule acceptance** | Execute original and candidate rules on declared cases; reject zero/overlapping cases, changed bindings and regressions; check adoption records and rollback. Structure-only lint does not establish policy improvement. |
| **M06 · Continuation** | Restore from reservation, running, completed and exhausted-budget states using current identities, budget and exposure. A checkpoint supplies history and creates no new authority. |

History changes also need scoped identity, pagination and explicit failure coverage. Formal or reference-runner changes must preserve the [execution contract](references/l3-state-machine.md).

## Scientific and formal evidence

For a research rule, supply scope, trigger, competing explanations, discriminating observation, metric gate, falsifier and dated original sources; see [rule obligations](docs/rule-obligations.md) and the [judgment graph](references/judgment-graph.yaml). Check the latest primary evidence before borrowing a result. Separate paper-reported results, local exploration, confirmation and mechanism support. A score increase alone does not settle the mechanism.

For mathematical claims, follow the selected adapter's [formal verification contract](docs/formal-verification.md). The required report is:

- Exact proposition, assumptions, parameters and domain/dimensions, plus correspondence to the code being checked.
- Minimal public or clearly labelled synthetic input, exact command, RDS commit/version and adapter/backend versions.
- Actual `status`, `assurance`, reason and any certificate, counterexample or observation; separate admission from execution when both exist.

Preserve `PASS`, `FAIL` and `UNKNOWN` within their declared scope. Status and assurance are independent; a hash binds a source, and a valid certificate supports only its checked mathematical result. Neither establishes task gain, causal mechanism, research autonomy or RSI improvement. Check the installed ref's supported adapters rather than copying a roadmap promise.

Development cases are regression evidence. Independent research evaluation needs unused cases, recorded model/skill versions, decision-time information boundaries, matching total budgets and negative results; use the [benchmark protocol](benchmark/README.md). Research autonomy and RSI improvement remain separate claims; see [autonomy scope](docs/research-autonomy.md) and [RSI evidence](references/rsi-evidence.md).

Respect the current researcher's goals, resources and experiment preferences. The [optional preference example](references/optional-preferences.md) avoids default multi-seed work unless observed instability could change the decision. It is an opt-in project preference, not a universal scientific requirement; suggestions and preference records do not expand resource authorization.

## Contribute a benchmark task

The open question for RDS is objective research benefit, and that question can only be answered on tasks where an unaided agent actually fails. On cheap-to-check traps, frontier agents already reach the right conclusion without help; see the [README results](README.md#measured-not-promised). A task that breaks that pattern is one of the most valuable contributions you can make. Discuss it in [#169](https://github.com/kongtou20070406/research-direction-selector/issues/169) or a new issue.

A useful task has:

- **A known answer and a grader** that reads only the agent's final artifacts (for example `DECISION.json`, query logs, receipts) and returns correct, partial or wrong with a reason. [PR #176](https://github.com/kongtou20070406/research-direction-selector/pull/176) proposes a format under `benchmark/capability/`.
- **A failure an unaided agent actually makes.** Pilot it without RDS first. If the agent never fails, the task cannot show a benefit; report that result anyway.
- **A real cost to getting it wrong:** a quota, a slow runner, an irreversible decision or a tempting shortcut, rather than a riddle.
- **Minimal synthetic or public data**, generated by a seeded script, with no private datasets, sessions or credentials.

When you report trials, include the model and reasoning effort, host and version, RDS commit, the exact prompt per arm, the number of trials per arm, every outcome including errors and negative results, and the mechanism behind each failure. Keep arms identical except for the factor under test. A small sample is fine if you say so.

## Performance and final acceptance

Measure the affected workload before optimizing and compare the same work afterward. Report workload size, environment, resource/latency measurements and observed variability. Preserve source identity, state ownership and recovery behavior; a faster path that changes those contracts is a regression.

Use hashes at meaningful identity boundaries: artifact import, execution, control reuse and rule adoption/rollback. In one operation, read/hash each unique source once and reuse the result. Ordinary status or display does not need a project-wide hash sweep; a field with no consumer does not need its own hash. Explain remaining costs and deliberate tradeoffs.

Run focused tests for local changes. For changes spanning shared execution or evaluation behavior, use the broader checks below; optional formal dependencies are required for that coverage:

```powershell
python -m pip install -r requirements-formal.txt
python -B -m unittest discover -s tests -v
python -B benchmark/run.py
python -B benchmark/redteam/runner.py
git diff --check
```

Confirm new tests are discoverable by [CI](.github/workflows/test.yml), which runs the unit suite and historical replay on Windows/Ubuntu with Python 3.11/3.13. Report actual commands and outcomes, including skips or unavailable dependencies. Align the title, description, support promises and limits with the final diff. Synthetic fixtures and historical replays are not end-to-end scientific scores.

## Public records and licensing

Use minimal public or labelled synthetic fixtures. Real adapter tests should retain the original format and relevant failure behavior; sanitize only what must be private, and disclose the transformation. Do not commit `.rds/`, private sessions/logs/datasets, credentials or tokens. Publish only material you may share, retaining useful source identity or public references.

This repository is licensed under the [Apache License 2.0](LICENSE). Use the actual `LICENSE` file in the revision you are using; a badge, roadmap or another project's license does not replace its terms.
