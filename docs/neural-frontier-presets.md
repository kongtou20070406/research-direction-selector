# Neural frontier presets (v2): thirty findings that break the textbook picture

Written 2026-10-24 for agents working on RDS. Purpose: replace stale priors about
how deep networks learn, so experiment design is not shaped by the 2018 picture.

Each entry: the claim, why it violates the usual intuition, the source, and what it
means for how we design and judge DL experiments. `[摘要]` means the claim was read
from an abstract or HTML body and was **not** re-run locally. `[经典]` marks a
result older than 2024 that is still widely ignored in practice.

---

## The organising idea

**Learning has two timescales, and standard training only optimises the first.**

Fitting the training set is fast. Compressing the solution - discarding input
information that does not predict the label - is slow, and gradient descent does
not actively accelerate it. Generalisation arrives when the second phase finishes,
which is why test performance can jump long after training loss is already zero.

> "We argue that the second phase is not actively optimized by standard training
> algorithms and may be unnecessarily prolonged." - Phys. Rev. E, on grokking,
> double descent and the information bottleneck sharing one temporal structure.

Everything below is a consequence, a sibling, or a counterexample to this.

---

## A. Training dynamics and generalisation phase transitions

**A1. Grokking is a training-condition property, not an architecture property.**
Depth has a non-monotonic effect: depth-4 MLPs consistently fail to grok while
depth-8 residual networks recover. Under matched hyperparameters the
Transformer-vs-MLP gap collapses from a reported 2.18x to **1.11x** (45,600 vs
50,800 steps, 10/10 seeds grokked). Weight decay is the dominant control, with a
narrow Goldilocks band. `[摘要]`
- *Surprising because*: the folk story is "Transformers grok, MLPs don't".
- *Source*: arXiv:2603.25009 (2026-03).
- *Meaning*: when a run fails to generalise, hypothesise the regularisation
  regime before the architecture. Varying architecture at one weight decay value
  is confounded and must be labelled so.

**A2. Grokking is provable, and therefore not an inherent failure mode.**
End-to-end proof in over-parameterised ridge regression with weight decay: (i)
overfit early, (ii) poor generalisation persists long after overfitting, (iii)
generalisation error eventually becomes arbitrarily small. Quantitative bounds on
"grokking time" in terms of hyperparameters, and the delay can be made arbitrarily
large or eliminated by tuning. `[摘要]`
- *Surprising because*: it looks like a pathology of deep nets; it reproduces in
  a model with a closed-form solution.
- *Source*: arXiv:2601.19791 (2026-07).
- *Meaning*: "it did not generalise" is a statement about conditions, not capacity.

**A3. Grokking, double descent and the information bottleneck are one phenomenon.**
All three are the late onset of generalisation after training error hits zero, and
their timescales align across settings. Mutual information between hidden layers
and input is proposed as the natural progress measure. A neural-collapse account
makes it quantitative: contraction of population within-class variance underlies
both, and weight decay sets the lag. `[摘要]`
- *Surprising because*: taught as three separate puzzles from three subfields.
- *Source*: Phys. Rev. E two-phase account; *Explaining Grokking and Information
  Bottleneck*, ICLR 2026.
- *Meaning*: fixing one should move the others. A paper claiming to fix one while
  the others are unmeasured is measuring a proxy.

**A4. Fitting noise is part of the mechanism, not a bug.**
CIFAR-10 with 30% label noise: epoch-wise double descent appears - the model fits
noisy labels to 100% then **re-generalises**. Mechanism: a single very large
activation emerges in a shallow layer (the same "massive activation" seen in LLMs)
and compresses inputs into a small subnetwork, leaving the rest of the capacity to
absorb noise separately. Final state is benign overfitting. `[摘要]`
- *Surprising because*: textbook says noise fitting is overfitting.
- *Source*: arXiv:2601.08316; background NeurIPS 2023 "From Tempered to Benign
  Overfitting".
- *Meaning*: train/val gap is a nearly useless progress signal here. Early stopping
  on that gap stops before the useful phase.

**A5. Large-width networks decouple feature learning from overfitting.** `[摘要]`
Dynamical mean-field analysis of two-layer nets: for large width and large n/d, a
separation of timescales appears, giving (i) a slow timescale for complexity
growth, (ii) an inductive bias toward low complexity when initialisation is low
complexity, (iii) **dynamical decoupling between feature learning and overfitting**,
(iv) non-monotone behaviour.
- *Surprising because*: "overfitting" and "feature learning" are spoken of as one
  process with one knob.
