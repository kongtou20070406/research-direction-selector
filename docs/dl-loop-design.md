# Deep Learning Experiment Loop Mapping: Factorial Interaction Campaign

Status: Design & Pre-registration Specification  
Target Document: `docs/dl-loop-design.md`  
Target Branch: `release/6.0.0-compass`  
Target Commit Context: `3864dfa`

---

## 1. Executive Summary & Design Invariants

This design specifies the mapping of an empirical deep-learning (DL) experiment loop onto the frozen three-layer Compass architecture (`Adjudication`, `Evidence`, `Hypotheses`) defined in `docs/compass-general-20261021.md`. 

The benchmark task is to decide whether an interaction effect between learning rate ($lr \in \{10^{-3}, 10^{-2}\}$) and dropout ($p \in \{0, 0.5\}$) on validation accuracy ($val\_acc$) is genuinely real under 3 seeds per configuration and an absolute tolerance of 1 percentage point ($\tau = 0.01$).

### Epistemic & Architectural Invariants
1. **Single Authoritative Ledger**: Reuses existing `COMPASS_DECLARED`, `COMPASS_MEASURED`, and `COMPASS_HYPOTHESIS` event rows in SQLite `ProjectStore`. No parallel loop or second database.
2. **Zero-Guessing Tolerances**: Tolerances are frozen in the pre-registered declaration. The kernel never adjusts tolerances dynamically to force a verdict.
3. **Preservation of Interval Radii**: Repeated training runs over random seeds are represented exclusively as rigorous confidence enclosures (`Inexact(lo, hi)` with `radius = s * t / sqrt(n)`). Measurements never collapse into point estimates.
4. **Reduction of Comparative Hypotheses to Conservation Laws**: Comparative claims are mapped into invariant zero-difference predictions on derived observables, completely isolating mechanism testing from absolute baseline accuracy prediction.

---

## 2. Declarative Campaign Template

The declaration below adheres strictly to the schema enforced by `scripts/rds_compass_declaration.py`. All fields exist in the current codebase unless marked with `[New Field Stage X]`.

```json
{
  "schema": "rds-compass-declaration-v1",
  "goal": {
    "kind": "IDENTIFY_LAW",
    "statement": "Identify whether the second-order interaction effect between learning rate and dropout on validation accuracy is strictly zero within tolerance",
    "observable": "interaction_delta"
  },
  "inputs": {
    "pair_id": {
      "min": 1,
      "max": 1,
      "integer": true
    }
  },
  "observables": {
    "interaction_delta": {
      "tolerance": {
        "abs": "0.01"
      },
      "higher_is_better": true
    }
  },
  "experiment": {
    "argv": [
      "{python}",
      "-B",
      "runner/dl_factorial_runner.py",
      "--grid",
      "lr=1e-3,1e-2;dropout=0,0.5",
      "--seeds",
      "3",
      "--output",
      "{output}"
    ],
    "files": {
      "code": [
        "runner/dl_factorial_runner.py",
        "models/network.py"
      ],
      "config": [
        "configs/train_base.json"
      ],
      "data": [
        "data/manifest.json"
      ],
      "evaluator": [
        "eval/verify_interaction.py"
      ]
    },
    "timeout_seconds": 3600,
    "wall_seconds": 3700
  },
  "candidates": [
    {
      "id": "factorial_quad_01",
      "inputs": {
        "pair_id": 1
      },
      "cost": 12,
      "description": "2x2 factorial grid over lr in {1e-3, 1e-2} and dropout in {0, 0.5}, 3 seeds per cell"
    }
  ],
  "hypotheses": [
    {
      "id": "null_no_interaction",
      "statement": "Additive null: second-order interaction delta is strictly zero",
      "predict": {
        "interaction_delta": "0"
      },
      "origin": "declared"
    },
    {
      "id": "rival_positive_synergy",
      "statement": "Hypothesized synergy: high learning rate benefits disproportionately from dropout regularization",
      "predict": {
        "interaction_delta": "3/100"
      },
      "origin": "declared"
    }
  ],
  "budget": {
    "wall_seconds": 4000,
    "experiments": 1,
    "max_experiments": 2
  },
  "verification": {
    "confirmations": 1,
    "spend_budget": false,
    "confirm_by_eig": false,
    "holdout": 0,
    "holdout_seed": 357,
    "max_gap": 0
  }
}
```

---

