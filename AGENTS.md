# Operational Contract for Agentic Work in `research-direction-selector` (RDS)

You are operating inside `kongtou20070406/research-direction-selector`, an evidence-driven academic autoresearch kernel. Your job is disciplined, minimum-diff systems engineering that preserves epistemic and cryptographic integrity.

## I. Architectural Invariants (Non-Negotiable)
1. **Single Authoritative Ledger**: Reuse `ProjectStore` events, Content-Addressed Storage (`CAS`), and the Truth Maintenance System (`TMS` persistent AND/OR dependencies, immutable revisions, retractions, conflict checks, and `--saved-dependencies`). NEVER introduce a second ledger, parallel state machine, alternate scheduler, or sidecar database.
2. **Task Truth != Scientific Support**: Completing a command or receiving `verified=true` from a candidate tool (`INPUT_REPORTED`) is NOT scientific confirmation. Scientific status (`SUPPORT`, `REFUTED`, `UNKNOWN`, `NEEDS_EVIDENCE`, `RESOLVE_PREMISE`) is determined exclusively by replaying original measured outputs through the frozen independent verifier.
3. **Fail-Closed Epistemics & Budgets**:
   - Unknown goals, thresholds, rival predictions, commands, or budgets stay explicitly `UNKNOWN`. Never guess, interpolate, or invent fallback defaults.
   - Missing, tampered, stale, or timed-out execution receipts (`r['receipt']`, stdout/stderr SHA256 identities, declared output byte bindings) must fail closed and record negative/unknown evidence. Never silently re-buy or re-run a failed/uncertain paid step.
   - Never widen pilot caps (e.g., 10s pilot cap / 7.2s reservation), campaign caps (100s), conditional forecast hard timeouts (20s), or outer drive timeouts (50s) to make a failing test pass.

## II. Execution Discipline for `GPT-6.1 Sol` (Anti-Bloat & Anti-Drift Rules)
1. **Forensic Trace Before Any Edit**:
   - Do not design new abstractions while reading the problem.
   - First locate the exact existing functions, callers, and CLI entry points in `scripts/` and `tests/`. State the root cause or insertion point in <= 5 bullet lines citing `file:line` before touching code.
2. **Strict File & LOC Budget**:
   - Edit ONLY the files explicitly named in the task scope. If an unlisted file appears broken, log `OUT_OF_SCOPE_DISCOVERY: <file:line> - <1 sentence>` and leave it untouched.
   - Keep net additions under **150 LOC** for bug/CI fixes and **300 LOC** for feature slices.
   - Do NOT write defensive guard-clause towers for impossible internal states. Prefer a single fail-closed assertion that preserves the full return object (`r`) for diagnostics.
3. **Concurrency, Subprocess & Path Safety**:
   - Do not introduce threads, async worker pools, or custom file locks unless explicitly instructed.
   - On `subprocess.run(..., timeout=...)`, always catch `TimeoutExpired` only to record partial `stdout`/`stderr`, current phase/attempt identity, and charged cost into the existing ledger before re-raising or returning a structured failure receipt.
   - On Windows/cross-platform path checks, compare `Path(...).resolve()` identities rather than raw lexical strings while preserving explicit `--root` containment checks.
4. **Test-Fest Prohibition**:
   - During implementation, run ONLY the 1–2 focused test modules directly covering your change (e.g., `pytest tests/test_rds_feasibility.py -k <test_name>`).
   - Never write unit tests for documentation, `AGENTS.md`, or unchanged helper functions.
   - If an environment/harness flake occurs, retry at most **ONCE**. Never loop on failing tests by weakening assertions or increasing timeouts.
5. **No Diagnosis-Only Stopping**:
   - Do not stop after summarizing what should be changed. Execute the code edit, run the focused CLI/test verification, and output the final 6-line execution ledger.
