# Diagnosis before prescription: why "overfitting means add regularisation" is wrong

Written 2026-10-24. Applies to any DL experiment run through RDS or by hand.

The reflex is: validation loss is bad, therefore overfitting, therefore add weight
decay / dropout / early stopping. Three things are wrong with it.

1. "Bad validation loss" is not a diagnosis. It is a symptom with at least five
   causes, and overfitting is the **least** likely one in the modern regime.
2. "Overfitting" has a testable definition, and most of the time the test fails.
3. Regularisation has costs, and applying it under a wrong diagnosis makes the
   run worse in a way that looks like "the idea did not work".

---

## 1. What overfitting actually claims

Overfitting is a claim about two quantities diverging over training:

> empirical risk keeps falling while expected risk stops falling and rises, **and**
> the gap widens monotonically beyond what sampling noise explains.

That sentence has four checkable clauses. If any of them fails, the diagnosis is
not supported:

| Clause | How to check | If it fails |
|---|---|---|
| train loss is still falling | it must fall for the window in question, not be flat | the run is not optimising; this is an optimisation problem |
| val loss is falling then rising | needs a turning point, not one noisy epoch | a single uptick is noise; compute the interval |
| the gap is monotone | plot gap vs step, not loss vs step | a shrinking gap is the opposite of overfitting |
| the rise exceeds sampling noise | need enough val examples; bootstrap the val loss | with a small val set the "rise" is usually variance |

A fifth, subtler clause: **the run must not be inside a grokking plateau.** During
that plateau the gap is large and stable for tens of thousands of steps, then
collapses. Early stopping on the gap kills exactly the run that was about to
generalise (see `neural-frontier-presets.md` A1-A4).

---

## 2. The five causes of "val loss is bad", in rough order of prior probability

**a. The split is wrong, not the model.** Train and val differ in preprocessing,
   sampling, label noise rate, or time. This is the most common cause and it is
   checked in minutes. If val loss is bad from step zero and never improves, suspect
   this before anything else.

**b. Generalisation is late and the budget is short.** This is the grokking /
   double-descent / compression story. The run is mid-incubation. Raising weight
   decay here pushes it *away* from generalisation, because weight decay controls the
   timescale and has a Goldilocks band - too much and it never groks.

**c. Routing or optimisation pathology.** Feedforward collapse, dead layers, LR on
   the wrong side of a descent, warmup removed at a batch size that needed it. Train
   loss still falls, so it looks like a capacity story; it is not.

**d. Measurement noise.** Small val set, class imbalance, a metric with high variance
   across seeds. One seed is an anecdote. Three seeds with overlapping intervals
   cannot support a "the gap" claim.

**e. Real overfitting.** Data is small relative to parameters, or there are many
   epochs over the same data, or it is fine-tuning on a narrow distribution. Only
   here does "add regularisation" become the first move.

Note the ordering. In the modern regime (single epoch, data far larger than
parameters), (e) is rare and (a)-(c) dominate. The reflex assumes (e) every time.

---

## 3. The prior depends on the task shape

This is the "按任务来" part. The same symptom has a different default cause depending
on what kind of run it is.

| Run shape | Prior for real overfitting | First check | First fix |
|---|---|---|---|
| Single epoch, data >> parameters | **low** | split hygiene; is the val set drawn from the same pool? | fix the split |
| Single epoch, parameters >> data | **low-medium** | is this benign overfitting (fitting noise then re-generalising)? | lengthen the run; do not early-stop |
| Multi-epoch over a small corpus | **high** | how many epochs; does the curve turn? | then regularise |
| Fine-tuning a pretrained model | **high** | is it memorising instances (SFT memorisation) vs learning the task? | data curation before weight decay |
| Toy task, tiny dataset (grokking regime) | **low, and misleading** | is weight decay in the Goldilocks band? | tune weight decay, usually *up* or *down*, not "add regularisation" as a category |
| Anything with 1 seed | **unknown** | run 2-3 seeds before any claim | seed variance first |

The last row is the one that matters most in practice: **most "overfitting" reports
are one run compared against itself.**

---

## 3b. Even a confirmed overfit may be the middle of double descent

Here is the second, deeper error. Suppose the diagnosis passes: train loss is still
falling, val loss has turned, the gap is widening, and it exceeds sampling noise.
The reflex says "stop, shrink, regularise". **That reflex can be exactly wrong**,
because a widening gap is also the signature of the *first hump* of double descent.

Three forms, all documented:

| Form | What varies | Test error goes |
|---|---|---|
| model-wise | parameters while data is fixed | up, then down again past the interpolation threshold |
| epoch-wise | training time past the point of fitting the data | up, then down again |
| sample-wise | number of samples | up, then down again |

The common structure: **near the interpolation threshold, test error rises before it
falls.** The "overfit" state is not a terminal state; it is a transient on the way
through.

