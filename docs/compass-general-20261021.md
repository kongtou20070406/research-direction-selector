# A general Compass: what is already domain-free and what is not

Status: design note, written 2026-10-21 before any General-Compass code lands.
Scope: how the same Compass loop can carry mathematics, physics and
experimental-science (deep learning) campaigns without a second engine.

## 1. The question

Compass today lands its results on exact scalar law recovery. Every hypothesis
in a campaign is an expression in the rational dialect, `synthesize` builds
those expressions by enumeration, and a measurement is an exact rational.
That shape fits mathematics and physics. It does not fit a deep-learning
experiment, where a hypothesis is "this configuration beats that one" and a
measurement is a mean over seeds with a variance.

The tempting reading is that Compass needs a second engine for the
experimental sciences. This note argues that it does not, and locates the one
layer that is genuinely domain-bound.

## 2. What is already domain-free

Read bottom-up through `scripts/rds_compass_model.py`.

**`decide(predicted, observed, tol)` — `scripts/rds_compass_model.py:484`.**
The whole admissibility rule is:

```python
radius = getattr(predicted, 'radius', 0) + getattr(observed, 'radius', 0)
gap, spread = abs(predicted - observed), relative * getattr(observed, 'radius', 0)
if gap + radius <= absolute + relative * abs(observed) - spread:
    return True
if gap - radius > absolute + relative * abs(observed) + spread:
    return False
return None
```

This function never asks what the numbers *mean*. It asks two values, each
carrying a half-width, whether the bands provably overlap, provably separate,
or neither. `True` is "consistent", `False` is "counterexample", `None` is
"the evidence cannot decide". Those three verdicts are as valid for a noisy
accuracy measurement as for an exact rational.

**`Inexact` — `scripts/rds_compass_model.py:58`.** A `Fraction` subclass whose
`radius` is the half-width of a rigorous enclosure. It exists so irrational
evaluation stays honest. It is, without modification, the right carrier for a
statistical measurement: a mean over *n* runs with standard error *s* is an
interval of half-width *s·t*.

**`replay(hypothesis, observations, tolerances)` — `scripts/rds_compass_model.py:559`.**
It reads exactly three things per cell: `row['inputs']`, `row['outputs'][name]`,
and `tolerances[name]`. `inputs` is already an arbitrary dict, so
`{'lr': 0.01, 'dropout': 0.3, 'seed': 1}` needs no change. `outputs` is a
number, and per the previous paragraph a number carrying a half-width is still
a number.

**`scientific_support` is always `UNKNOWN`.** Compass never consults a ground
truth to adjudicate. `truth.law` exists only in benchmark harnesses, to score
`claim_exact` afterwards. The kernel decides what the evidence supports, not
what the answer is. That is precisely the property an experimental science
campaign needs.

So the adjudication core is already domain-free. Three vectors of change carry
every campaign type through it.

## 3. What is domain-bound

**The hypothesis source.** `synthesize` enumerates expressions over a declared
grammar. In deep learning there is no expression to enumerate: the hypothesis
space is "which knob to turn next", and its prior comes from a model or a
human, not from a size-ordered enumeration. This is the single genuinely
domain-bound layer. `scripts/rds_compass.py` reaches `synthesize` from six
sites (`:708`, `:715`, `:732`, `:747`, `_hypothesis_from_fit` at `:905`, and
`scripts/rds_compass_rsi.py:377`); every one of them assumes the returned
object is an expression AST.

**Two smaller asymmetries.** `measured()` (`scripts/rds_compass.py:135`) calls
`model.exact(v)`, which accepts `int`, `float`, `Fraction` and rational
*strings* (`scripts/rds_compass_model.py:85`) and has no route for an interval.
And `plan['candidates']` is a finite pre-declared list that the declaration
freezes at ingest time (`scripts/rds_compass_declaration.py`).

## 4. Target architecture

Three layers, only the middle one changes shape.

| Layer | Contract | Today | General |
| --- | --- | --- | --- |
| Adjudication | `decide(predicted, observed, tol)` -> `True` / `False` / `None` | exact rationals | unchanged |
| Evidence | `measured(state) -> [{id, inputs, outputs}]` where each output may carry `radius` | point values | interval-or-point |
| Hypotheses | `source(state) -> [hypothesis]`, each with `predict: {observable: rule-or-value}` | enumeration only | pluggable |

