# Research notes and reminders: evidence for this change

Change: `docs/research-notes.md`. No claim that reminders improve research is made here.
`scientific_support` stays UNKNOWN.

Why this shape
- A replay pilot (`replay-pilot-result-2026-10-31.md`, on `feat/jumpbench`) found general text prompts gave no
  useful gain: bare 8%, RDS skill text 14%, expectation prompt 14%, always-doubt 14%.
- A bounded literature scan found no direct comparison of state-triggered and periodic reflection. Nearest
  support: Kulkarni & Simon 1990 (surprise against recorded expectation), Huang et al. 2024 (self-correction
  without external feedback is weak), BMJ 2020 m3216 (a recorded disposition helped clinical alerts), Felisberto
  et al. 2024 (alerts overridden at 90%), arXiv:2604.11088 (random rules tie curated ones, so a sham arm is needed).

Checks, all rerun by the maintainer's orchestrator, not reported by the worker
- `test_rds_nudge` 27, `test_rds_nudge_regressions` 8, `test_rds_notes_project_init` 1, `test_rds_conversation_hook`
  20, `test_rds_hypergraph_view` 69 (2 skipped), plugin tests 14: all pass. The full suite was not run.
- Import graph: upward edges 17, cycles 3, unclassified 0, unchanged from the baseline.
- Acceptance test and contract hashes were locked before the workers ran and verified afterwards.
- One independent review by gpt-5.6-sol gave 7 findings: 6 fixed, 1 rejected because `shown` is a delivery record
  required by the contract.
- Real Claude Code host: 3 failing `python train.py` runs gave 3 auto attempts and 1 delivered reminder. This
  found a packaging bug no unit test had caught (the hook could not import its sibling modules), since fixed.

Not established
- Whether a reminder changes what an agent does. In one uncontrolled real run the agent chose to stop rerunning,
  and it did not close the reminder. That is one anecdote with no sham arm.
- Codex: `UserPromptSubmit` is not registered and real delivery was not tried.
