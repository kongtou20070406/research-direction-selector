# Top-Venue Paper Writing Rulebook (NeurIPS / ICML / ICLR Oral Standard)

Distilled from Oral / Best-Paper rubrics and flagship architectures (*Counterfactual World Models*, *LeWorldModel*, *Huginn*, *TinyRecursiveModels*).

## Rule 0: Short, Plain, One-Idea Sentences (Less Is More)

Complex, nested sentences lose tired reviewers in the first two pages. Keep writing crisp and scannable (also enforced by the `plain-clear-writing` skill and `scripts/check_sentence_clarity.py`):

- **One idea per sentence (advisory readability guide).** Aim for 12–22 English words (soft 25, max 35) and 15–28 Han characters (soft 42, max 60). Count commas and semicolons together (EN soft 3, ZH soft 2). Readable prose never substitutes for scientific positioning or baseline comparability.
- **Subject and verb upfront**: avoid long front-loaded clauses (*"In order to..., while avoiding..., our method..."*). State what happens first; put numbers or caveats in the next short sentence.
- **No rhetorical questions; no jargon inflation**: motivate strictly as *Problem -> Existing Methods & Trade-Off -> Named Method -> Community Takeaway*. Never package an implementation detail as a grand "dynamics" or "paradigm"; state plainly what capability, speed-accuracy gain, or empirical law the community gains.
- **Standalone modular EN + ZH drafts**: write `latex/sections_en/00..08.tex` and `latex/sections_zh/00..08.tex` (natural academic Chinese, zero translationese) separately, included by `main_en.tex` and `main_zh.tex`; never interleave EN and ZH in one file. Both must pass `check_sentence_clarity.py --submission-mode` (`violations=0`) and `paper_gate.py --compile`. Legacy Markdown drafts are read-only.
- **Clean typography & metric arrows**: never wrap plain numbers, hyperparameters, or model names in Markdown backticks in manuscript prose (reserve backticks for file paths and CLI/code). Always mark every metric in tables and on first prose mention with `↑` or `↓`.

## Rule 1: Zero Lab-Diary Leakage in the Paper Body

A submission draft must read like a standalone manuscript for an external ML reviewer who has never seen your repository.

- **Write as if every reported run is finished**: never describe the team's schedule or stages in the paper (*"the main table will be rerun with the finalized model"*, *"early exploratory runs are used only for diagnosis"*, *"runs complete before the submission freeze"*, *"单种子探索性实验"*). Report what was measured, under which protocol, in present or past tense; leave unfinished numbers as `TBD` cells in tables. The clarity check and `paper_gate` flag these sentences as `process` leaks.
- **Ban internal project taxonomy from the manuscript sections**: never write internal proposition codes (`W1`, `W2a`, `W7`), experiment card IDs (`Card 004`, `Card 010`), internal run arm names (`Arm 3`, `Arm 6`), verdict badges (`PASS`, `PARTIAL`, `audit verdict`), or file paths (`experiments/...`). Move all traceability pointers to `02-证据与复现索引（Evidence）.md` and sprint gap lists to `00-审稿人速览（Reviewer-Brief）.md`.
- **Replace internal slang with standard scientific terms**:
  - `自然 C3 更新比` $\rightarrow$ *error-conditioned adaptation selectivity* (high- vs. low-initial-error state update ratio)
  - `Boat 前缀朝向直泄` $\rightarrow$ *observation-prefix confounding* / *kinematic heading shortcut*
  - `T0 尺子` $\rightarrow$ *iteration-resolved diagnostic profile* (tracking transitions across inference depths $T$)
  - `两段式脚手架` $\rightarrow$ *explicit two-stage extract-execute decoupling*
  - `不越线/红线约束` $\rightarrow$ *strict factual-only protocol* / *zero step-index conditioning*

## Rule 2: The 4-Beat Abstract Formula (~160–220 Words)

Every strong abstract opens directly in active voice without riddle framing (never start with *"We ask what conditions..."* or *"本文研究……如何……"*):

