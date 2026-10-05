# Bounded research drive and method repair

`project drive` connects the existing owned Advisor, execution kernel, history,
tool workbench and method-revision gate. An obstacle triggers another decision:
inspect originals, select a different permitted route, or request a new method
or tool proposal. Rejected proposals become feedback for a distinct authorized
repair slot. Exhausting this finite scope does not prove scientific impossibility.

```powershell
python -B scripts/rds_cli.py --root <owned-project> project drive --max-steps 8
```

The command runs in the foreground, without installing a scheduler or changing
global Codex settings, hooks, approvals or credentials. `--max-steps` limits
executions in this pass. Frozen `autonomy.max_steps` limits all registered runs
across calls, including model work. Original budget, failures and campaign
deadline survive every pass, repair, adoption and recovery.

To inspect concrete model input before dispatch, use the same controller:

```powershell
python -B scripts/rds_cli.py --root <owned-project> project drive --prepare-only --max-steps 8
```

Ordinary selected steps and recovery of already paid results continue. When the
next selected step is a repair worker, the command returns `MODEL_REQUEST_READY`
with `run_id`, the original CAS `request` reference, `parent_sha256`, frozen
`provider` identity and `provider_timeout_seconds`. It does not start that worker
or write a new provider dispatch intent. Request preparation and control work
still use the original ledger budget. Repeating preparation retains the same
request; a later `project drive` without this flag consumes that original input.
The cumulative step cap and uncertain-delivery reconciliation still apply.

## Declaration and authority

Declare `advisor_policy.autonomy` before initialization: `schema:1`, `max_steps`
(1–64), `repair_slots` (0–4) and a frozen `provider`. Optional
`controller_wall_seconds` bounds control work per pass (positive, at most 120;
default 30). A slot refers to an existing ordinary frozen route:

```json
{
  "run_id": "repair",
  "code_path": "candidate.py",
  "worker_path": "engine/rds_autonomy_worker.py",
  "response_path": "outputs/repair.json"
}
```

This fragment is not a complete contract. `code_path` must already be an
exclusive code binding in `method_evolution.code_paths`. The worker must be an
immutable bound copy of the program adapter. Its argv is
`python -B <worker_path> --run <run_id>`. Outputs are the response and companions
`.trace.jsonl`, `.stderr.bin`, `.schema.json`, `.provider.json`. The action's
sole precondition is `autonomy.<run_id>.ready eq true`. Future actions may depend
on `autonomy.<run_id>.adopted eq true`. Verified ledger events produce these
facts; imported observations cannot supply them or make them the research goal.

Production `provider` binds `kind:"codex_exec"`, a one-element argv with the
absolute installed native `codex` executable, its `executable_sha256`,
`model:"gpt-6.1-sol"`, `effort:"high"`, `service_tier:"default"`. The actual
subprocess carries these settings and structured output flags. Its explicit
`--sandbox read-only` narrows only that child's local writes. Host requirements
still apply. This is not a host-wide isolation or external-connector coverage
claim; review inherited host integrations before production use.

The explicit `fixture` provider uses immutable bound local Python code and
null model/effort/tier. It tests plumbing, never model ability or scientific
policy gain. The provider envelope contains `kind`, `argv`,
`executable_sha256`, `model`, `effort`, and `service_tier` in both modes.

## Ownership, costs and recovery

Ownership and reservation live in the existing project ledger. Another
possibly-live controller prevents a duplicate drive. A definitely dead owner's
hold is charged conservatively and committed even if no new budget remains.
Only the current process's verified controller hold can coexist with idle
method adoption; any reserved/running worker still blocks that transition.

The existing workbench retains immutable source hints and receipt diagnostics.
A CAS request binds effective parent, original goal, current source/policy and
earlier rejected methods. It also carries `evidence_excerpts`: the first 4 KiB
of each config/data/evaluator binding whose bytes still match its frozen hash,
and of each ordinary receipt's original project output. Both limits count
retained UTF-8 text bytes; inputs and outputs share one 64 KiB total, so the
last head may be shorter and is then marked `truncated`. A wrong
value that exits 0 leaves no stderr tail, so without these the model sees no
task content. They are sent to the configured provider; review them with the
request. The request is included in the normal reserved run
and original receipt. Fingerprints and original-file checks prevent replacing
it between review, reservation and launch. Ordinary method, dependency,
history, feasibility, command and resource admission remain authoritative.

