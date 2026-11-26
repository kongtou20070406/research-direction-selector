---
name: paper-write
description: Draft, revise, frame, review, or finalize top-venue ML/AI papers (ICML, NeurIPS, ICLR) in standalone English and Chinese sections, abstracts, introductions, theory, or rebuttals. 起草、改写、润色、审稿或定稿顶会中英文论文、摘要、引言与审稿回复。Do NOT use for casual partner chat (follow AGENTS.md), literature search (use paper_search), figure drawing (use paper-figure-craft), or pre-coding architecture/idea vetting (use research-gate).
---

# Manuscript writing

The lead agent writes and translates all manuscript prose. Use the lead model as the default writer. Keep pre-code checklists out of drafting.

**Draft vs. Freeze (Anti-Amnesia).** Do not let style or formatting rules stall early drafting: during everyday compilation, record known gaps with `% gate-sorry: <check> — <reason>` or `TBD` so the PDF still delivers while every deferred item stays counted and listed. Before submission or `--freeze`, run with `--no-sorry` and `--require-headline-bindings` so every deferred debt turns back into a hard check and nothing is forgotten.

**Before writing anything, every time** (these reads are required, not on demand, and do not count against any "read few files" budget):
1. Read [top-venue-writing-rules.md](references/top-venue-writing-rules.md) in full.
2. For an abstract, introduction or theorem narrative, read [exemplars/index.md](references/exemplars/index.md), then one matching card and its original passage.
3. After writing, run the clarity check and `paper_gate.py`. They block internal workflow wording (reruns, stages, schedules), internal labels and long sentences, whether or not you remembered the rule.

## Draft

- Write independent `latex/sections_en/00..08.tex` and `latex/sections_zh/00..08.tex` modules. Include them from `latex/main_en.tex` and `latex/main_zh.tex`. Use `--markdown` when assembling a legacy Markdown draft.
- Default layout during research is clean single-column arXiv preprint (`"venue": "arxiv"` in `paper_gate.json`). When switching to a target conference/journal wrapper (`main_<venue>.tex` + `"venue"` / `"page_limit"`), run `paper_gate.py --compile --layout-check --render-pages` to check page limits, overfull boxes and margins automatically and visually inspect the rendered PNG pages with the Main Model.
- Generate `\input{tables/auto_*.tex}` directly from experiment `RESULTS.json` via `verify_manuscript_formal.py --results-to-tex` instead of hand-copying numbers.
- Use Abstract → Introduction → Setup → Method → Theoretical Justification → Experiments → Related Work → Discussion & Limitations → Appendix. Swap Method and Theory only for pure theory.
- Open the abstract and introduction with a concrete problem, why it matters and where current methods fail. Name the method and its capability by abstract sentence 3.
- State only supported claims. Cut lab tags, repo paths, workflow slogans, coined shorthand, proxy-metric padding and speculative side claims. Keep protocols, speedups and unit tests out of the headline.
- Describe completed results and protocols in the present or past tense, keep plans, reruns, internal stages and submission schedules out of the prose, and mark pending numbers as TBD in tables. Sentence clarity in submission mode and the paper gate both flag internal workflow wording.
- Match prose and figures to code. Give each metric its direction (↑/↓), denominator, seed count and precision. Separate three-seed main tables from single-seed appendix tables. Use plain type for numbers, hyperparameters and model names.
- Write one idea per sentence. Aim for 12–22 English words (soft limit 25, maximum 35) or 15–28 Han characters (soft limit 42, maximum 60). Count commas and semicolons together. EN soft 3 / hard 4, ZH soft 2 / hard 3. Avoid ≥3 的 and nested 在…中通过…以… chains.
- Give each main-text theorem 1–2 intuition sentences, a statement with ≤5 active symbols and a proof sketch of ≤5 steps. Move algebra, auxiliary constants and edge cases to the appendix.

### De-AI hard rules

1. Keep the Abstract free of internal module checklists. Name the method and its capability there. Introduce individual modules in Method.
2. Connect sentences through cause and consequence. Read paragraphs aloud and revise runs of similarly short sentences with parallel openings.
3. Draft Chinese independently from the claims and evidence. Explain the argument aloud in natural Chinese before writing, then check EN/ZH claim parity. Cut calques such as `记分反事实查询`, `对称排版`, `零点归零门控`, and `学得X`.

**Narrative Planning Rule.** Before drafting, map the problem, evidence of the bottleneck, proposed mechanism and supported result. Give each paragraph a job in that sequence and identify the evidence supporting its claim.

