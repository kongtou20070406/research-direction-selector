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

## 2. Verified `Obelisk` Empirical Post-Mortems (Do Not Repeat)

| Date | Experiment / Diagnostic | Verified Observation | Permanent Design Rule Derived |
|---|---|---|---|
| **2026-11-03** | `Probe H` (`frm_arc_v0`, Obelisk `arc_probe_conf_rule_shared`) | Training saw noisy ground-truth states (`level * gt + noise`) while test-time rollout fed back self-generated guesses, causing carry collapse (`R16 .32`, re-saturating `.90`). | Train the loop on its **own self-generated carry states** (`nograd_k_1step`: `k` no-grad self-steps + gradient step). |
| **2026-11-11** | `dc_b` Hard Keep Routing (`arc_probe_dc_keep.json`, Obelisk) | Hard threshold keep-mask routing selected **`pad` tokens (`39.4%`) more often than `content` tokens (`26.9%`)** (`final_exact = 1/31`). | Reject brittle binary routing masks; use smooth differentiable `softmax` Cross-Attention slot pooling. |
| **2026-11-13** | `baseline_targets_example` & `aux_loss_ablation_example` (`RESULTS.md`) | (1) `CE + 0.1 * encoder(y)` with `SIGReg/Gaussian = 0` scored `0/16` exact on real ARC (`NLL 0.9926 vs 0.9107`).<br>(2) Blind full-state/KV replay losses (`38.28% / 41.99%`) lost to compute-matched ordinary training (`47.85%`). | Never use unregularized latent target matching without `SIGReg`, and never add blind state-replay losses without a causal ablation win. |
| **2026-11-15** | `homm_rule_recurrence_20261115` (`RESULTS.md` lines 22–37) | Re-partitioning the same 40 episode points (`32 support / 8 query`, expanding direct transition supervision without adding data or auxiliary heads) improved unseen dev RMSE `0.364115 -> 0.255019` (`0/128 -> 1/128`). | **Episode-internal transition prediction** across multiple `(x_m -> y_m)` pairs using the **same network and task loss** is a clean, zero-hyperparameter world-transition supervision mechanism. |
| **2026-11-16** | `Stage1B` & `hnet_closure` (`RESULTS.md`, `model_clean.py`) | Adding recurrent cross-attention to support memory lifted L1 unseen dev accuracy from `37.70%` (`193/512`) to `65.04%` (`333/512`, `hard = 57.47%`), though `8 -> 16` still had net `-13` and relied on explicit graph edges. | Recurrent reading from in-context support helps generalization; next step must strip explicit graph-edge priors and validate across heterogeneous domains. |
| **2026-11-17** | `universal_hnet_loop_20261117` (`CURRENT.md`, `PRECISION_FAILURE_preln.json`) | Cholesky solver + manual 2nd-order features + `p > 0.5`/STE + 6-term loss (`InverseActionHead`, `CQR`, contraction hinge) on Norman:<br>- `compiler_only`: `T16 DE20 = 0.9377`, `FP32diff = 0.0016` (PASS)<br>- `hnet_single_step`: `T16 DE20 = 0.5881`, `FP32diff = 1.923` (FAIL)<br>- `hnet_loop_obs_only`: `T16 DE20 = 0.9145`, `FP32diff = 0.0146` (FAIL)<br>- `hnet_loop_world_cqr`: `T16 DE20 = 0.9197`, `FP32diff = 0.0387` (FAIL) | (1) Never embed Ridge/Cholesky solvers or tune an entire architecture to a 94%-linear tabular benchmark.<br>(2) Hard `p > 0.5` routing flips discrete chunk boundaries between BF16 and FP32.<br>(3) Detached `InverseActionHead` + variance smoothing is **not** a world model loss. |

---

## 3. Minimalist 4-Equation Cross-Domain Architecture Blueprint

When training **one single shared model with one shared hyperparameter set** simultaneously on **2D Compositional-ARC** + **Non-Grid Multi-Step Task** (`50/50` mixed batches, zero task-specific layers inside the core):

1. **Thin Domain Atom Adapter (`Prelude`)**: map raw domain elements into unified tokens `(x_ctx, y_ctx, x_q)` of shape `[B, L, d_model]`, preserving continuous/discrete values + normalized coordinates/roles only (zero task ID tokens, zero hand-crafted summary statistics, zero fixed patching).
2. **Smooth Abstraction (`L -> K` Cross-Attention Slots)**: $K$ learned query slots cross-attend smoothly over support transition tokens to form episode context representation $S = \operatorname{XAttn}(Q_{\text{slot}}, K_{\text{sup}}, V_{\text{sup}})$ with `0` routing thresholds or STE.
3. **Shared Pre-LN Recurrent Transition Core (`Huginn / TRM` Style)**: initialize $h_0 = E_\theta(x_q)$; for $t = 0 \dots T-1$,
   $$h_{t+1} = h_t + \operatorname{Block}_\theta\bigl(\operatorname{RMSNorm}(h_t),\; \text{context}=[E_\theta(x_q), S]\bigr)$$
   using `nograd_k_1step` during training ($k \sim \{0..3\}$ self-rollout + 1 grad step) to match inference dynamics.
4. **Thin Shared Readout (`Coda`) + Minimal Objective**: decode $\hat{y}_T = C_\theta(h_T)$ on both $x_q$ and held-out in-episode support transitions $(x_m \to y_m)$ using the primary observable task loss (plus optional `LeWM` `||F(E(x_m)) - E(y_m)||^2 + SIGReg` only as a 1-scalar controlled ablation against `lambda_world = 0`).