## 3. Detailed Structural Mapping

### 3.1 Candidate Semantics: Seeds vs Configuration Inputs
- **Decision**: **Seeds must be treated as measurement-internal replicates, NOT as candidate inputs.**
- **Rationale**:
  1. *Ontological Distinction*: Hyperparameters ($lr, dropout$) are controllable system configuration states. Random seeds ($s \in \{1, 2, 3\}$) are stochastic realization variables representing shot noise in parameter initialization and data shuffling. Treating seeds as candidate inputs pollutes the candidate space with artificial state dimensions.
  2. *Statistical Protection via Inexact Enclosures*: In `scripts/rds_compass_model.py:500`, the `decide` kernel evaluates:
     $$\text{radius} = \text{radius}_{pred} + \text{radius}_{obs}$$
     $$\text{gap} = |pred - obs|$$
     If each seed were an independent candidate, each cell would emit a point estimate ($\text{radius} = 0$). Ordinary run-to-run stochastic jitter would immediately puncture the tight tolerance band ($\tau = 0.01$), yielding spurious counterexamples (`REFUTED`). By encapsulating 3 seeds inside the candidate execution, the runner computes:
     $$\bar{x} = \frac{1}{n} \sum_{i=1}^n x_i, \quad s = \sqrt{\frac{1}{n-1} \sum_{i=1}^n (x_i - \bar{x})^2}$$
     $$\text{half\_width} = \frac{s \cdot t_{\alpha/2, n-1}}{\sqrt{n}}$$
     and writes `{"lo": text(x_bar - half_width), "hi": text(x_bar + half_width)}`. The measurement enters the kernel as `Inexact(lo, hi)`, keeping statistical noise honestly bounded.
  3. *Adherence to Existing Declarations*: In `scripts/rds_compass_declaration.py:210`, the `replicates` key is rejected under `IDENTIFY_LAW` (`require(goal['kind'] == 'IMPROVE')`). Packaging the replicate execution inside the runner preserves compatibility with existing declaration checks without introducing illegal schema fields.

### 3.2 Observable Definition and Tolerance Declaration
- **Observable Name**: `interaction_delta`.
- **Mathematical Formulation**:
  $$\Delta_{inter} = \left( val\_acc(lr=10^{-2}, drop=0.5) - val\_acc(lr=10^{-2}, drop=0) \right) - \left( val\_acc(lr=10^{-3}, drop=0.5) - val\_acc(lr=10^{-3}, drop=0) \right)$$
- **Tolerance**:
  `tolerance: {"abs": "0.01"}`.
  An absolute tolerance of 1 percentage point reflects the minimum scientifically relevant effect size declared by the researcher.

### 3.3 Hypotheses Reduction: Comparative to Predictive
- **The Friction**: DL hypotheses are comparative assertions (e.g., "interaction exists" vs "interaction is additive"), whereas Compass hypotheses are predictive rules (`predict: {observable: expression}`).
- **The Breakthrough Reduction**: 
  In an additive system (zero interaction), the double-difference $\Delta_{inter}$ is mathematically invariant and equal to **0**.
  Therefore:
  - Null Hypothesis (`null_no_interaction`): `predict: {"interaction_delta": "0"}`.
  - Rival Hypothesis (`rival_positive_synergy`): `predict: {"interaction_delta": "3/100"}` (predicting a 3% synergy).
- **Epistemic Advantage**:
  This reduction eliminates *Auxiliary Hypothesis Pollution*. The researcher does not need to guess the baseline accuracy (e.g., whether baseline accuracy is 82.5% or 83.1%). The prediction is anchored entirely to an algebraic conservation property ($\Delta = 0$).

### 3.4 Budget Specification
- **`wall_seconds`**: 4000s, providing sufficient headroom over the experiment runner execution (3700s).
- **`experiments`**: 1 slot (since the 4 grid configurations $\times$ 3 seeds are executed in one orchestrated factorial runner).
- **`max_experiments`**: 2 slots, reserving exactly one retry attempt in case of machine-level or transient CUDA interruption (`scripts/rds_compass_declaration.py:214`).