The worker records dispatch intent before calling the provider and returns
UTF-8 source/policy data. The controller never imports or executes model source.
The existing revision gate validates authorized paths, source structure, parent,
registered routes, final goal producers and unchanged evaluator/data/budget/
commands. Comments, renaming or numeric tuning alone cannot establish a new
structural tool. Validation and pilots must establish useful behavior.

The exact proposal is retained before adoption. PREPARED partial copies and a
crash after ADOPTED resume that proposal without another model call or receipt
replacement. Rejections feed the next distinct declared slot. An uncertain
paid provider outcome requires reconciliation before another model dispatch;
other eligible ordinary routes can still proceed. Suggestions cannot widen
authority or remove a budget limit.

The whole UTF-8 response envelope, including worker metadata, is bounded to
2 MiB. Provider originals remain available as evidence when the envelope is
too large; they are not truncated into an adoptable proposal. Recovery of an
older successful oversized response records one controlled UNKNOWN and blocks
another paid repair slot pending reconciliation. Damage to original artifacts,
receipt identity or a validated proposal is an evidence failure requiring
handoff, rather than a rejected method that authorizes another purchase.

An old immutable adapter's settled originals may be inspected by the updated
consumer. Starting any new model attempt still requires the bound adapter to
match the running distribution. Reading paid evidence does not renew dispatch
authority.

Each pass reserves its control allowance before dispatch. Only worker cost from
the original receipt is subtracted from controller elapsed time: admission,
post-receipt Advisor/confirmation and adoption work are charged too. Release
records measured control work, refunds unused reservation and charges any
measured overrun. A crashed pass keeps its conservative allocation. Token/USD
usage remains UNKNOWN unless separately measured. Bootstrapping/orchestration
outside `drive` are separate costs in a complete research evaluation.

Admission rechecks the control allowance and current verified owner after
registration and Advisor checks, immediately before allocating a new attempt.
This also applies to previously RESERVED routes. Exhaustion retains that
reservation, allocates no attempt and settles actual control cost; it cannot
be bypassed by spending the remaining allowance during registration or review.

## Confirmation and completion

With `advisor_policy.confirmation`, a program-replayed task predicate joins the
original goal predicates in selection and history. An early candidate metric
cannot skip the frozen confirmation route. Only the composite goal can yield
`GOAL_CONFIRMED`. See [domain confirmation](domain-confirmation.md).

| Domain | Actual supported consumer | Limit |
| --- | --- | --- |
| Mathematics | Replay original exact certificate against frozen claim | Declared proposition; empirical application premises can remain unknown |
| Algorithms | Recompute integer oracle; compare original same-input baseline/candidate cost | Finite cases and one whole-run observation |
| Deep learning | Shared-data linear profile or explicit post-commit two-hidden-layer CPU MLP | Shared-data task stays UNKNOWN; future-sample MLP checks are finite, without population/secrecy claims |

The DL example ends `DOMAIN_CONFIRMATION_UNKNOWN`: current shared bindings
record its confirmation data as exposed. No threshold is lowered to claim
independent evidence. Waiting for an original attempt, yielding a bounded pass,
exhausted resources, missing originals and missing authority have distinct
explanations. A later pass recovers paid results before new work; none of these
outcomes certifies scientific impossibility.

The opt-in `--dl-profile postcommit_mlp` freezes a different declaration at
genesis. It commits a program-generated future-sample challenge after original
weights settle; it does not migrate the shared-data example's existing goal.
See the finite profile and retained validation limits in
[domain confirmation](domain-confirmation.md) and
[validation](autonomy-validation.md).

## Actual entry points

Use a new sibling workspace per frozen public engineering iteration:

```powershell
python -B examples/autonomy/run.py --workspace ../autonomy-math --domain mathematics
python -B examples/autonomy/run.py --workspace ../autonomy-algorithm --domain algorithms
# Use an existing Python environment with torch; no dependency is installed.
python -B examples/autonomy/run.py --workspace ../autonomy-dl --domain deep_learning
python -B examples/autonomy/run.py --workspace ../autonomy-mlp --domain deep_learning --dl-profile postcommit_mlp
```

These default to fixture and preserve CLI transcripts, ledger and receipts.
They check later passes do not change completed attempts. A short first pass
may yield its control allowance; the next pass continues in the same ledger.
For an approved real model call use `--provider codex_exec --codex-path
<installed-native-executable>`. Review the concrete request and applicable
external-service authorization before dispatch.

The shared hypergraph represents current dependencies and evidence. The ledger
retains authoritative events, receipts and costs. These repair/confirmation
producers and consumers do not create a second bus or scientific state store.
They preserve the separate #192 route-planner author and grant generated tools
no new command or path authority.
