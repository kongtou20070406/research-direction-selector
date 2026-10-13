# Lean research assistance

RDS provides a read-only local Lean source lookup and a general proof-obligation
preflight. These are preparation tools, **not a general theorem verifier**.
The existing closed-rational Lean verifier is unchanged.

## Agent entry point

```console
python -B scripts/rds_lean_research.py --root formal inspect
python -B scripts/rds_lean_research.py --root formal search threshold
python -B scripts/rds_lean_research.py --root formal review --obligation obligation.json
python -B scripts/rds_lean_research.py feedback --source Draft.lean --diagnostics diagnostics.jsonl
```

The CLI obtains these services through `rds_app.create_app`. It does not create
a ledger, execute Lean/Lake, download libraries or initialize a research session.
Point `--root` at an explicitly selected local library to search it. Source
matches include locations and hashes; they are text matches, not Lean name
resolution or evidence that a theorem is proved. Truncation is explicit.

## Conjectures versus proved statements

An obligation records `schema_version: 1`, `kind` (`CONJECTURE`, `LEMMA`, or
`THEOREM`), `theorem`, `informal_statement`, `formal_statement`, and the exact
`source_sha256`. The schema is `schemas/rds-lean-obligation.schema.json`.

- Only a conjecture may retain `sorry` or other unfinished proof obligations.
  It remains unproved and cannot be used as an established premise.
- A lemma or theorem needs a completed proof. Missing proofs, remaining goals,
  errors, warnings, `sorryAx`, native-evaluation axioms and custom axioms block
  proof acceptance in the strict profile.
- `propext`, `Quot.sound` and `Classical.choice` are the fixed standard-axiom
  allowlist for this general-mathematics preflight. This does not relax the
  existing rational verifier's axiom-free policy.
- A proof of a different source, statement or named declaration is stale.
- A clean audit still returns `REPLAY_REQUIRED`, assurance `NONE`, and
  `usable_as_proved_premise: false`. Audit JSON, even well-formed JSON claiming
  no axioms, is not authenticated proof evidence.

Reviewing an optional audit uses `--audit audit.json`; its bounded format is
`schemas/rds-lean-audit.schema.json`. The output contract is
`schemas/rds-lean-review.schema.json`. Exit 0 means a query completed or an
unproved conjecture was retained, **not proof success**. Exit 2 signals an
incomplete/rejected proof review, unknown query or partial search; exit 1 is a
request/I/O error.

Search skips build/cache directories (including `.lake`); select a specific
local dependency source directory explicitly to search that library. Reads are
bounded and detected symlinks/reparse entries are rejected. This is a local
inspection utility, not a security boundary against concurrent filesystem
replacement by an adversarial process.

## Diagnostic feedback

The `feedback` command parses saved Lean `--json` diagnostics, including actual
`Tactic.unsolvedGoals` messages. It binds the supplied source bytes by SHA-256,
limits diagnostics to 256 KiB and reports truncation/parse errors explicitly.
Locations retain Lean's line numbers and zero-based columns. Exit 0 means the
log was parsed completely, not that compilation or proof succeeded. The log is
untrusted input: the parser cannot authenticate which process/source produced
it. Feedback always retains `proof_status: UNKNOWN`, `assurance: NONE` and
`usable_as_proved_premise: false`; it creates no ledger records.

## Not implemented by this slice

This entry point does not yet execute general proof attempts, return Lean LSP
goal states, independently replay arbitrary compiled proofs, align formal and
informal statements, install Mathlib or archive proofs in the research graph.
Consequently it has no path that creates a general `LEAN_KERNEL_CHECKED` claim.
Those capabilities need a separately reviewed runner and evidence integration.

Lean tactics and build code can execute arbitrary programs. A timeout, Job
Object or `--trust=0` alone is not a filesystem/network sandbox. Future execution
must preserve RDS admission and accounting, isolate untrusted proof code, and
bind the independently checked result to the original statement and dependencies.
Do not turn this preflight into an execution permit or let a plugin supply its
own trusted PASS. See [Lean proof validation](https://lean-lang.org/doc/reference/latest/ValidatingProofs/).
