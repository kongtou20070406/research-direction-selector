# Style DNA: Anti-Autoresearch Top-Venue Academic Writing Guide

Use this reference whenever drafting or rewriting an Abstract, Introduction, Theory narrative, or Conclusion — and whenever scrubbing an autoresearch-style or AI-dense draft into publication-grade human prose.

> **Scope note:** Style DNA fixes surface readability and information layering. It does **not** verify scientific positioning, baseline comparability, or theorem-to-code validity — run the 3-Question Positioning Gate first.

## 1. Quantitative Benchmarks from Real Top-Venue Exemplars

Measured directly from published source text (arXiv HTML / official PDFs) versus a typical autoresearch-style draft:

| Paper (Venue) | Abstract Words | Decimals in Abstract | Abstract Sentence Lengths (words) | Opening Move (Intro) |
| --- | :---: | :---: | --- | --- |
| **TD-MPC2** (ICLR 2024, `2310.16828`) | 124 | 0 | concise 20–30w range; integers only (`104`, `4`, `317M`, `80`) | Scaling trend → concrete single-task bottleneck |
| **VGGT** (CVPR 2025 Best Paper) | 147 | 0 | `[39, 23, 26, 26, 25, 8]` (8-word closer) | Classical geometry loop → direct prediction question |
| **1000-Layer SSL-RL** (NeurIPS 2025) | 162 | 0 | `[21, 24, 32, 34, 25, 16, 10]` (10-word closer) | Scaling gap between vision/NLP and RL |
| **DEQ** (NeurIPS 2019, `1909.01377`) | 181 | 0 | balanced; 1 integer metric (`88%` memory reduction) | How feedforward layers work → fixed-point observation |
| **Diffusion Policy** (RSS 2023, `2303.04137`) | 191 | 1 (`46.9%`) | clear 20–30w clauses; integers (`12`, `4`) | Supervised regression vs. multimodal robot actions |
| **What If / CWM** (`2611.24778`) | 287 | 0 | integer ranges only (`38–46%` → `86–96%`, `35–69%`) | 8-word punchline + boat & hidden current story |
| *Autoresearch Anti-Example* | *217* | ***16*** | *`[18, 24, 26, 31, 59, 59]` (two 59w scorecards!)* | *Abstract copy-pasted into Intro bullet 3 + `TBD`* |

**Target operating envelope (EN, advisory readability guide):**
- **Abstract length:** 130–185 words (6–8 sentences).
- **Decimals in Abstract:** **0–1 typical, soft cap 3.** Avoid 4-decimal tables in prose (`0.8123 vs. 0.7410`). Round to percentages or order-of-magnitude gains (`4x fewer rollouts`, `nearly 2x lower wall-clock latency`), or state clear regime wins.
- **Sentence-length cadence:** Alternate short anchor sentences (**8–14 words**) with medium explanatory sentences (**18–26 words**). Keep prose under 35 words; never write a 50+ word scorecard sentence.
- **Noun-stack ceiling:** At most **3 pre-modifiers** before a head noun in narrative prose (unpack 5–7 word stacks into clauses or Theorem blocks).

## 2. Information Layering Rule (What Goes Where)

Autoresearch drafts fail readability when they dump lab-notebook verification artifacts into the Abstract and Introduction:

| Section | Job | Allowed Evidence Density | Keep Out |
| --- | --- | --- | --- |
| **Abstract** | 1 vivid physical tension → 1 core idea → 1–2 headline regime/order-of-magnitude outcomes. | 0–1 decimal (cap 3); rounded ratios, qualitative wins, key horizon/depth. | `strict IEEE FP32`, `three-seed`, `seed=42`, solver zoos, 4-decimal scorecards, `TBD`. |
| **Introduction** | Vivid physical scenario first (method name later) → why decoupled pipelines structurally fail → key mental shift → 3 short, non-repetitive contributions (<= 2 lines each). | Conceptual mechanism, benchmark names, at most 1–2 high-level summary stats if not already in Abstract. | Copy-pasting Abstract decimals; lawyer-style disclaimer stacks inside contribution bullets; dumping 20 baseline names; `TBD`. |
| **Method & Theory** | 1–2 plain-English intuition sentences first, then crisp math. Isolate exact hypotheses inside `Theorem` / `Assumption` environments. | Clean equations (<= 5 active symbols per theorem); short proof sketch. | Jamming 6-adjective scope caveats into narrative prose (put exact algorithm/covariance assumptions inside the formal statement). |
| **Experiments** | Full quantitative proof, controlled comparisons, ablations, and protocol fidelity. | Exact decimals, precision/seed/budget specs, paired bootstrap CIs, $p$-values, and `TBD` table cells (only if pending). | Unbounded narrative walls that re-read every cell of a table instead of stating the takeaway of each comparison. |
| **Conclusion** | One-sentence conceptual synthesis + candid scope/limitations. | <= 2 numbers total; honest failure regimes and theoretical assumptions. | A second Abstract scorecard repeating Tables 3 and 4. |

## 3. Anti-Autoresearch Redline Checklist (10 Advisory Checks)

