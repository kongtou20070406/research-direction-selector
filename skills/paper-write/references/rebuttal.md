# Rebuttal and revision

This merges three sources: the vendored ARS revision coach, rebuttal-audit mode and commitment ledger; CCFA-Skills `ccf-rebuttal-writer` (MIT); and a standard conference rebuttal package (`QA.json`, `REVISION_MANIFEST.json`, latexdiff PDF, 5,000-character replies). Mechanical checks run in [check_rebuttal.py](../scripts/check_rebuttal.py) so reviewers spend tokens only on judgment.

## Folder

```text
rebuttal/
  rebuttal.json     char_limit, forbid (project-forbidden terms), revised_dir
  manifest.json     hashes of the submitted source (snapshot step)
  reviews/          reviews exactly as downloaded
  ledger.json       one entry per reviewer comment, updated in place
  replies/          one file per reviewer, general response, AC note
  bindings.json     every decimal or percentage in the replies -> ledger value
  QA.json           written by check_rebuttal.py
```

Ledger entry: `id` (`R1-C2`, `AC-C1`), `source`, verbatim `quote`, `concern`, `status` (`open`, `planned`, `drafted`, `done`, `blocked`, `accepted_limit`), `action` {`kind`: `edit` | `experiment` | `clarify` | `limitation` | `no_change`, `location` as `path#anchor`, `rationale`}, `evidence`.

## Steps

1. Snapshot the submitted source before touching it: `check_rebuttal.py snapshot <source> --out rebuttal/manifest.json --allow <files the revision may change>`.
2. Split every review into ledger entries with a verbatim quote. Keep one canonical ledger. Group concerns that several reviewers share for the general response.
3. Give each entry one action. Use `accepted_limit` to acknowledge a limitation instead of hiding it. New experiments pass research-gate and the project's constraints, and their numbers enter the experiment ledger before any reply cites them.
4. Draft the replies. Start each paragraph with its tag, e.g. `[R1-C2]`. Answer the concern in the first sentence, give evidence second and the paper change third. Concede valid points. Promise only what the ledger records. The AC note lists changes, the evidence behind them and any remaining disagreement. Chinese editing copies are fine; the submitted text is English.
5. Run `check_rebuttal.py check rebuttal --forbid <terms>` until it prints PASS. It checks quotes, tag coverage, character limits, promises, `done` locations, experiment evidence, number bindings, forbidden terms and the source snapshot.
6. Revise the paper. LaTeX papers run `paper_gate.py`. Build a latexdiff PDF and look at every changed page. Keep the page count and main-text end page within the venue limit.
7. Send one editorial reviewer `QA.json`, the ledger, the replies and the reviews, not the whole paper. The reviewer judges only what the script cannot: whether each reply answers its concern, tone, and missing concessions. Findings use the Tier 2 schema in [SKILL.md](../SKILL.md).

## Writing rules

- Prioritize soundness, novelty and missing evidence; presentation points come last.
- No defensive framing, flattery or blaming the reviewer.
- Do not introduce a claim that is not in the ledger or the evidence.
- When declining a request, give the reason and the evidence, and record `no_change` with its rationale.

Read on demand: [revision coach](ars/academic-paper/agents/revision_coach_agent.md) · [commitment ledger example](ars/academic-paper/examples/commitment_ledger_example.md) · [revision tracking template](ars/academic-paper/templates/revision_tracking_template.md).
