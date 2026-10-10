# Frozen execution policy

An existing project contract can opt into a bounded execution policy before initialization:

```json
"execution_policy": {"schema": 1, "max_attempts": 1}
```

`max_attempts` is an integer in 1..32. The contract hash freezes this setting alongside the input bindings, allowed commands and budget. Contexts, choices and run manifests cannot remove it or raise its limit. Projects without this setting keep their existing behavior.

Include this field in the original contract passed to the existing entry point:

```text
python -B scripts/rds_cli.py --root path/to/project project init --mode quick --contract path/to/contract.json
```

The usual `project create` and `project execute` commands then enforce it. Quick `exec` from that configured source root also consumes its policy without requiring research context. An already initialized contract cannot be retrofitted with this field; configure an explicit new project instead.

The policy compares the declared command and input contents within its owner ledger. Bound file arguments become content/role identities; renaming a script with identical bound bytes does not create a new route. Run names, candidate/decision IDs, descriptions, output destinations, timeouts and allowances do not reset the counter. Reviewed quick requests retain Advisor's existing structured action fingerprint as scope metadata, while the goal guard remains separately enforced. Editing an action's target or operation alone cannot reset an identical execution request. An actual changed bound input, command or checked native objective can identify different work; a reported fact edit alone does not. Native objectives remain frozen and require an explicitly initialized new project to change. Authorized replication of an unchanged successful check requires an explicitly configured new policy-bearing contract/project, rather than a renamed proposal.

Project registration checks existing runs while holding the same SQLite write transaction that reserves resources. An existing reserved, running or successful route blocks another registration and identifies the run to inspect. Executing an already dispatched run observes its owned state or retained receipt. Successful reuse checks receipt integrity, its matching completion event, original input bindings and current declared output hashes. Missing or changed evidence causes refusal, rather than another automatic attempt.

Quick execution checks the selected parent ledger under the same transaction that writes `EXTERNAL_RUN_ALLOWANCE` and charges its wall allowance. The event binds the route, complete request, frozen contract and policy. The child inherits the policy and registration checks the executor hash observed before charging. Even same-name recovery requires the parent's matching allowance for that exact child location and request; a copied child alone cannot supply ownership. A duplicate live or successful request observes the retained child without a new allowance or launch. Validated failed attempts remain charged and count toward `max_attempts`; a configured additional attempt needs a new job name. A charge followed by a missing or incomplete child remains consumed and blocks another launch. Recover the existing job or inspect the retained state; the controller does not invent a refund. Checkpoint restoration uses the current parent budget.

For an exact pending request interrupted before any attempt or receipt, recovery
preserves the partial files in `.rds/quick-partials`. Its old operational `.rds`
directory is stored as `.rds.retained`, with every original file byte intact, so
recursive campaign discovery cannot treat that forensic snapshot as a live
project. The recovered job stays at its original location and reuses its
original allowance and choice. An interruption while moving the retired
snapshot can resume that same pending request; it does not authorize replacing
started or completed work.

If the quick source root itself owns a configured project policy, quick applies that owner even without research context. A different ledger cannot replace it. A policy in another ledger applies when that ledger owns the request; a standalone command from an unrelated, unconfigured root does not discover arbitrary project ledgers. Global host command interception is outside this implementation.

This is exact structured comparison, not a detector for every semantic rewrite. Declared content/role normalization intentionally ignores file names; relocation, dynamic imports, environment reads and other hidden dependencies can still change program semantics. Bind relevant inputs explicitly. Allowed code remains trusted local code; this is not an OS sandbox. A finite attempt limit refuses further spending under the configured agreement. It does not establish scientific impossibility, convergence, task gain or a method's superiority.

Regression coverage: [project tests](../tests/test_rds_project.py) and [real quick CLI tests](../tests/test_rds_quick.py) check refusal before launch/reservation/charge, concurrent requests, renamed identities/files, changed input bytes, verified output reuse and the charged crash window.