### 3.5 The Role of `UNDECIDED`: A Legitimate First-Class Scientific State
- When measurement variance is wide (e.g., $\text{radius}_{obs} = 0.015$), the confidence band straddles the tolerance boundary ($\tau = 0.01$).
- In `scripts/rds_compass_model.py:597`, `decide()` returns `None`. `replay()` tallies this under `undecided += 1` and returns `status: 'UNTESTED'` / `UNKNOWN`.
- **Epistemic Meaning**: `UNDECIDED` is not an engine failure. It is an honest scientific verdict: **the statistical power of 3 seeds is insufficient to resolve whether an effect smaller than 1% exists**. It directs the researcher to increase $n$, rather than hallucinating consistency or refutation.

---

## 4. Rigorous Answers to the Three Hard Questions

### Question 1: The $O(k^2)$ Candidate Explosion
> *What are the limits of pairwise candidates? At what scale does it break? Is there a cheaper representation?*

1. **Failure Thresholds**:
   - **Kernel Architectural Limit**: `scripts/rds_compass_declaration.py:26` strictly enforces:
     ```python
     MAX_CANDIDATES = 64
     ```
     For an unconstrained pairwise comparison $\binom{k}{2}$, $k=12$ configurations generate 66 pairs ($> 64$), triggering an immediate fail-closed ingest rejection (`compile_plan`). For directed pairs $k(k-1)$, $k=9$ generates 72 pairs ($> 64$).
   - **Compute Ceiling**: In DL, running independent configurations is costly. Comparing $k=8$ configurations pairwise implies $\binom{8}{2} = 28$ candidate cells. If each pair is executed independently, 56 training runs are dispatched. At 20 minutes per run, this consumes 18.6 GPU-hours. At $k=16$, 120 pairs require 80 GPU-hours. In practice, **pairwise comparison becomes economically unacceptable at $k \ge 8$ and architecturally invalid at $k \ge 12$**.
2. **Cheaper Structural Representations**:
   - **Representation A: Star-Graph / Anchor-Relative Delta ($O(k)$)**:
     Select one canonical baseline (e.g., $c_{base} = c_{00}: (lr=10^{-3}, drop=0)$). Declare observables as deltas relative to this anchor:
     $$\Delta_{acc}(c_i) = val\_acc(c_i) - val\_acc(c_{base})$$
     Number of candidates scales linearly: exactly $k-1$ candidates. For $k=8$, this is 7 candidates instead of 28. Additivity is tested via:
     $$\Delta_{acc}(c_{11}) = \Delta_{acc}(c_{10}) + \Delta_{acc}(c_{01})$$
   - **Representation B: Factorial Cell Candidate ($O(1)$ or $O(\text{subspaces})$)**:
     As adopted in our primary template (§2), package the $2 \times 2$ grid into a single candidate evaluation unit whose output is the composite statistic $\Delta_{inter}$. Candidate count is reduced from 6 to 1.
   - **Representation C: Decoupled CAS Replay (Stage 3 Capability)**:
     The execution layer evaluates and caches raw configuration runs $\{c_i\}_{i=1}^k$ once into Content-Addressed Storage (`CAS`). The Compass adjudication layer constructs pairwise comparisons virtually by referencing cached execution receipts, eliminating redundant training overhead.

---

### Question 2: Who Sets Noise Tolerance & Handling `UNDECIDED`
> *Tolerances are user-declared. If declared tolerance is smaller than seed variance, producing widespread `None` (UNDECIDED), how should the system react? Request seeds? Alter tolerance? Report insufficient evidence?*

1. **Non-Negotiable Rule**: **The system must NEVER alter the tolerance.** Modifying user tolerance post-hoc to force a verdict is scientific falsification and directly violates the project contract (`Task Truth != Scientific Support`).
2. **Mathematical Condition for Total Stagnation**:
   In `scripts/rds_compass_model.py:505-508`:
   ```python
   if gap + radius <= absolute + relative * abs(observed) - spread:
       return True
   if gap - radius > absolute + relative * abs(observed) + spread:
       return False
   return None
   ```
   If $\text{radius}_{obs} > \tau_{abs}$, then because $\text{gap} \ge 0$, $\text{gap} + \text{radius} > \tau_{abs}$ is identically true. **A verdict of `True` (`CONSISTENT`) is mathematically impossible.** The system can only oscillate between `False` (if the gap is enormous) and `None`.
