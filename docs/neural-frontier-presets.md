# Neural frontier presets: what breaks the textbook intuitions

Written 2026-10-24 for agents working on RDS. Purpose: replace stale priors about
how deep networks learn, so experiment design is not shaped by the 2018 picture.

Every entry gives the claim, why it violates the usual intuition, the source, and
what it means for how we design and judge DL experiments here. Entries marked
**[摘要]** were taken from the paper's abstract or HTML body; I did not re-run them.

---

## 0. The one idea that reorganises the rest

**Learning has two timescales, and standard training only optimises the first.**

Fitting the training set is fast. Compressing the solution - discarding input
information that does not predict the label - is slow, and gradient descent does
not actively accelerate it. Generalisation arrives when the second phase
completes, which is why test performance can jump long after training loss is
already zero.

> "We argue that the second phase is not actively optimized by standard training
> algorithms and may be unnecessarily prolonged." - Phys. Rev. E, on grokking,
> double descent and the information bottleneck sharing one temporal structure.

If you take one thing from this file: **a metric that improved inside the budget
is not the same as a direction that works, and a metric that did not improve
inside the budget is not the same as a dead direction.** Both errors are
systematic, not random.

---

## 1. Grokking is not an architecture property; it is a training-condition property

**Claim.** On modular addition, depth has a *non-monotonic* effect: depth-4 MLPs
consistently fail to grok while depth-8 residual networks recover generalisation.
Under matched hyperparameters the Transformer-vs-MLP gap collapses from a reported
2.18x to **1.11x** (45,600 vs 50,800 steps, 10/10 seeds grokked). Weight decay is
the dominant control, with a narrow Goldilocks band - too little or too much both
prevent grokking. **[摘要]**

**Why it is surprising.** The folk story is "Transformers grok, MLPs don't" and
"deep is better". The measured gap is mostly an artefact of mismatched optimiser
and regularisation settings, and adding depth *without* architectural
stabilisation makes it worse, not better.

**Source.** *A Systematic Empirical Study of Grokking: Depth, Architecture,
Activation, and Regularization*, arXiv:2603.25009 (2026-03). Related:
*To Grok Grokking: Provable Grokking in Ridge Regression*, arXiv:2601.19791
(2026-07), which proves the three stages end-to-end for over-parameterised ridge
regression and concludes grokking "is not an inherent failure mode of deep
learning... does not require fundamental changes to the model architecture or
learning algorithm to avoid". **[摘要]**

**Design meaning.** When a run fails to generalise, the first hypothesis should be
"the regularisation and schedule are in the wrong regime", not "the architecture
is wrong". Any experiment that varies architecture while holding weight decay at
one value is confounded and should be labelled as such.

---

## 2. Fitting noise is part of the mechanism, not a bug

**Claim.** With 30% label noise on CIFAR-10, an epoch-wise double descent appears:
the model perfectly fits the noisy training data, then **re-generalises**. The
mechanism found: a single very large activation emerges in a shallow layer - the
same "massive activation" seen in modern LLMs - and compresses input patterns into
a small subnetwork, letting the remaining capacity absorb noise separately. The
final state is benign overfitting. **[摘要]**

**Why it is surprising.** Textbook says fitting noise is overfitting and
generalisation must be paid for by capacity control. Here the model reaches
100% train accuracy *including* noise and then generalises best.

**Source.** *Deep Exploration of Epoch-wise Double Descent in Noisy Data: Signal
Separation, Large Activation, and Benign Overfitting*, arXiv:2601.08316.
Background: *From Tempered to Benign Overfitting*, NeurIPS 2023.

**Design meaning.** Train loss is nearly useless as a progress signal in this
regime. A design that early-stops on train/val gap will stop exactly before the
useful phase. Also: "massive activations in LLMs" and "benign overfitting in small
MLPs" are the same phenomenon, so a trick found in one transfers more often than
the literature division suggests.

---

## 3. Grokking, double descent and information bottleneck are one phenomenon

**Claim.** All three are the late onset of generalisation after training error
reaches zero, and their timescales align across two different settings. Mutual
information between hidden layers and input is proposed as the natural progress
measure. A neural-collapse account makes it quantitative: the contraction of
population within-class variance underlies both grokking and IB, and the lag
behind training-fit time is set by weight decay. **[摘要]**

**Why it is surprising.** They are taught as three separate puzzles from three
subfields. If they are one, then a paper that fixes one should move the others, and
a paper that claims to fix one while the others are unmeasured is probably
measuring a proxy.

**Source.** Phys. Rev. E, "two phases" account; *Explaining Grokking and
Information Bottleneck* (ICLR 2026), neural-collapse unification.

**Design meaning.** Pick the progress measure deliberately. If we track only
accuracy, we cannot distinguish "learning features" from "compressing an existing
solution". A run should log at least one representation-level quantity, or the
claim "it learned X" is unsupported.

---