**Pre-Compile De-AI Pass.** Run `check_sentence_clarity.py --submission-mode --strict-deai --draft-dir <paper-dir>` on both languages and review the flagged passages in context before compiling. See the [six mechanical checks](references/top-venue-writing-rules.md#bilingual-mechanical-checklist).

### Lead–GPT Complementarity & Clean-Room Voice Charter

The Lead owns the thesis, framing, core manuscript voice, and final visual PNG sign-off. Anchor that voice to a relevant card in [exemplars/](references/exemplars/index.md) and its original passage. Offload mechanical TeX/BibTeX/table/ref/acronym fixes, layout/page-limit loops, and EN↔ZH numeric/label parity checks to workers via `scripts/dispatch_gpt.py --skill latex-typeset` (automatically enforcing `--verify-prose`), alongside counterexamples, Lean↔LaTeX checks, and read-only evaluations. The Lead drafts from manuscript evidence and `lead_action_brief.md`. Keep raw worker commentary outside the Lead's writing context. Apply scope caveats to §7 Limitations and state the supported claims directly in the Abstract, Introduction and Method.

## Assemble and freeze

**LaTeX-native papers.** After every edit run `python scripts/paper_gate.py PAPER_DIR --fix --compile` and deliver only when it exits 0. `scripts/paper_gate_watch.py` (started at logon) also runs the gate without `--fix` 30 s after the last save and notifies on failure. It copies the PDF to `deliver_as` itself (one path or a list; an absolute path such as the project's Obsidian library delivers the stage PDF there). It auto-fixes registered term variants, undefined first-use acronyms and self-range cross-references, then compiles and checks duplicate display equations, figure-only acronyms, number claims against same-named main-table rows, floats placed ≥2 pages late or after the Conclusion, and unresolved refs, and tasks or datasets that appear only in the abstract or conclusion (`scope`). When you introduce an acronym, coined term or a new name for an existing module, register it in `PAPER_DIR/paper_gate.json` in the same edit. Keep that registration in the paper configuration. Allowed placeholders such as `TBD` are counted, not failed. An acknowledged gap works like Lean's `sorry`: `% gate-sorry: <check> — <reason>` on the flagged line or the line above turns that error into a counted sorry and the PDF is still delivered; a marker without a reason or matching no error fails; build the submission with `--no-sorry`.

Register manuscript-wide aliases in `terms` and context-dependent expressions in `term_context` with a reason. Add `--semantic` for advisory duplicate, vocabulary and evidence checks through the local EmbeddingGemma service. Review candidates in their source sentences before promoting aliases. See [LaTeX terminology and evidence](references/latex-terms-and-evidence.md) for the configuration and report. When the gate emits `PAPER_GATE_AUTOFIX_APPLIED`, read its `writer_review.json`, check the changed passages for meaning and agreement, and use the approved names in further drafting. Correct any context-specific mismatch before delivering.

1. **Tier 1.** For bilingual drafts, run `verify_manuscript_formal.py`, `check_sentence_clarity.py` in submission mode on both languages, and `paper_gate.py --compile` with `latex/paper_gate.json` and `latex/paper_gate_zh.json` in order. Each config supplies its PDF delivery path via `deliver_as`. For LaTeX, parity compares section names, labels and decimal/percentage counts in each section. Use `--require-headline-bindings` to bind every headline decimal or percentage to the ledger.
   LaTeX freeze briefs contain text extracted from the `.tex` sections. Audits record the raw source hashes and scope their checks to the corresponding `latex/sections_en/*.tex` and `latex/sections_zh/*.tex` paths. Legacy section paths in ledger bindings resolve to their LaTeX counterparts.
2. **Tier 2.** Before a major freeze, run `freeze_review.py`, which sends both reviews below in parallel, stamps the staged hashes into the reports and checks them, so nobody relays reviews by hand. First rerun `verify_manuscript_formal.py --require-headline-bindings`, so every decimal or percentage in sections 00–01 (EN and ZH) binds to the ledger. Require a read-only `independent-reviewer` review of math, claims and entailment (with configurable `--auditor` model). Its JSON verdict must be `PASS` and match the staged manuscript hashes. Also require a read-only `editorial-reviewer` editorial review (`codex exec -s read-only`) on the same drafts for framing, flow and EN/ZH fidelity. The workflow uses only the lead model and independent reviewers; the lead model writes, so it never approves its own draft. Give both reviewers the drafts plus the bindings, ledger rows and `run_meta.json`, never the lead's summary or verdict. Run them independently; neither sees the other's report. A blocking finding that survives a mechanical recheck stops the freeze. Stop reviewers after review. Changed drafts require fresh reviews. Each finding in the audit JSON carries an id, severity, type, status, evidence and a countercheck; an open critical or major finding also needs a resolution condition and cannot coexist with `PASS`. Build what reviewers read with `scripts/build_review_brief.py`: the first freeze sends every section; later freezes send only changed sections (as diffs), open findings and the bindings they use, and every changed section needs an audit item whose `scope` names it. The audit records the brief's `section_sha256`. On a re-review, pass the previous audit with `--prior-audit-json`: every earlier open finding must reappear with its status, and each finding states its origin (`inherited`, `revision_regression`, `previously_undetected`, `newly_revealed_by_evidence`, `external_standard_change`).

## Rebuttal

Follow [rebuttal and revision](references/rebuttal.md): snapshot the submitted source, keep one ledger with a verbatim quote per reviewer comment, tag every reply paragraph with its comment id, bind every number and promise only what the ledger records. `scripts/check_rebuttal.py check` must print PASS before any reviewer reads the replies. The reviewer then gets `QA.json`, the ledger, the replies and the reviews, not the whole paper.

## Read on demand

- [Writing exemplars](references/exemplars/index.md): before drafting an abstract, introduction or theorem narrative, read one matching card and its original passage.
- [Claim scope and quantitative framing](references/framing-g1-g3.md) · [Venue terminology](references/world-model-venue-lexicon.md) · [Prose and theorem rules](references/top-venue-writing-rules.md)
- [Titles and introductions](references/ars/academic-paper/references/intro_title_rhetoric_guide.md) · [Abstracts](references/ars/academic-paper/references/abstract_writing_guide.md) · [Process leakage](references/ars/academic-paper/references/anti_leakage_protocol.md)
- [Peer review](references/ars/academic-paper-reviewer/WORKFLOW.md) · [Revision patches](references/ars/academic-paper/references/revision_patch_protocol.md) · Read [ARS scope](references/ars/VENDORED.md) before following any ARS step that names a script or `shared/` file.