1. **Abstract Decimal Cap:** Count `\d+\.\d+` in the Abstract. Aim for `<= 3` (target `0–1`); avoid 4-decimal numbers (`0.xxxx`).
2. **Abstract Protocol Ban:** Zero occurrences of `FP32`, `IEEE`, `three-seed`, `seed=`, `TBD`, or 3+ hyphenated solver-budget tags in the Abstract.
3. **Zero Intro–Abstract Copy-Paste:** No 4-decimal metric or baseline list from the Abstract should reappear verbatim in the Introduction contributions.
4. **Rhythm & Ceiling:** Average EN sentence 15–22 words; avoid sentences > 35 words; include at least one crisp 8–14 word anchor sentence in Abstract and in Intro P1–P2.
5. **Noun-Stack Limit:** Flag any sequence of >= 4 stacked modifiers before a noun (e.g., `strict IEEE FP32 three-seed equal-observation evaluation`). Unpack into a verb phrase or move into a formal Theorem statement.
6. **Physical Scenario Before Jargon:** Opening 2 sentences of Intro should depict a concrete physical dilemma before naming the architecture or parameter count.
7. **Zero Mid-Claim Lawyer Disclaimers:** Do not smother a main-text claim with defensive parenthetical hedges. State the supported mechanism cleanly in prose; state exact formal hypotheses inside the Theorem block and §7 Limitations.
8. **Zero Workflow / Pending Leakage:** Never write `TBD`, `rebuttal`, `rerun`, `card`, `stage`, or `verification protocol` in Abstract, Intro, or Conclusion prose.
9. **Earned Transitions (No Audit Dumps):** Avoid mechanical `First... Second... Third...` dumps that list disconnected numbers. Each paragraph must advance one causal link (`observation → mechanism → consequence`).
10. **Conclusion Synthesis Test:** Conclusion contains `<= 2` numbers and ends on the core scientific principle, not a recapitulation of every table row.

## 4. Before vs. After Rewrite Pairs (Generic Examples)

### Pair 1 — Abstract Outcomes
- **Before (Autoresearch/AI — protocol & 4-decimal dump):**
  > Under strict IEEE FP32 three-seed evaluation on 4-step ContactPush, ModelX outperforms a 5.2M equal-observation transformer across pre-query ($0.6120$ vs.\ $0.5710$), endpoint ($0.6140$ vs.\ $0.5580$), and dense ($0.6230$ vs.\ $0.5810$) masks, and in real-simulator planning against 20 baselines ranks first on 2D Drift ($0.1960$) while HighDOF ($D=80$) evaluation is **TBD**.
- **After (Human Top-Venue — crisp claims, 0 decimals):**
  > On multi-step contact dynamics in ContactPush, ModelX outperforms larger matched-observation transformers across sparse and dense observation masks while remaining stable across extended unrolls. In real-simulator control, eight recurrent refinements rival or surpass matched-supervision baselines at lower wall-clock CPU latency.

### Pair 2 — Theory Narrative Prose
- **Before (Autoresearch/AI — defensive disclaimer jammed into 1 sentence):**
  > Proposition 2 contrasts assumed dimension-independent contraction inside a bounded residual band against an $\Omega(D\log(d_0/\varepsilon))$ lower bound for fixed-covariance isotropic unweighted elite-mean samplers.
- **After (Human Top-Venue — intuition first, exact assumptions isolated in Proposition 2):**
  > Proposition~\ref{prop:2} characterizes when a contractive learned update reaches tolerance $\varepsilon$ in $O(\log(d_0/\varepsilon))$ steps, and contrasts this regime with the $\Omega(D\log(d_0/\varepsilon))$ sample complexity established for fixed-covariance isotropic elite-mean search (with exact contraction and sampler assumptions stated in §\ref{sec:4}).

## 5. Two-Stage Content-Lock Rewrite Protocol

Never "polish" an autoresearch draft in place — local edits preserve the bloated sentence skeleton and scorecard rhythm. Always use two clean-room stages:
- **Stage A — Fact Lock (`FACT_LEDGER.md`):** Extract every verified metric, paired CI, ablation, exact theorem hypothesis, and pending `TBD` boundary from authoritative JSONs into a read-only Fact Ledger.
- **Stage B — Clean-Room Fresh Rewrite:** Close the old narrative sections (`Abstract`, `Introduction`, `Discussion & Limitations`, and prose paragraphs of `Method`/`Experiments`). Draft fresh prose solely from `FACT_LEDGER.md` + this Style DNA + one exemplar passage.
- **Stage C — Ledger Back-Check & Gate:** Verify every number, claim direction, and scope caveat against `FACT_LEDGER.md`, then run `check_sentence_clarity.py --submission-mode --strict-deai` and `paper_gate.py --compile`.

## 6. Idiomatic Academic Chinese Counterpart Rules

Draft Chinese sections directly from `FACT_LEDGER.md` and the scientific argument — never translate English sentences clause-by-clause:
1. **Kill telegraphic compressions & parenthetical scorecards:** Avoid parenthetical 4-decimal scorecards in Abstract/Intro prose; put exact tables in §5 and write clean Chinese claims in Abstract/Intro.
2. **Unpack English pre-modifier chains:** Break stacked pre-modifiers into natural verb/clause phrases, and keep `FP32` / seed specs in §5 rather than the Abstract.
3. **Natural cadence & zero calques:** Aim for 15–28 Han characters per sentence (soft limit 42, `<= 2` `的` per clause). Never put `TBD` inside Chinese Abstract/Intro/Conclusion sentences.