1. **Beat 1 — Broad-Audience Stakes & Tension (1 sentence)**: Tell even a non-specialist reviewer why the real-world problem matters, what structural bottleneck stops existing paradigms from solving it, and why overcoming that bottleneck is significant.
2. **Beat 2 — Named Method & What-Claim (1–2 sentences)**: Name the proposed method upfront and state **What** scientific capability it achieves (never a laundry list of internal modules). Only claim "unification" if equal-budget split-vs-shared ablations prove joint capability beyond parameter reuse.
3. **Beat 3 — Core Guarantees or Mechanism Insight (1–2 sentences, optional)**: Include a formal guarantee only if backed by a verified theorem or proposition; for empirical/mechanistic papers, state the key diagnostic mechanism or omit this beat rather than forcing weak theory.
4. **Beat 4 — Headline Evidence & Scientific Scope (2 sentences).** Give the strongest matched comparison and name the measured setting where it matters to the claim. Put regime boundaries from the review brief in §7 Limitations.

## Rule 3: Inverted-Pyramid §1 Introduction

Answer *"Why should the community change how it thinks?"* before presenting equations:

1. **Paragraph 1 (Broad-Audience Significance Preamble)**: Make an outsider ML reviewer care in 3–4 sentences—answer plainly *why this problem matters in practice, why existing paradigms hit a wall, and what broad significance solving it holds* before introducing technical notation.
2. **Paragraph 2 (Diagnosing the Villain)**: Present concrete empirical or geometric proof that prior failure is structural, not merely insufficient parameters or task ambiguity. A single crisp number pair outperforms a paragraph of adjectives.
3. **Paragraph 3 (Conceptual Solution Before Equations)**: Explain the architectural/theoretical mechanism in plain physical or dynamical intuition *before* introducing symbols. Contrast directly with the nearest peer lines (*LeWorldModel*, *CWM*, *LoopFormer*, *Huginn*).
4. **Paragraph 4 (3 Falsifiable `What` Contributions)**: Frame every contribution strictly as **What** scientific capability, law, or problem is solved—never as a list of modules or implementation tricks (**How**)—paired with verified quantitative scope.

## Rule 4: Standard ICML Order & Optional Theory Depth

Follow standard ICML section order: `Abstract -> §1 Introduction -> §2 Setup -> §3 Method -> [Optional §4 Theory] -> Experiments -> Related Work -> Discussion & Limitations -> Appendix` (`Related Work` follows `Experiments`; swap `Method` and `Theory` only for pure theory, and omit `Theory` cleanly if the paper is empirical).

1. **Zero-Collision Symbol Table (`§2`)**: Assign one symbol per meaning across all sections; define dimensions, sets, and operators on first use.
2. **Academic 3-Part Theorem Standard (when theorems are included)**: Keep academic precision while eliminating unreadable symbol walls (never pad weak claims into a forced theorem):
   - *Part 1 — Geometric/Physical Intuition*: 1–2 sentences explaining why the claim holds physically.
   - *Part 2 — Minimal Formal Statement*: state only the sharp condition and conclusion; banish secondary constants and long subscript chains.
   - *Part 3 — Stepwise Proof Sketch*: `<= 5` numbered steps, pairing 1 plain causal sentence with 1 compact equation line per step. Move dense algebra, full constants, and edge-case counterexamples to the Appendix.
3. **Substantial `§3 Method` (Never Thin)**: Map design elements to the motivating bottleneck (and to `§4` if theorems exist):
   - Include a **`Motivation / Theorem -> Method Component -> Purpose` mapping table** when helpful.
   - Give explicit **architecture & tensor dimensions**, **state initialization**, **iteration update & gating equations**, **explicit training objectives**, and a self-contained **Algorithm 1 pseudocode** + complexity accounting.

## Rule 5: §5 Experiments (Evidence-Driven Tables, 3-Tier Citations & Split) & §7 Discussion

1. **Evidence-Driven Table Budget (No Forced Slots)**: Design tables strictly around what the paper's actual evidence supports (typically main matched-supervision & wall-clock comparison + single-variable ablations, plus optional mechanism probes). Never force a closed-loop control table, theorem-probe table, or fixed 4-table quota when the evidence does not warrant it.
2. **Three-Tier External Citations & Strict Two-Tier Evidence Split**:
   - *Tier 1* (identical protocol/split/metric): cite official table numbers directly with `†`. *Tier 2* (same env, different protocol): contextual reference only, no bold superiority claims. *Tier 3* (controlled main comparison): retrain 3 seeds locally under matched recipes.
   - Keep **3-seed FP32 recipe-aligned results in Main Text** and **single-seed exploratory runs in Appendix**. Report denominators, paired 95% CIs, and any raw metric where a baseline wins honestly without cherry-picking.
