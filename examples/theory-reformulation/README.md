# Review a broader formulation without inventing a guarantee

These small inputs are synthetic software examples, not ReFRM observations or
evidence that a state-space architecture improves an image model.

```text
python scripts/rds_cli.py --root . advise --frontier examples/theory-reformulation/frontier.json --frontier-proposals examples/theory-reformulation/proposals.json --brief
python scripts/rds_theory_tools.py --signals trajectory_degradation local_global_gap
python scripts/rds_theory_tools.py --id contraction_target_bias
python scripts/rds_dynamics_probe.py --input examples/theory-reformulation/snapshot.json --output snapshot-diagnostics.json
```

The first command saves the full review to CAS and prints its locator. The
minimal bridge specifies only its candidate, mapping and source; RDS reuses the
original goal and assumptions. The proof-check proposal asks whether a recurrence
embeds in a general **discrete** state-space description. This does not establish
an embedding in a linear continuous SSM or improved task quality.

The bounded finite-model → EGraph → native Lean example can be rerun with
`python -B scripts/rds_theory_tools.py --progression examples/theory-reformulation/progression.json`.
Its schema 2 request supplies only the domain and resource limits; the runner
derives the fixed proposition, empty assumption set and ordered transport
obligations that every stage must share. Extra claim, assumption or transport
fields are rejected instead of silently ignored. Schema 1 requests remain
supported for existing callers. This reduces repeated declarations for this
supported chain; it does not make unrelated operators freely interchangeable.
The EGraph result remains a bounded rewrite check, and an unchecked or missing
native Lean leaf keeps the overall result UNKNOWN.

The snapshot has a nonnormal Jacobian with spectral radius 0.5 and operator norm
greater than 10. Thus a local eigenvalue check alone cannot certify one-step norm
contraction. The residual spectrum and finite endpoint differences are descriptive
estimates, with global stability and task gain left unknown. Missing NumPy leaves
matrix diagnostics unavailable; the refinement calculation remains usable.

Use the existing `exec` command to bind the selected checker, input and output
when an execution receipt is needed. A library match or prototype check grants no
execution authority and does not replace the project's acceptance conditions.