This is the same machinery as A3 in the presets: grokking, double descent and the
information bottleneck are one phenomenon with two timescales. The rise is phase one
finishing; the fall is phase two.

### How to tell a transient from a terminal overfit

| Question | Transient (keep going) | Terminal (intervene) |
|---|---|---|
| Is the train set fully fit, including noise? | usually yes, or nearly | may be, but the gap keeps widening |
| Are parameters/data near the interpolation threshold? | yes, close to 1 | far above it, or far below |
| Does the gap widen but the run is still early in phase one? | yes | gap widens monotonically with no inflection |
| What happens with 20-30% more budget? | error turns back down | error keeps climbing |
| What happens with a larger model at fixed data? | error turns back down | error stays flat or worse |

The decisive test is the last two rows. **You cannot diagnose transient overfitting
from a single fixed-budget run.** You need the "keep going" arm, which is exactly the
arm that early stopping removes.

### Why the reflex makes it worse

- **Early stopping** lands you on the top of the first hump. It converts a slow
  success into a clean-looking failure, and the failure is recorded as evidence
  against the idea.
- **Adding weight decay** moves the interpolation threshold. It does not simply
  "prevent overfitting"; it relocates where the hump sits, and can put it inside the
  budget where it was previously outside.
- **Shrinking the model** may move you from the right of the threshold to the peak.
  Double descent makes "smaller" a non-monotone intervention.
- **Adding data** is the safest of the four, because sample-wise descent says the
  curve eventually turns - but it costs budget and needs the same "keep going" arm to
  confirm.

### The honest boundary

Double descent is not universal. Some task/architecture combinations do not show it,
and at LLM scale the empirical picture is UNKNOWN. So the claim is not "overfitting
is never real". The claim is narrower and still decisive:

> If the widening gap was measured inside a fixed budget, you have not distinguished
> transient from terminal overfitting. Any intervention chosen before that
> distinction is a guess.

Which means the correct first action is usually **lengthen the run**, not regularise.
That is also the cheapest arm to add, because it is the same code.

---

## 4. What regularisation costs when the diagnosis is wrong

- **Weight decay** controls the timescale of the compression phase. Too little: no
  grok. Too much: no grok. It is not "more regularisation is safer".
- **Dropout** does not help single-epoch language-model pretraining and can hurt it.
  It is a small-data-era regulariser.
- **Early stopping** on the train/val gap stops before the late phase. This is the
  single most expensive wrong reflex, because it converts a slow success into a
  clean-looking failure.
- **Smaller models** trade capacity for a proxy objective. If the cause was (a) or
  (b), you now have two problems.
- **Data augmentation** changes the distribution you are fitting. Under cause (a),
  it makes the mismatch worse.

Also: applying regularisation changes the run so much that a later "this idea did
not work" conclusion is not about the idea. The experiment has been confounded by
the fix.

---

## 5. The order to actually run checks

1. **Reproduce with 3 seeds.** If the effect disappears, stop; there is nothing to
   diagnose.
2. **Inspect the split.** Same preprocessing, same source pool, same time window.
   Look at the loss curves from step 0.
3. **Check whether train loss is still falling** in the window of interest. If not,
   it is an optimisation problem.
4. **Check the gap shape**, not the loss shape. Monotone widening or not.
5. **Check the budget length** against known late-phase behaviour for the task.
6. **Only then** consider regularisation, and prefer tuning the existing weight
   decay over adding a new regulariser.

Each step has an output that either kills the overfitting hypothesis or keeps it
alive. That is the point: diagnosis is a sequence of attempts to kill the
hypothesis, not a lookup from symptom to prescription.

---

## 6. How this changes RDS

The RDS judgement layer currently has one place where a null or negative result gets
interpreted: the run either improved a metric inside the budget or it did not. That
is the reflex at system level, and it is wrong for the same reason.

Three concrete changes:

1. **Distinguish three terminal states, not two.** `NO_SIGNAL_WITHIN_BUDGET`,
   `REFUTED_WITH_EVIDENCE`, and `BLOCKED_BY_CONDITION`. The first is compatible with
   causes (b) and (d) above; collapsing it into "did not work" is the same error as
   calling every bad val loss overfitting.
2. **Record the diagnosis, not only the outcome.** A run record should carry which
   cause was ruled out and how (three seeds? split inspection? curve shape?). Without
   that, a later reader cannot tell a refutation from a short budget.
3. **Refuse the prescription without the test.** A rubric that scores "did the agent
   apply regularisation correctly" would teach exactly the reflex this document is
   about. Score the diagnosis quality instead: did the agent name a cause, and what
   would have falsified it?

---

## 7. UNKNOWN

- The Goldilocks band width for weight decay at LLM scale is not measured here; the
  claim is from small-scale grokking studies.
- The relative frequency of causes (a)-(e) in real projects is an impression from
  this repository's own history, not a measured distribution. It should be measured
  before being quoted as a number.
