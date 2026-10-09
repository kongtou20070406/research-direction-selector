# Development evidence and limits

Scope: one author/work key `issue-292`, base
`cc071e1aaf3ba5d8beb40b4998d4e40f3c2d254e`, a finite task-family slice of
#250/#253. The initial human draft/no-merge hold was superseded by the direct
2026-10-10 instruction "继续加强然后合并". Merge still requires independent
substantive review, final-head CI, and subsequent main verification. No release/version,
formal kernel, SQLite implementation or third-party active branch was changed.

Original checks were executed before the neural benchmark. The RDS self-development
iteration `result-methods-development-20261010-01` ran 59 relevant cases, 0 failures
and 0 skips. After independent review identified concrete source reconciliation,
numeric underflow and timeout-boundary gaps, the changed methods/consumer were
checked in a new frozen iteration `result-methods-development-20261010-02`:
11 cases, 0 failures, 0 skips. Both imported original output and returned Advisor
`development-regression:review-next-change`; the independent review was actually
performed. This is an engineering decision, not proof of scientific gain.

Distinct relevant cases cover 32 pre-existing finite result tests, 2 pre-existing
result consumer CLI tests, 18 new decision diagnostics, 9 new paired/residual
checks, 3 new method consumer checks and 1 benchmark preflight check. Existing
unchanged passing cases are reused; affected paths were rechecked after fixes.
The final consumer check also reads a Windows UTF-8 BOM specification and rejects
duplicate keys in original point JSON through the existing strict parser.

Original development failures are retained in the local tool transcript:

- One premature broad discovery ran while the author's new method definitions
  were still absent: 53 cases, 52 passed and one import error. This was not a
  diagnostic test failure and was not reported as a passing run.
- Two early public-qualification attempts failed because independent JSON
  expected values used integers where Python arithmetic returned floats
  (`-2` versus `-2.0`, acceptance `1` versus `1.0`). The strict qualifier was
  preserved; the explicit expected values were corrected.
- Independent substantive review reproduced incorrect zero standard error for
  `[1e-200,-1e-200]`, inline values borrowing another source's hash, an unenforced
  benchmark cap, and failure handling that modified a rejected old workspace.
  These requirements were fixed, with specific regressions and re-review.

The unit fixture's #253 eight-row oracle is independent of the candidate:
regression beneficial/harmful acceptance 3/4 and 1/4, classifier 1/4 and 1/4,
conjunction 1/4 and 0/4. Missing harmful class, unknown stage, duplicates,
incompatible split/evaluator, stale/missing parents, mixed natural/artificial
origins and excessive/hostile/nonfinite inputs retain failure or UNKNOWN.

The original public consumer fixture had three methods with full independent
returns, original byte bindings and actual numeric goal consumption. Its reused
cases establish software wiring and recovery, not unseen-input qualification.
No paid-model benchmark arms, token savings, GPU training campaign, mathematical
theorem about an MLP, or end-to-end research efficacy score was run.

The single Digits run's worker receipt is preserved in
[`results/20261010/receipt-fields.json`](results/20261010/receipt-fields.json);
the displayed fields are extracted from the original receipt, retaining its
original SHA but not presented as a newly signed complete receipt. The
[`summary.json`](results/20261010/summary.json) and original predictions retain
the actual unfavorable head results and all declared costs. The total worker
wall includes startup; internal timing does not. CPU-seconds remain unknown.
SQLite WAL concurrency and formal-engine performance were not measured by this
finite result-method benchmark.

The strengthening increment adds explicit finite acceptance requirements and a
fourth owned consumer which returns and consumes FAIL/`met=false`. Fifteen
independent requirement tests and eight saved-output reconciliation tests cover
class support, decimal boundaries, UNKNOWN/error separation, forged results,
original value/hash mismatches, split overlap, cost preservation and overwrite
prevention. A real replay uses the same saved probabilities, never model fitting.

The replay exposed an original Windows evaluator defect: `write_text` wrote
CRLF bytes while the original metadata hashed LF text. The original record is
unchanged and the mismatch is explicitly reported. Future benchmark evaluator
writes use exact UTF-8 bytes. The replay verifies the original evaluator text
and normalized claim, then binds all current calls to the actual file hash.
It distinguishes old caller metadata from the checked current byte identity.

The strengthening native development iteration `...-03` exhausted its existing
90-second worker allowance after 25 passing cases while the complete consumer
case was still running; it has no final test-results document and remains FAILED
with timeout=true and 91.609802 seconds spent. It is not a passing suite. The
bounded follow-up `...-04` ran the 24 fast affected checks successfully, imported
their original observations and received the native review-next-change advice.
The complete four-method consumer passed separately in author and independent
reviewer runs (about109/106 seconds), and a fresh persistent owned workspace
retains its actual runs, goal consumption and recovery/cost records. No original
attempt was overwritten or cost refunded. The replay was run only after fixes
and these relevant checks passed, without another model training execution.

Neither #250's broader independent model-arm acceptance nor complete #253
acceptance is claimed. Main acceptance and Issue closure are separate from
local checks and PR CI.
