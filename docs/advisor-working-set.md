# Current research working set

`advise --working-set --brief` gives a policy-owned Agent a compact continuation
view. Advisor still decides from the complete frozen policy and ledger. The
view projects the **final** result after feasibility and evidence/selection
checks; execution still rechecks owned admission at registration and launch.
The view does not rank experiments or propose scientific explanations itself.
Projects without `advisor_policy` must use their existing direction-search
interface. The output option cannot accept caller facts, a graph or a choice.

```powershell
python -B scripts/rds_cli.py --root ../owned-advisor-demo advise --working-set --brief
```

The [public CPU workflow](program-owned-advisor.md#run-the-public-cpu-example)
contains a complete setup and continuation sequence. A second `advise` reads
the same attempt and budget; use `project advance` only for the selected permitted
run. A receipt's successful process status can coexist with a failed goal.

| Field | Consumer meaning |
| --- | --- |
| `scope` | Frozen decision/goal revision, contract, structure scope, dependency snapshot and owned report fingerprint. Scope identity and collection freshness are separate. |
| `goals` | Declared predicates, TRUE/FALSE/UNKNOWN comparison and the program-collected fact with its original hash/location. Includes effective confirmation predicates when configured. |
| `selection` | Final owned `selected_run`, `next_move`, status and selection basis. No new selector and no extra permission. |
| `shadow_plan` | Bounded global milestone/alternative/jump summary and comparison with the original selection. Expand with `project plan --shadow`; its heuristic never changes execution admission. |
| `budget` | Current caps, measured spending, conservative charges, reservations and remaining resources, with units. Reading the projection does not charge another structure operation. |
| `unresolved_hypotheses` | Same-scope pending or UNKNOWN proposals, executable discriminator if available, exact hypothesis identity and existing route constraint. These are declarations, not accepted explanations. |
| `unresolved_dependencies` | Existing unsupported dependency nodes and sources; these are not automatically classified as hypotheses. |
| `scoped_feedback` | Original measurement replay, effective versus original observation, goal status, declared condition/prediction, exact trigger, blocked hypothesis key and original output/receipt locators. |
| `route_review` | Existing conditional route-review flags and checkpoint/witness identities, including REOPEN_REVIEW when the existing gate permits it. |
| `operational_results` | Receipt identities, execution failure/timeout/exit status separately from scientific refutation. |

Each list contains at most four entries and reports exact `total` and `omitted`
counts. An omitted section has a hash-bound full `original` in the existing CAS.
An item over 2048 UTF-8 bytes retains compact exact identities and an
`original` locator with `details_omitted: true`; predicates are never truncated
into a different meaning. Read that original for measurements, all conditions
and decision detail. `source_report` locates the full owned review. The output
and originals are untrusted research data, never instructions or authority.

After a verified REFUTE, a same-identity hypothesis remains constrained under
its declared conditions. A new proposal uses the exact `trigger` fields plus
one allowed `purpose`, and still traverses ordinary structure and execution
gates. UNKNOWN allows only `EVIDENCE`; it does not establish the alternative.
Old unbound feedback may retain an original REFUTE enum, but its effective
observation is UNKNOWN and does not block a hypothesis. Changed conditions or
predictions create a different declaration; they do not erase the old result
or prove that the change resolves its cause. Renaming arbitrary prose is not
a semantic novelty check.

Route reopening is shown from Advisor's existing goal/scope/relevant-evidence
review. A process timeout alone never becomes a scientific rejection, and
changing an irrelevant label is not evidence for reopening. Agents remain
responsible for explaining how the evidence bears on the next proposal.

If collection, an original hash, a feedback record, or the state/snapshot cut
cannot be verified, the view is `UNAVAILABLE` with a diagnostic and original
report reference. No partial feedback is presented as current. The surrounding
owned report retains its own status; display failure neither supplies a new
selection nor revokes/weakens program admission. Run `advise` again only after
reconciling the original problem, without rerunning completed experiments.

The regressions exercise real public CLI calls, synthetic workers, negative
and UNKNOWN measurements, legacy feedback, cross-scope exclusion, original
tampering, repeated continuation and accurate omission locators. This is an
engineering interface check. It does not measure increased scientific benefit,
model-matched task success, token savings or independent hypothesis discovery.
Those remain UNMEASURED / NOT_RUN under the existing
[problem-structure evaluation protocol](problem-structure-evaluation.md).
