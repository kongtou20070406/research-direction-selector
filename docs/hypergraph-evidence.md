# Receipt-bound evidence and OR-surviving retraction

The dependency map reports labels; it was never a proof kernel
(`INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF`). Two upgrades make its
labels worth consuming while keeping that honesty: evidence bindings that
tie SUPPORTED claims to executed runs, and retraction semantics that
recompute closure so healthy OR alternatives survive.

## Evidence bindings: `SUPPORTED` with a receipt

Any node or hyperedge may declare an evidence binding naming one receipt in
a frozen, append-only project ledger (`.rds/project.sqlite3`):

```json
{"id": "B", "status": "SUPPORTED", "source": "paper, p.7",
 "evidence": {"receipt": {"project_root": "C:/proj", "sha256": "<receipt sha256>"}}}
```

`rds_cli.py hypergraph --input map.json --audit-receipts` grounds the
binding: the named ledger must contain a receipt with that sha256 whose
`run_status` is `SUCCEEDED` (read-only; append-only triggers are never
touched). Outcomes per binding land in `receipt_audit.audits`:
`GROUNDED`, `RECEIPT_NOT_FOUND`, `RECEIPT_NOT_SUCCEEDED`,
`RECEIPT_BODY_INVALID` (the stored body does not decode to a JSON object,
or fails the ledger's own receipt check: a repeated key, another run, a
sha256 that does not match the row or the recomputed digest, or a value with
no canonical encoding; `reason` names the type or the failure),
`RECEIPT_AMBIGUOUS` (more than one stored receipt
carries the sha256), or `LEDGER_UNAVAILABLE`.

- **Fail-closed in both modes**: declaring a binding never promotes the
  record. Without the audit (or when grounding fails) the record stays out
  of the closure and is listed in `receipt_blocked_node_ids` — a SUPPORTED
  label with an unverified receipt behaves like an UNKNOWN premise, never
  like silent trust.
- **Blocked records surface repair**: a blocked premise node degrades to a
  direct-evidence obligation (`node:<id>`); a blocked SUPPORTED rule
  surfaces a `RECEIPT_REVALIDATION` ready obligation for the rule, so the
  map still names the next action instead of going dark.
- A succeeded run is not a statement proof; the audit upgrades the
  receipt's existence, not the claim's truth.

## Retraction: one derivation dies, its OR alternatives live

Retraction is expressed by editing the record's status in the input map
(CONTRADICTED, or UNKNOWN for evidence loss) and re-running analysis. The
closure recomputes from scratch, so a conclusion keeps support whenever any
grounded route remains; only conclusions whose every route lost support are
retracted. Aggregate failure never names a guilty premise, and receipt
blockades compose with this: a receipt-blocked premise retracts downstream
derivations exactly as a status flip does.

## Bounds and truncation

Existing limits are unchanged (`DEFAULT_LIMITS`), and blocker-set
incompleteness still means `UNRESOLVED`, never impossibility. Receipt
audits are read-only and bounded by the ledger they name.

## Exact blocker enumeration

Missing-evidence families use a dependency worklist: only a changed premise
family wakes its consumers, including a same-size replacement by smaller sets.
Operation-local integer masks implement exact set union and subset subsumption.
AND products join narrow factors first and reuse identical premise families;
an existing conclusion family prunes a partial union only when that union and
every possible extension are already supersets of a retained evidence set.
Neither cost preferences nor top-K selection discard incomparable alternatives.
Actual support, first-derivation witnesses, receipt grounding and original source
records continue through their existing code paths.

`combinations_examined` counts the union and conclusion candidates actually
examined by this implementation, including rejected candidates. Skipped unchanged
rules, duplicate factors and identity factors do not consume fictitious work.
The count and the point of truncation can therefore differ from the previous
scanner. Default/hard limits and the fail-closed output contract are unchanged:
an unfinished unsupported goal never exposes partial blockers as complete.
True exponential antichains can still exceed the limit on a small input graph.

For a reproducible local comparison, save the **trusted** parent revision's
`scripts/rds_hypergraph.py` outside the checkout and run:

```text
python -B benchmark/hypergraph_blockers.py --baseline-source <trusted-parent-file.py> --output <new-timings.json>
```

For revisions with `rds_hypergraph_blockers.py`, save that same revision's helper
beside the selected file under its original name. Both files must come from the
trusted baseline; a required missing helper is an error, never a fallback to the
current checkout. Revisions before the helper existed remain supported.

The benchmark executes those selected Python sources. It records both source
identities, alternating raw samples, work counts and separate Python allocation
peaks. The same-cap timings retain complete/incomplete differences; a separate
common larger-cap comparison checks complete semantic equality. A truncated
baseline is never used to claim a complete-result speedup ratio. Tiny graphs can
pay extra bookkeeping overhead; chain/high-branching gains are synthetic local
observations, not a worst-case latency, LLM-token or scientific-gain guarantee.

## Verification

`tests/test_hypergraph_evidence.py` drives the analyzer and the real CLI:
grounded binding keeps closure and reports `GROUNDED`; missing receipt
downgrades the node while a healthy OR route keeps downstream goals
DECLARED_SUPPORTED; with the sole route retracted the map reports the
repair atom (`node:B`) as the ready obligation; a FAILED receipt never
grounds; a blocked SUPPORTED rule surfaces `RECEIPT_REVALIDATION`; malformed
bindings are rejected; and declaring without auditing is fail-closed.
