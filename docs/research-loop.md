# One research loop for the host agent

The researcher gives a question, existing evidence, or a stuck approach. The
agent uses one entry to retain that goal, compare changed routes, call existing
checks and continue from their results. Commands below are agent-facing.

The interaction borrows Lean's [goal/context and tactic feedback](https://lean-lang.org/doc/reference/latest/Tactic-Proofs/Reading-Proof-States/):
keep the current obligations visible, apply a specific action, and inspect the
resulting state. Research proposals remain informal; this is not a general
Lean kernel or an automatic proof that a changed research direction is useful.

Set `CLI` to `python -B "<skill-dir>/scripts/rds_cli.py" --root "<project>"`.
Read `CLI research status` first. It discovers a canonical project from child
directories, reports existing native objectives/workflow and recent ordinary
notes, and replays retained checks without running experiments or generators.
Use `note list` to inspect omitted ordinary notes. Existing campaign objectives,
pause instructions, admission and budgets remain authoritative.

`CLI research start --goal "Find a fixed point of x -> 2x+1"` records the actual
working goal and returns its note id (for example `n1`) plus the context. Starting
the same current goal is idempotent. A changed goal creates a new scope; old
records remain available through `research show --id <note-id>`. A working goal
does not revise an existing frozen campaign objective.

## Propose, check, revise

Write a proposal using returned ids, then `CLI research propose --file route.json`:

```json
{
  "goal": "n1",
  "parent": null,
  "summary": "Try fixed-point iteration",
  "change": {"kind": "baseline", "before": "No route", "after": "Iterate 2x+1"},
  "discriminator": {
    "question": "Does this map satisfy the infinity-norm contraction criterion?",
    "if_pass": "Use the contraction route within its exact assumptions",
    "if_fail": "Try an algebraic representation; a fixed point may still exist"
  },
  "obligations": [{
    "id": "contraction",
    "meaning": "This particular sufficient contraction condition holds",
    "tool": "formal",
    "spec": {"schema": 1, "kind": "affine_contraction", "model": {"matrix": [["2"]], "bias": ["1"]}, "threshold": "1"}
  }]
}
```

`CLI research check --proposal <returned-id> --obligation contraction` invokes
the existing formal checker, stores its original result in CAS, replays it and
returns context with `REFORMULATE`. The failed sufficient condition does not
prove that there is no fixed point.

Propose a new route with `parent` set to the first proposal, `change.kind` set to
`representation`, and before/after describing iteration versus solving
`x=2x+1`. Replace the obligation with `affine_fixed_point`, the same model and
`"point": ["-1"]` (remove `threshold`). A check now returns a scoped PASS and
`INTERPRET_CHECKED_SCOPE`; the failed first route remains visible. The host must
explain that this checks a fixed point, not iterative convergence.

Use `hypothesis` or `decomposition` for other changes. A non-baseline proposal
requires a parent in the same goal and different before/after descriptions.
The host supplies the ideas; string inequality is not a semantic novelty test.
Each proposal has 1..16 named obligations, each with `meaning`, `tool`, and the
existing tool's `spec`. Define the discriminator before running the check.

`CLI research select --proposal <id> --reason "..."` retains the host's next
route choice without promoting it to evidence. Selection can retain an untested
route. The returned `focus_route` and `next_action` follow a later explicit
selection; adding another proposal focuses that new exploration.

## Connected capabilities

| Tool | Spec and behavior |
| --- | --- |
| `formal` | Existing [formal statement](formal-verification.md); generate then independently replay its certificate. Preserves checker assurance, failures and unsupported UNKNOWN. |
| `math` | Existing [exact math request](math-capability-router.md); call the registered solver and replay with independent rational arithmetic. Inspect the check record for the certified solution. Missing backends or unsupported problems stay UNKNOWN. |
| `structure` | `{"proposal_id":"candidate"}` names an existing [structure proposal](problem-structure.md). `research check` consumes settled feedback and rechecks original receipts. `research advance` runs/reconciles that candidate and verifier through existing admission, then consumes feedback into this same research context. |
| `observation` | An explicit question/measurement description as `spec`. `research check ... --evidence output.json` snapshots original file bytes. Even if they claim PASS, the result is INPUT_REPORTED and discharges no obligation. |

Repeated proposals with identical content reuse their note. Repeated checks
reuse the original result and replay it; they do not silently rerun a failed or
unknown operation. Different evidence requires an explicit revised proposal.
`research check` exits zero when the operation was recorded or reused, including
a checked FAIL or UNKNOWN. Read `check.status` and the returned context for the
research outcome; this differs from the standalone `formal` verdict exit codes.
Use `research show --id <check-record-id>` to retrieve the original result and
request. The context includes this id beside each check. Missing/tampered CAS
or receipts fail closed. An interrupted empirical attempt uses the existing
structure/project recovery path; it is never converted into a scientific FAIL.

`research status` is a read-only projection, not a second scheduler. It does not
activate TMS nodes or rewrite original verdicts. Exact checks concern only
declared obligations; omission of a necessary premise remains the host's open
research problem. No automatic aggregate verdict claims the user goal is solved.

## Try the whole path

Run `python -B examples/research-loop/run.py --root <new-empty-directory>`.
The example drives the public CLI through failed contraction, changed
representation, checked fixed point, selection, restart and reuse. It uses
prescribed ideas and exact arithmetic, so it verifies integration and scope
handling, not AI discovery ability or improved research performance.