3. **Tri-Level Adjudication Policy**:
   - **Level 1: Pre-flight Resolution Gate (`STATISTICAL_POWER_DEFICIT`)**:
     Upon receiving output receipts, the kernel evaluates the resolution ratio:
     $$\rho = \frac{\text{radius}_{obs}}{\tau_{abs}}$$
     If $\rho > 1.0$, the measurement is tagged with `STATISTICAL_POWER_DEFICIT`.
   - **Level 2: Budget-Bounded Adaptive Seed Expansion**:
     If unspent experiment budget remains in `budget.experiments`, the controller does not guess. It computes the required sample size using the empirical variance $s^2$:
     $$n^* = \left\lceil \left( \frac{t_{\alpha/2, n-1} \cdot s}{\tau_{abs}} \right)^2 \right\rceil$$
     If $n^*$ is within candidate retry allowance, an adaptive follow-up run is dispatched to narrow the enclosure radius below $\tau_{abs}$.
   - **Level 3: Exhausted Budget Adjudication (`INSUFFICIENT_EVIDENCE`)**:
     If budget is exhausted and $\rho > 1.0$ persists, the campaign terminates with:
     ```json
     {
       "status": "INSUFFICIENT_EVIDENCE",
       "reason": "OBSERVATION_RADIUS_EXCEEDS_TOLERANCE",
       "observed_half_width": "0.0162",
       "declared_tolerance": "0.0100",
       "remedy": "Increase seeds from 3 to at least 8 or pre-register a wider tolerance"
     }
     ```
     This verdict is fail-closed, rigorous, and actionable.

---

### Question 3: False Certification Vulnerabilities & Detectors
> *What experimental artifacts could be falsely certified as a "discovered interaction effect"? Provide at least two scenarios and their detectors.*

#### Scenario 1: Optimization Dynamics & Truncation Artifact (Epoch Truncation)
- **Failure Mechanism**:
  Dropout introduces stochastic gradient regularization, which systematically retards the initial training velocity. Under a fixed training horizon (e.g., 30 epochs):
  - Configuration $(lr=10^{-3}, drop=0)$ converges smoothly.
  - Configuration $(lr=10^{-2}, drop=0)$ converges rapidly.
  - Configuration $(lr=10^{-3}, drop=0.5)$ experiences severe underfitting at epoch 30 due to small steps combined with regularization.
  - Configuration $(lr=10^{-2}, drop=0.5)$ trains at an optimal effective learning rate and achieves near-peak performance.
  Evaluating validation accuracy at epoch 30 produces an apparent strong positive interaction:
  $$\Delta_{inter} = (acc_{11} - acc_{10}) - (acc_{01} - acc_{00}) \gg +0.01$$
  The system refutes the null and declares a "discovery of synergy". In truth, this is an **epoch-truncation artifact**. If trained to convergence (100 epochs), the gap disappears.
- **Detector Protocol: Convergence Rate-of-Change Gate**:
  In `experiment.files.evaluator`, register a mandatory stationarity check:
  $$\left| \frac{\Delta \mathcal{L}_{val}}{\Delta \text{epoch}} \right|_{\text{last 10\% epochs}} \le \epsilon_{conv}$$
  If any cell in the $2 \times 2$ grid fails the stationarity threshold, the measurement is marked `UNCONVERGED_MEASUREMENT`. A claim of interaction is blocked from adjudication until convergence invariants are satisfied.

#### Scenario 2: Covariance Leakage via Identical Seed Re-use
- **Failure Mechanism**:
  If the experiment runner executes the 4 grid configurations using identical deterministic seed indices `[1, 2, 3]` without independent PRNG stream separation:
  Every configuration receives the identical mini-batch sequence ordering and data-loader augmentation sequence. The sampling errors across configurations become positively correlated ($\text{Cov}(acc_A, acc_B) > 0$).
  If the runner estimates difference variance using naive independent variance addition:
  $$\sigma^2_{diff} = \sigma^2_A + \sigma^2_B \quad (\text{ignoring } - 2\text{Cov}(A, B))$$
  a single lucky batch draw that favors a specific architectural configuration will systematically bias the point estimate while misrepresenting the enclosure bounds. The confidence band artificially separates from zero, producing a false certification of interaction.
- **Detector Protocol: Holdout Independent Seed Resampling**:
  Enforce the holdout verification seam (`scripts/rds_compass_declaration.py:356`):
  Before certifying `GOAL_IDENTIFIED`, the verifier automatically schedules confirmation runs using a high-entropy seed set completely disjoint from the development seeds (e.g., `seeds in {101, 102, 103}`).
  If the interaction enclosure collapses back across zero under fresh seeds, the claim is rejected with `REPRODUCIBILITY_FAILURE: SEED_COVARIANCE_DETECTED`.