- *Source*: Montanari & Urbani, NeurIPS 2025 / Proc. 39th NeurIPS.
- *Meaning*: two runs can differ in one process while matching on the other. Report
  both or the comparison is not identified.

---

## B. Parametrisation and optimisers

**B1. muP is not the main lever for learning-rate transfer.** `[摘要]`
Weight decay may matter more than muP for learning-rate transfer in practice.
- *Surprising because*: muP is sold as making hyperparameters transferable.
- *Source*: arXiv:2510.19093.
- *Meaning*: an LR chosen under one regularisation regime is not transferable. This
  is the same confound as A1, one level down.

**B2. Learning rates do not transfer across double descent.**
- *Source*: ICML 2026, "Learning Rates Do Not Transfer Across Double Descent".
- *Meaning*: tuning LR at one model size and reusing it at another can land you on
  the wrong side of the descent. Any "we scaled up and it got worse" claim must
  state where the LR was tuned.

**B3. Adaptive gradient methods can generalise worse than plain SGD.** `[经典]`
The marginal value of adaptive methods is largely optimisation convenience, not
generalisation; matched tuning often closes or reverses the gap.
- *Surprising because*: Adam is the default everywhere.
- *Source*: NeurIPS 2017, "The Marginal Value of Adaptive Gradient Methods".
- *Meaning*: an optimiser swap is a change to the regularisation regime, not just to
  speed. It belongs in the experiment record next to weight decay.

**B4. Warmup exists to survive an instability, not to improve the objective.** `[摘要]`
Large-batch / high-LR training sits near an "edge of stability"; warmup and sequence
length warmup are stability devices with measurable efficiency costs.
- *Source*: NeurIPS 2022 sequence-length-warmup study; edge-of-stability line of work.
- *Meaning*: removing warmup changes what the run can survive. "It trained fine
  without warmup" is a statement about this batch size, not about warmup being
  unnecessary.

---

## C. Architecture and representation

**C1. Attention sink has a structural origin, not a semantic one.** `[摘要]`
The first-token attention sink is traced to variance discrepancy, super neurons and
dimension disparity - a numerical/structural effect, not "the model stores meaning
in token 0".
- *Source*: ICML 2026, "The Structural Origin of Attention Sink"; also
  arXiv:2504.02732 "Why do LLMs attend to the first token?".
- *Meaning*: interpreting attention weights as importance is unsupported, and the
  sink makes naive attribution worse at position 0.

**C2. Linear probes and sparse autoencoders fail at compositional generalisation.**
`[摘要]`
"Stop probing, start coding": probe accuracy and SAE feature quality do not imply
the model can compose the probed feature in a new task.
- *Surprising because*: SAEs are the default interpretability instrument.
- *Source*: ICML 2026 / UAI 2026.
- *Meaning*: a feature that is "found" is not a feature that is "used". Any claim
  about mechanism needs an intervention, not a probe.

**C3. Adversarial vulnerability can come from feature interference in superposition.**
`[摘要]`
Superposition forces features to share directions; the resulting interference
produces vulnerability without any "bug" in the classifier.
- *Source*: ICML 2026.
- *Meaning*: robustness is not a separate module added later; it is constrained by
  representational capacity choices made at pretraining time.

**C4. ResNet training collapses progressively in the feedforward path.** `[摘要]`
Measurement of feedforward signal collapse during ResNet training; depth "helps" in
part by changing how signal is routed, not by adding capacity per se.
- *Source*: IEEE 2025-2026, "Progressive Feedforward Collapse of ResNet Training".
- *Meaning*: "deeper is better" is not a capacity statement. Two nets with equal
  parameters can differ because of routing, so parameter count is not a comparable
  axis.

---

## D. Data

**D1. Pretraining data ORDER changes representational geometry.** `[摘要]`
"Educated pretraining" (structured order) produces fundamentally different
representational geometry than shuffled pretraining **on identical data**.
- *Surprising because*: data order is treated as noise to average out, not as a
  design variable that changes what the model represents.
- *Source*: Zenodo 21363244; related: ScienceDirect 2026 on data ordering in
  encoder-only vs decoder-only models.
- *Meaning*: any comparison between two pretraining runs with different data order
  is a comparison of two different models, not two samples of one model.