3. **Elevate Failure Analysis Into Regime Insight (§7)**: Group honest boundaries into sharp dynamical regimes (*out-of-radius initialization*, *residual-gate vs. ClampRMS gap*, *adaptive-covariance black-box search scope*) paired with falsifiable fixes.

## Rule 6: Anti-Hype Self-Audit Before Calling Any Draft Ready

- [ ] Does Abstract Sentence 1 and §1 Paragraph 1 clearly tell a non-specialist reviewer why the problem matters and its broader significance, do Sentences 2–3 name the method directly, and are contributions framed as **What** rather than **How** (no module checklists)?
- [ ] Do both `latex/sections_en/*.tex` and `latex/sections_zh/*.tex` contain zero plans, reruns or stage words, zero internal tracking labels (`Card`, `Arm`, `W1–W7`, `PARTIAL`, `ASTRA`, repo paths), zero stray backticks on numbers/models, and pass `--submission-mode` with `0` violations?
- [ ] Does every theorem follow the 3-part format (`Intuition -> Minimal Statement -> <=5-step Sketch`) with a zero-collision symbol table, and does `§4 Method` have an explicit `Theorem -> Component` table + Algorithm 1?
- [ ] Does every metric table and first prose mention carry `↑` / `↓`, with 3-seed Main Text evidence strictly separated from single-seed Appendix exploration and subspace probes accompanied by random-direction references?
- [ ] Were negative or null comparisons and baseline advantages reported cleanly with exact denominators rather than spun?

## De-AI hard rules

1. Give the Abstract zero internal module checklists. Name the method and the supported capability. Introduce gates, modules and implementation names in Method.
2. Build causal flow through each paragraph. Read it aloud and revise runs of similarly short sentences and repeated template openings.
3. Rewrite Chinese independently in natural oral-style phrasing. Start with the scientific claim and its evidence, then check the two drafts for claim parity. Ban `记分反事实查询`, `对称排版`, `零点归零门控`, and `学得X`.

**Narrative Planning Rule.** Before drafting, map the problem, the evidence for the bottleneck, the mechanism and the supported result. For each paragraph, decide which claim it establishes, what evidence it needs and how it leads to the next paragraph.

**Pre-Compile De-AI Pass.** Run `check_sentence_clarity.py --submission-mode --strict-deai --draft-dir <paper-dir>`. Read each flagged passage in its paragraph and resolve the hard violations before compilation. In default mode, the De-AI patterns and rhythm check are advisories.

## Lead–GPT Complementarity & Clean-Room Voice Charter

The Lead owns the thesis, framing and voice, anchored to a matching [exemplars/](exemplars/index.md) card and its original passage. GPT owns counterexamples, Lean↔LaTeX checks and closed engineering tasks with verifiable outputs. The Lead reads `lead_action_brief.md` for objective defects and §7 scope boundaries. Raw GPT commentary, scores, verdict slogans and prose suggestions stay outside the author context.

## Bilingual mechanical checklist

Enable `theory_gate` in each language's `paper_gate.json` for the structural checks, and run `paper_gate.py --compile` in the normal build.

- [ ] Keep the Abstract within 220 English words or 380 Han characters and at most six decimal or percentage values. Keep the exact Abstract–Introduction overlap below four distinct values with three or more decimal places.
- [ ] Give each configured `title_pillars` entry its required number of captioned Experiments tables.
- [ ] Remove harness-split and meta-outline wording. Limit each main-text table to 48 numeric cells and define shorthand such as `mode A1` near use.
- [ ] Keep Method-only symbols out of Setup. Refer to a displayed equation when both sections need it.
- [ ] Give Method one to four numbered equations and put a math expression in every theorem, proposition and lemma body. Register aliases and reserved roles. Typeset algorithm pseudocode in LaTeX.
- [ ] Maintain one active LaTeX source tree. Use `allowed_tex_trees` for intentionally retained trees and `single_tex_source: true` to check inside a paper directory.
