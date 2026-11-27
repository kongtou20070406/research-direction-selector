# 2025–2026 Frontier Anchors & Obelisk Evidence Ledger

Companion reference for [SKILL.md](../SKILL.md). Every rule in `SKILL.md` is grounded in (1) current 2025–2026 deep learning literature and (2) verified experimental evidence recovered via `Obelisk` and project logs.

---

## 1. Latest 2025–2026 Literature Anchors

| Research Line | Earlier Heuristic | 2025–2026 Frontier Consensus | Concrete Minimalist Consequence |
|---|---|---|---|
| **Recurrent Latent Reasoning** | Deep equilibrium fixed-point solvers, dual-frequency brain metaphors (`HRM`), unnormalized recurrent MLPs | **`TRM`** (*Less is More*, Oct 2025; audit `arXiv:2512.11847v2`): single tiny 2-layer Pre-LN network recursing with input injection.<br>**`Huginn`** (2025): `Prelude -> Shared Pre-LN Recurrent Block -> Coda`. | Keep one standard **Pre-LN Transformer** core with input injection. Avoid nested multiplicative gates without Pre-LN and avoid hand-coded stability clamps. |
| **Tokenizer-Free Abstraction** | Fixed pixel patches, BPE vocabularies, or brittle hard keep/drop thresholding (`p > 0.5` + STE) | **`H-Net`** (Hwang et al., Jul 2025) motivated raw-atom operation without fixed tokenizers; **`Perceiver IO` / Slot Attention / Cross-Attention Bottlenecks** achieve smooth variable-length $L \leftrightarrow K$ abstraction with zero discontinuous step functions. | Start from raw atoms `(value, coords)` so no domain tokenizer is needed, but compress via **smooth `softmax` Cross-Attention ($L \to K \to L$)** instead of brittle hard threshold routing. |
| **In-Context / Test-Time Memory** | Explicit Ridge/Cholesky matrix solvers or manual statistical summaries inside the forward pass | **`Gated DeltaNet` (2025) $\to$ `Titans` (2025) $\to$ `ATLAS` (mid-2025) $\to$ `TTT-E2E` (2026)**, plus standard Support–Query Cross-Attention on moderate support sets. | Let linear superposition and nonlinear composition emerge from standard **Cross-Attention** (or differentiable delta-rule fast weights) rather than embedded linear-algebra solvers. |
| **Latent World Models** | 5–7 term VICReg/JEPA/Dreamer objective soups, EMA momentum encoders, or detached inverse-action side heads | **`LeWorldModel` (`LeWM`)** (`arXiv:2603.19312v3`, Mar 2026): **2 terms only** — latent next-state prediction MSE + parameter-free `SIGReg` (1D sliced Gaussian test, `0` EMA).<br>**`Σ-LeWM`** (`arXiv:2611.12732v1`, Nov 2026): observable transitions.<br>**`PlanningObjective`** (`arXiv:2608.12959v1`, Aug 2026): representations trained on detached decoder heads do not reliably serve planners. | Call something a **World Loss** only if it trains the **exact same $E_\theta$ and $F_\theta$** to predict **world state transitions ($x \to y$)** — via in-episode transition prediction (`0` extra knobs) or `LeWM` latent transition + `SIGReg` (`<= 1` scalar, ablated vs `0`). |
| **Consumer Equivalence** | Matching Euclidean state vectors `||h_t - h'_t||` across views or steps | **Glasgow & Bruna** (*Uniform-in-Time Weak Propagation of Chaos*, `arXiv:2605.22010v3`, Nov 2026, NeurIPS Oral): measure behavioral sensitivity through actual downstream consumers $d_{\mathcal{C}}^2(s, s')$, not raw state norm or step-to-step freezing. | Never force `h_{t+1} == h_t` across loop steps; verify improvement directly through fixed-weight rollout curves (`T = 1..16`) and consumer behavior. |

---

## 2. Anonymized Empirical Post-Mortems (Do Not Repeat)

| Pattern | Verified Failure Mode | Permanent Design Rule Derived |
|---|---|---|
| **Teacher-Forced Rollout Training** | Training with noisy ground-truth states while test-time rollout feeds back self-generated predictions causes compounding carry collapse. | Train recurrent loops on their **own self-generated carry states** (`k` no-grad self-steps + gradient step). |
| **Hard Binary Keep-Mask Routing** | Hard threshold keep-mask routing can over-select padding/noise tokens over real content tokens. | Reject brittle binary routing masks; use smooth differentiable `softmax` Cross-Attention slot pooling. |
| **Unregularized Latent Target Matching** | Adding auxiliary latent-target or blind state-replay losses without variance/covariance regularization (`SIGReg`) degrades generalization vs. compute-matched training. | Never use unregularized latent target matching without `SIGReg`, and never add blind state-replay losses without a causal ablation win. |
| **Episode-Internal Transition Supervision** | Re-partitioning existing episode trajectories into support/query pairs expands direct transition supervision without adding data or auxiliary heads. | Prefer **episode-internal transition prediction** using the **same network and task loss** over ad-hoc auxiliary heads. |
| **In-Context Support Cross-Attention** | Recurrent reading from in-context support improves unseen generalization, provided explicit dataset-specific graph priors are stripped. | Validate cross-attention readers across heterogeneous domains without dataset-specific priors. |
| **Discrete Routing + BF16/FP32 Drift** | Embedding Ridge/Cholesky solvers or hard `p > 0.5`/STE gating flips discrete chunk boundaries between BF16 and FP32. | Keep forward passes smooth and verify BF16-vs-FP32 numerical parity directly. |

---

## 3. Minimalist 4-Equation Cross-Domain Architecture Blueprint

When training **one single shared model with one shared hyperparameter set** simultaneously on **2D Compositional-ARC** + **Non-Grid Multi-Step Task** (`50/50` mixed batches, zero task-specific layers inside the core):

1. **Thin Domain Atom Adapter (`Prelude`)**: map raw domain elements into unified tokens `(x_ctx, y_ctx, x_q)` of shape `[B, L, d_model]`, preserving continuous/discrete values + normalized coordinates/roles only (zero task ID tokens, zero hand-crafted summary statistics, zero fixed patching).
2. **Smooth Abstraction (`L -> K` Cross-Attention Slots)**: $K$ learned query slots cross-attend smoothly over support transition tokens to form episode context representation $S = \operatorname{XAttn}(Q_{\text{slot}}, K_{\text{sup}}, V_{\text{sup}})$ with `0` routing thresholds or STE.
3. **Shared Pre-LN Recurrent Transition Core (`Huginn / TRM` Style)**: initialize $h_0 = E_\theta(x_q)$; for $t = 0 \dots T-1$,
   $$h_{t+1} = h_t + \operatorname{Block}_\theta\bigl(\operatorname{RMSNorm}(h_t),\; \text{context}=[E_\theta(x_q), S]\bigr)$$
   using `k`-step warmup + 1-step grad during training ($k \sim \{0..3\}$ self-rollout + 1 grad step) to match inference dynamics.
4. **Thin Shared Readout (`Coda`) + Minimal Objective**: decode $\hat{y}_T = C_\theta(h_T)$ on both $x_q$ and held-out in-episode support transitions $(x_m \to y_m)$ using the primary observable task loss (plus optional `LeWM` `||F(E(x_m)) - E(y_m)||^2 + SIGReg` only as a 1-scalar controlled ablation against `lambda_world = 0`).