## 4. Emergent abilities can be an artefact of the metric

**Claim.** Sharp "emergence" at scale largely disappears when the metric is
continuous (token-level accuracy) instead of discontinuous (exact match). The
apparent jump is often a property of the measurement, not of the model.
**[摘要, 2023 result, still under-cited in practice]**

**Why it is surprising.** "Emergence" is used as an argument that scale creates
qualitatively new capability. Under a different metric the curve is smooth.

**Source.** *Are Emergent Abilities of Large Language Models a Mirage?* ICML 2023.

**Design meaning.** This is the same disease as choosing an easy rubric: the metric
creates the finding. For RDS, any judgement function must declare which metric it
uses and what the continuous counterpart looks like, or the claim "capability X
appeared" is not identifiable.

---

## 5. Multi-epoch repetition has its own scaling law

**Claim.** Repeated tokens are not simply wasted. There is a scaling geometry for
multi-epoch pretraining, and larger datasets can be repeated more times before
returns collapse. **[摘要]**

**Why it is surprising.** The practical rule of thumb is "one epoch, else it is
overfitting". The measured behaviour is a scaling surface, not a cliff.

**Source.** *What Is a Repeated Token Worth? The Scaling Geometry of Multi-Epoch
Pretraining*, arXiv:2610.05591; *Larger Datasets Can Be Repeated More*, ICLR 2026.

**Design meaning.** "We need more data" is a claim that needs its repeated-epoch
counterfactual computed first. For small-task DL experiments this changes the
cheapest way to spend budget.

---

## 6. In-context learning behaves like an optimisation procedure

**Claim.** Mechanistic work reports ICL implementing something gradient-descent-like,
verified mechanically; behavioural evidence (inverse frequency effect in structural
priming) points the same way. **[摘要]**

**Why it is surprising.** It is tempting to read ICL as retrieval or as "the model
already knew it". If it is optimisation, then the prompt is a training set, and
prompt order, redundancy and difficulty all change the *solution*, not just the
recall.

**Source.** *When In-Context Learning Implements Gradient Descent: a learned
mechanism, mechanically verified* (Zenodo 2026); inverse-frequency-effect evidence.

**Design meaning.** For agent workflows: few-shot examples are training data with
all the usual hazards (leakage, ordering, distribution shift). Reporting "we gave
it examples and it worked" is not a mechanism.

---

## 7. Alignment can cost capability

**Claim.** Post-training alignment (RLHF-style) can reduce capability on some
tasks - the alignment tax - and the loss is not uniform across benchmarks.
**[摘要]**

**Why it is surprising.** The naive reading of the RLHF pipeline is "smarter and
safer". The measured behaviour is a trade, with the direction depending on what is
measured.

**Design meaning.** When a task shows a regression after a model update, check
whether the arm changed model behaviour (alignment) before assuming the change
broke the logic. Model identity is part of the experiment record; RDS currently
records tokens but not model identity, which is a gap.

---

## 8. Retrieval augmentation does not fix stale knowledge

**Claim.** Search-augmented LLMs fail on reasoning over rapidly evolving knowledge
in specific, characterisable ways - the failure is not uniform across query types.
**[摘要]**

**Why it is surprising.** RAG is treated as "the fix" for knowledge cutoff. The
measured failure modes persist *with* retrieval.

**Design meaning.** "We added retrieval" is not evidence that the answer is current.
Any experiment claiming a knowledge-dependent win must state the knowledge cutoff
of the base model and the retrieval corpus date separately.

---

## 9. What this changes for RDS

Four consequences, in order of how much they should change the design:

1. **Budget exhaustion is not evidence of a dead direction.** Because
   generalisation is a late phase, a run that improves nothing inside the budget may
   be mid-incubation. RDS must distinguish "budget spent, no signal" from "budget
   spent, refuted", and the second requires a *longer-horizon* arm, not a rerun.
2. **Train loss and val loss are both poor progress measures.** Add at least one
   representation-level or compression-level quantity, or all "it learned X" claims
   are proxies.
3. **Metric choice creates findings.** Every rubric or judgement function must
   declare its continuous counterpart, in the same way the emergence literature
   showed discontinuous metrics manufacture jumps.
4. **Weight decay / schedule belongs in the experiment record.** The dominant
   control parameter for grokking is not the architecture, so a record that stores
   the architecture but not the regularisation regime cannot support a later claim
   about which one caused the change.

---

## 10. UNKNOWN (not verified in this pass)

- Whether the "two phases" account survives at LLM scale, or is specific to toy
  tasks. The unification papers work on small settings; extrapolation is unmeasured.
- The alignment-tax numbers vary by benchmark and model pair; I did not pin a
  specific quantitative claim.
- The ICL-as-gradient-descent claim has mechanistic verification on small
  transformer settings; the extent at frontier scale is UNKNOWN.
- No claim here was re-run locally. These are literature presets, not measurements
  in this repository.