#### Scenario 3: Metric Non-Linearity & Ceiling Compression
- **Failure Mechanism**:
  Validation accuracy is bounded in $[0, 1]$. If baseline accuracy at $(lr=10^{-3}, drop=0)$ is already 98.2%, increasing learning rate can at most yield $+1.0\%$ (reaching 99.2%) due to saturation. If at $drop=0.5$ baseline accuracy is 85.0%, increasing learning rate yields $+5.0\%$ (reaching 90.0%).
  The double-difference yields:
  $$\Delta_{inter} = 5.0\% - 1.0\% = +4.0\% > 1.0\%$$
  The kernel refutes additivity. However, this interaction is purely a mathematical artifact of ceiling compression on a bounded metric. Under a linear loss scale (e.g., Cross-Entropy Loss), the effects are strictly additive.
- **Detector Protocol: Dual-Metric Scale Agreement Gate**:
  Declare dual observables: `interaction_delta_acc` and `interaction_delta_loss`.
  A claim of interaction is accepted if and only if:
  $$\text{Sign}(\Delta_{acc}) = -\text{Sign}(\Delta_{loss}) \quad \text{and both exceed their respective tolerances.}$$
  If loss is additive while accuracy shows an interaction, the system flags `SCALE_NONLINEARITY_ARTIFACT`.

---

## 5. Required RDS Capabilities: Stage 2 & Stage 3 Gap Inventory

| Stage | Feature / Gap | Current State | Required Modification in RDS |
| :--- | :--- | :--- | :--- |
| **Stage 2** | Pre-registered Declared Hypotheses Seam | Pre-registered in `docs/stage2-hypothesis-protocol.md`, not yet merged into `scripts/rds_compass.py` | Implement the single branch at the head of `_fits` in `scripts/rds_compass.py` to bypass `synthesize` when `hypotheses` block is present. |
| **Stage 2** | Constant Value AST Prediction | `compile_hypothesis` handles variable ASTs | Ensure rational scalar constants (`"0"`, `"3/100"`) compile cleanly as root AST leaves in `scripts/rds_compass_model.py:543`. |
| **Stage 2** | Fail-Closed Unknown Field Validation | `_object` in `declaration.py` validates known fields | Ensure `hypotheses` objects strictly reject undeclared provenance or unparseable observables at declaration ingest. |
| **Stage 3** | Derived Multi-Cell Observables Engine | Measurements are strictly 1-to-1 with candidate executions | Allow declaration of derived observables synthesized across distinct raw candidate receipts stored in `ProjectStore` CAS. |
| **Stage 3** | Adaptive Replicates Controller | Candidate replicates are static | Add an adaptive execution phase in `rds_compass_drive.py` that allocates unspent budget to expand seeds when $\rho > 1.0$. |
| **Stage 3** | Dual-Metric Verification Gate | Single target observable per `IDENTIFY_LAW` goal | Extend `verification` in `rds_compass.py` to require joint consistency across conjugate metrics (Accuracy + Loss). |

---

## 6. Pre-Registration Acceptance Gate

To verify this design before code execution, the following mechanized acceptance criteria must be satisfied:

```python
def test_dl_loop_declaration_contract():
    # 1. Declaration compiles without exception under existing declaration schema
    contract, protocol_raw, plan = compile_plan(store, declaration, source_sha256)
    assert plan['goal']['kind'] == 'IDENTIFY_LAW'
    assert 'interaction_delta' in plan['observables']
    
    # 2. Null hypothesis compiles to exact constant zero AST
    null_hypo = [h for h in plan['hypotheses'] if h['id'] == 'null_no_interaction'][0]
    assert null_hypo['predict']['interaction_delta'] == parse('0', set(plan['inputs']))
    
    # 3. Decision kernel produces None when observation radius straddles tolerance
    noisy_obs = exact({'lo': '-0.015', 'hi': '0.005'})
    assert decide(exact('0'), noisy_obs, tolerance({'abs': '0.01'})) is None
    
    # 4. Decision kernel refutes null when interaction cleanly separates from zero
    real_effect = exact({'lo': '0.025', 'hi': '0.035'})
    assert decide(exact('0'), real_effect, tolerance({'abs': '0.01'})) is False
```