**D2. Aggressive data filtering is not the free win it looks like.** `[摘要]`
"A Bitter Lesson for Data Filtering": heavily filtered corpora can lose the
diversity that later stages need; the benefit of filtering is regime-dependent.
- *Surprising because*: quality filtering is presented as monotonically good.
- *Source*: arXiv:2605.19407.
- *Meaning*: "we filtered for quality" is a hypothesis about the downstream task,
  not a neutral preprocessing step.

**D3. Model collapse is driven by selection bias, not only by synthetic data.** `[摘要]`
Recursive training degrades faster when sample selection bias is present; mitigations
exist but the mechanism is distributional narrowing, not "synthetic data is bad".
- *Source*: ICML 2026, "When Sample Selection Bias Precipitates Model Collapse";
  npj Artificial Intelligence 2026, ForTIFAI.
- *Meaning*: the dangerous variable is the selection operator applied at each
  generation. A pipeline that curates its own data is a recursive loop even if no
  synthetic text is generated.

**D4. Multi-epoch repetition has its own scaling law.** `[摘要]`
Repeated tokens have a scaling geometry; larger datasets can be repeated more times
before returns collapse.
- *Surprising because*: the rule of thumb is "one epoch".
- *Source*: arXiv:2610.05591; ICLR 2026 "Larger Datasets Can Be Repeated More".
- *Meaning*: "we need more data" needs its repeated-epoch counterfactual computed
  first. For small tasks this changes the cheapest way to spend budget.

---

## E. Scale and measurement

**E1. Emergent abilities can be a mirage manufactured by the metric.** `[经典]`
Sharp emergence largely disappears under continuous metrics (token-level accuracy)
versus discontinuous ones (exact match).
- *Source*: ICML 2023, "Are Emergent Abilities of Large Language Models a Mirage?".
- *Meaning*: **the metric creates the finding**. Any capability claim must name a
  continuous counterpart, or "it suddenly could" is unidentifiable.

**E2. Low perplexity hides out-of-distribution inputs.** `[摘要]`
There is a geometric blind spot: a model can report low loss on inputs that are
distributionally far from training, because perplexity measures fit, not position.
- *Source*: Zenodo 19039654, "The Geometric Blind Spot of Perplexity".
- *Meaning*: perplexity is not a coverage measure. Using it as an OOD gate is a
  category error that the repo's own "measure didn't improve" logic would inherit.

**E3. Data-constrained scaling is a different regime, not a smaller version.** `[摘要]`
Compute-optimal and data-optimal pretraining diverge; in a data-constrained world
the allocation rule changes.
- *Source*: arXiv:2605.09189; related ICLR 2026 bridging compute- and data-optimal.
- *Meaning*: scaling conclusions from an unconstrained-data paper do not transfer to
  a repeated-data setting (see D4).

---

## F. Post-training

**F1. DPO is not reliably better than PPO, despite being simpler.** `[摘要]`
A comprehensive study finds PPO can outperform DPO; the "DPO wins on simplicity"
framing conflates convenience with quality.
- *Surprising because*: DPO became the default for being one-stage and stable.
- *Source*: arXiv:2404.10719, "Is DPO Superior to PPO for LLM Alignment?"
- *Meaning*: "we switched to the simpler method" is a design decision with a
  measurable cost; it needs an arm, not an assumption.

**F2. "RL generalises, SFT memorises" is partly an artefact of the SFT data.**
`[摘要]`
Rethinking the dichotomy: what SFT memorises depends on what the SFT data contains;
with suitable data the gap narrows.
- *Surprising because*: it is quoted as a fact about optimisation.
- *Source*: NeurIPS 2026, "Rethinking 'RL Generalizes, SFT Memorizes'"; related
  NeurIPS 2026 "Memorize Theorems, Not Instances".
- *Meaning*: the load-bearing variable may be the data distribution, not the loss.
  Record the data, not just the algorithm name.

**F3. Alignment can cost capability, unevenly.** `[摘要]`
The alignment tax is real and benchmark-dependent.
- *Source*: alignment-tax literature (aggregated knowledge base entry; no single
  controlled number pinned here).
- *Meaning*: when a task regresses after a model update, check whether the arm
  changed model behaviour, not just code. RDS records tokens but **not model
  identity** - that is a gap.

---

## G. Reasoning and test-time compute

