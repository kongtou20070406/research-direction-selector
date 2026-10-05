# Public CPU acceptance trajectory

Use `prepare.py --root <new-empty-project>` and initialize/advance the generated
contract as described in [the guide](../../docs/predictive-feasibility.md).
Inspect `project next`, `project costs`, receipts and `outputs/launches.txt`.
The slow route never launches; a frozen verifier checks the integer result.
`test_rds_feasibility.py` covers denial, missing verification forecasts, deadline,
applicability and cumulative resource limits.

Result values are deterministic; measured wall times vary. Hard allowances permit
normal process startup and do not supply the forecast. Conditional scaling is
explicit. Passing this fixture does not establish scientific or policy gain.
