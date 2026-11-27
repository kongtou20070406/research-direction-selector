# 10-Minute Reviewer Pre-Check Gate & Two-Stage Compute Protocol

Deep math and engineering cannot rescue a presentation whose main table or framing fails a 10-minute reviewer scan.

## 1. Pre-GPU 5-Question Reviewer Gate (Mandatory Before Launching New GPU Lines)

Before writing training scripts or spending GPU hours, an independent red-team reviewer (`independent-reviewer`, `-s read-only`) MUST verify these 5 questions on the planned experiment matrix:

1. **Standard Full-Schedule Benchmark?** Are we evaluating on the community-accepted standard dataset + full training protocol (not a diagnostic toy or truncated short schedule)?
2. **Direct Published Alignment (`Table 1..N`)?** Can every planned main table place our numbers side-by-side against matched-supervision and published SOTA rows without reinterpreting metrics or eval observation windows?
3. **Unmistakable Headline Margin & Symmetry?** Across `Table 1..N`, does the proposed method establish a clear win against **same-supervision peers** at matched cumulative compute/depth (`E[T]`) and end-to-end wall-clock latency (`ms`), plus real-environment zero-action baselines (with small incremental deltas tucked into ablations)?
4. **Claims Bounded by Evidence?** Does the title, abstract, and contribution list claim *strictly what the planned tables directly prove*—using standard community terminology (`world-model_venue-lexicon.md`) with zero jargon inflation?
5. **10-Minute Readability & Visual Anchor?** Is Figure 1 a crisp mechanism contrast that explains "Why does this beat baseline X?" in under 30 seconds, using short one-idea sentences (`plain-clear-writing`)?

## 2. Two-Stage Compute-Saving Protocol

Never burn multi-seed GPU budgets before an architectural or sampling change clears single-seed screening:

- **Stage 1 (Method Screening — Single Seed + Minimal Check)**:
  - Run **1 deterministic seed** (e.g. `seed 42` or official repo `seed 0`).
  - Match official preprocessing / frozen visual features / public benchmark protocol so published SOTA rows are copied cleanly for reference; locally train **only** our candidate method + **1 parameter-matched `T=1` control** (plus $\lambda=0$ ablation when isolating loss terms).
  - Require pre-registered Stage-1 pass criteria (plus `perf-ruler v2` Z3 formal speed-limit pre-check if latency/throughput is a core claim) before escalating.
- **Stage 2 (Main-Table Skeleton Lock -> Multi-Seed Freeze)**:
  - **Before launching Stage-2 multi-seed runs**, draft and freeze the **complete main-text table skeleton (`Table 1..N` rows, columns, baseline families, wall-clock `ms`, and eval-script observation windows)** and clear it with a read-only `independent-reviewer` so missing comparable baselines are caught before compute is spent.
  - Only after Stage 1 passes and the table skeleton is locked: expand to **3 seeds** (`0, 1, 2`), compute/depth-matched wide controls, donor/prefix ablations, and independent CPU audit recomputation.