**G1. Chain-of-thought is an imperfect oversight channel.** `[摘要]`
Step-level evidence that reasoning traces become performative: the trace does not
reliably reflect the computation that produced the answer.
- *Surprising because*: CoT is used as evidence of process.
- *Source*: ICML 2026 / arXiv:2605.11746, "When Reasoning Traces Become Performative".
- *Meaning*: a CoT trace is not a receipt. Reading it as one is the same error as
  reading a status field as truth - which RDS already forbids for its own ledger.

**G2. Thinking more does not always help.** `[摘要]`
"The mirage of test-time scaling": additional reasoning tokens can degrade accuracy
on some tasks rather than improve it.
- *Surprising because*: test-time scaling is treated as monotone.
- *Source*: NeurIPS 2025.
- *Meaning*: reasoning budget is a hyperparameter with a non-monotone response. An
  experiment that only adds thinking budget without a stop rule can manufacture a
  regression.

**G3. In-context learning behaves like an optimisation procedure.** `[摘要]`
Mechanistic verification that ICL implements something gradient-descent-like;
behavioural evidence (inverse frequency effect) points the same way.
- *Source*: Zenodo 2026, mechanically verified; structural-priming evidence.
- *Meaning*: few-shot examples are training data with all the usual hazards
  (leakage, ordering, distribution shift). "We gave it examples and it worked" is
  not a mechanism.

---

## H. Robustness

**H1. Adversarial examples are not real features.** `[摘要]`
The "adversarial examples come from non-robust but predictive features" account is
challenged: under that account the examples should transfer like features do, and
they do not.
- *Source*: NeurIPS 2023, "Adversarial Examples Are Not Real Features".
- *Meaning*: robustness claims that rest on a feature story need transfer evidence,
  not a plausible narrative.

**H2. OOD detection benchmarks can be misleading.** `[摘要]`
Benchmark pitfalls in real-time detection: reported gains can vanish under a change
of protocol or a new mitigation paradigm.
- *Source*: IEEE 2026, "Revisiting OOD Detection in Real-Time Object Detection".
- *Meaning*: an OOD number without its protocol is not a result. This is the same
  lesson as E1, in a different subfield.

---

## I. Training tricks that stopped working

**I1. Dropout should be dropped for single-epoch LM pretraining.** `[摘要]`
Dropout does not help (and can hurt) single-epoch language-model pretraining.
- *Surprising because*: dropout is survival-of-the-default from the small-data era.
- *Source*: ACL Findings 2025, "Drop Dropout on Single Epoch Language Model
  Pretraining".
- *Meaning*: regularisers are data-regime-specific. Importing a trick from the
  small-data regime into the large-data regime is a hypothesis, not a default.

---

## What this changes for RDS

Six consequences, ordered by how much they should change the design.

1. **Budget exhaustion is not evidence of a dead direction.** Generalisation is a
   late phase (A1-A4). "Budget spent, no signal" and "budget spent, refuted" are
   different states, and the second needs a longer-horizon arm, not a rerun.
2. **Train loss and val loss are both poor progress measures** (A4). Add at least one
   representation- or compression-level quantity, or every "it learned X" claim is a
   proxy.
3. **Metric choice creates findings** (E1, H2). Every rubric must declare its
   continuous counterpart, exactly as the emergence literature showed discontinuous
   metrics manufacture cliffs.
4. **Regularisation regime and data order belong in the experiment record** (A1, D1,
   B1-B4). A record that stores the architecture but not the weight decay, schedule
   and data order cannot support any later claim about which one caused the change.
5. **Traces are not receipts** (G1). A chain of thought and an agent transcript are
   both generated text. RDS already refuses to treat a status field as truth; the
   same rule must apply to reasoning traces.
6. **Model identity is missing from the current record** (F3). Token counts are
   recorded without the model that spent them, so an alignment change and a logic
   change are indistinguishable after the fact.

---

## UNKNOWN / not verified in this pass

- Whether the two-phase account survives at LLM scale or is specific to toy tasks.
  The unification papers work on small settings; extrapolation is unmeasured.
- Specific alignment-tax magnitudes; only the direction is asserted here.
- The ICL-as-gradient-descent claim is mechanistically verified on small transformer
  settings; the extent at frontier scale is UNKNOWN.
- Several entries are single papers with `[摘要]`-level reading. Treat them as
  presets to test against, not as settled results.
- None of this was re-run locally. These are literature presets, not measurements
  in this repository.