The general Compass is the claim that a hypothesis is *anything that produces a
per-observable prediction*, and a source is *anything that produces
hypotheses*. Enumeration is one source. Inverse decomposition is another
(`scripts/rds_compass_decompose.py`, already present and measured at 190x on
its own). An agent emitting a JSON hypothesis is a third. An RSI-qualified
tool is a fourth, already wired.

## 5. How a deep-learning campaign maps onto it

- **Candidate** = one training configuration. `inputs` carries the knobs, the
  seed included as an input so repeats are distinct cells rather than
  duplicated ones.
- **Observable** = a scalar the campaign cares about: validation accuracy,
  generalisation gap, wall-clock to a loss threshold.
- **Measurement** = mean over seeds, carried as an interval of half-width
  `s·t / sqrt(n)`. The kernel then refuses to call a difference real unless the
  bands separate. `UNDECIDED` is the honest verdict on a single-seed run and
  is already a first-class state.
- **Tolerance** = the smallest difference the campaign is willing to treat as
  meaningful. This is the one number a user must state, and it must be stated
  before the run. Compass's existing contract already refuses to invent it:
  an observable with no declared tolerance stays `UNKNOWN` and refutes
  nothing (`scripts/rds_compass_model.py:576`).

The friction: a deep-learning hypothesis is usually *comparative* ("dropout
0.3 beats 0.5") while a Compass hypothesis is *predictive* ("this cell's
accuracy is 0.823"). The reduction is to define the candidate as a pair of
configurations and the observable as the delta. That works, and it costs cells:
comparing k arms pairwise needs O(k^2) candidates instead of O(k). For small
arms this is the right trade, since it makes multiplicity explicit in the
candidate list rather than hidden in a post-hoc analysis.

## 6. Staged implementation

Each stage is independently verifiable and independently revertable.

**Stage 1 — interval evidence.** Teach `model.exact` to accept
`{'lo': ..., 'hi': ...}` and `measured()` to pass such a dict through as
`Inexact(lo, hi)`. No decision logic changes. Reject pre-registered: a
campaign that supplies point values must produce byte-identical replay output
to today's.

**Stage 2 — explicit hypothesis submission.** Add a campaign-declared
`hypotheses` block that compiles JSON hypotheses through the existing
`compile_hypothesis` (`scripts/rds_compass_model.py:517`) without calling
`synthesize`, carrying declared provenance so the ledger records who proposed
it. Reject pre-registered: a campaign with no such block behaves identically,
and a campaign with one never invokes `synthesize`.

**Stage 3 — source plug-in.** Factor the six `synthesize` call sites behind one
`hypothesis_source` seam, so a domain can register enumeration,
decomposition, or agent proposals without touching `_fits`.

Stage 3 is a refactor of a hot file and should not start until the derived-cap
question (see `docs/diag-enum-budget.md`) is settled, because both touch
`_fits`.

## 7. What would make this fail

Three failure modes, each with a pre-registered detector.

1. **Tolerance becomes a rubber stamp.** If a campaign declares a tolerance
   wider than the effect it hopes to see, every hypothesis comes back
   `CONSISTENT` and the tool reports success. Detector: the false-certification
   harness must run on any general-Compass campaign, with the tolerance
   recorded in the result file rather than inferred at judging time.
2. **`CONSISTENT` is read as "validated".** It is not, and the kernel already
   says so: `CONSISTENT` means no counterexample was found among the cells that
   were actually tested, and `scientific_support` stays `UNKNOWN`. A
   general-Compass report must surface `tested`, `undecided` and
   `not_applicable` counts next to the verdict, or readers will over-read it.
3. **Statistical evidence laundered into exact language.** If an interval
   measurement is later replayed as if it were a point, the radius silently
   vanishes and an `UNDECIDED` turns into a hard verdict. Detector: a
   regression test that replays an interval measurement through every path
   that touches `exact()` and asserts the radius survives.

## 8. Non-goals

- Not a second engine, a second ledger, or a parallel state machine.
- Not dynamic candidate generation: declarations stay frozen, campaigns
  pre-declare their grid.
- Not a promise of research gain. This note is about widening the domain where
  the loop *can* run, with the same evidence discipline. Whether it produces
  real findings is measured separately and has not been shown yet.
