# 10-Minute Reviewer Pre-Check Gate & Two-Stage Compute Protocol

Deep math and engineering cannot rescue a presentation whose main table or framing fails a 10-minute reviewer scan.

## 1. Pre-GPU 5-Question Reviewer Gate (Mandatory Before Launching New GPU Lines)

Before writing training scripts or spending GPU hours, an independent red-team reviewer (`independent-reviewer`, `-s read-only`) MUST verify these 5 questions on the planned experiment matrix:

1. **Standard Full-Schedule Benchmark?** Are we evaluating on the community-accepted standard dataset + full training protocol (not a diagnostic toy or truncated short schedule)?
2. **Direct Published Table 1 Alignment?** Can our main Table 1 place our numbers side-by-side against published SOTA rows from the literature without reinterpreting metrics?
3. **Unmistakable Headline Margin?** On Table 1, does the proposed method establish a clear primary win (accuracy, rollout stability, or qualitative new capability), with small incremental deltas (`+0.02` to `+0.08`) tucked cleanly into ablations?
4. **Claims Bounded by Table 1?** Does the title, abstract, and contribution list claim *strictly what Table 1 directly proves*—using standard community terminology (`world-model_venue-lexicon.md`) with zero jargon inflation?
5. **10-Minute Readability & Visual Anchor?** Is Figure 1 a crisp mechanism contrast that explains "Why does this beat baseline X?" in under 30 seconds, using short one-idea sentences (`plain-clear-writing`)?

## 2. Two-Stage Compute-Saving Protocol

Never burn multi-seed GPU budgets before an architectural or sampling change clears single-seed screening:

- **Stage 1 (Method Screening — Single Seed + Official Table Reuse)**:
  - Run **1 deterministic seed** (e.g. `seed 42` or official repo `seed 0`).
  - Match official preprocessing / frozen visual features / public benchmark protocol so published SOTA rows are copied cleanly for reference; locally train **only** our candidate method + **1 parameter-matched `T=1` control** (plus $\lambda=0$ ablation when isolating loss terms).
  - Require pre-registered Stage-1 pass criteria (plus `perf-ruler v2` Z3 formal speed-limit pre-check if latency/throughput is a core claim) before escalating.
- **Stage 2 (Submission Freeze — Multi-Seed + Full Ablation Grid)**:
  - Only after Stage 1 passes and the method is frozen: expand to **3 seeds** (`0, 1, 2`), compute-matched wide controls, donor/prefix ablations, and independent CPU audit recomputation.
