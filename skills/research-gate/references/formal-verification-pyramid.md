# Four-Layer Formal Verification Pyramid

Use the cheapest formal tool that can kill a broken hypothesis *before* burning theorem-proving hours or GPU budget.

| Layer | Tooling | Runtime | What It Catches Best | When Required |
|---|---|---|---|---|
| **1. Property-Based Falsification** | `pytest` + `Hypothesis` | `< 3s` | Tensor shape edge cases, unit-norm degeneracy, zero-residual bias bugs, causal blindness leaks | Every new model module or operator (`@settings(database=None)` to avoid stray cache dirs) |
| **2. Symbolic & SMT Verification** | `SymPy` + `Z3` | `< 2s` | Invalid algebraic bounds, broken sublevel separation, Lyapunov step violations, CEGIS counterexample search | Every new loss formulation, stability law, or theoretical inequality before Lean formalization |
| **3. Machine-Checked Proofs** | `Lean 4` (`-DwarningAsError=true`) | Seconds–Minutes | Unstated quantifiers, hidden finiteness/non-degeneracy assumptions, multi-theorem consistency | Core theoretical claims intended for publication |
| **4. Concurrent State Machines** | `TLA+` / `TLC` (optional, JDK 17) | Seconds–Minutes | Deadlocks, race conditions, resume/checkpoint inconsistency in multi-agent loops | Only when multi-process or distributed training protocols grow non-trivial |

## Pattern A: Hypothesis Property Tests for PyTorch (Zero Side Effects)

```python
from hypothesis import given, settings, strategies as st
import torch

@settings(database=None, max_examples=40, deadline=None)
@given(
    vals=st.lists(st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False), min_size=12, max_size=12)
)
def test_blindness_and_norm_invariant(vals):
    x = torch.tensor(vals, dtype=torch.float32).view(2, 6)
    ...
```

Always enforce `database=None` plus `HYPOTHESIS_STORAGE_DIRECTORY` (pointed to a temp directory or `set_hypothesis_home_dir(...)` at module import top before pytest collection hooks collect local constants into `.hypothesis/`) and pure IEEE FP32 when asserting exact `0.0` determinism or blindness.

## Pattern B: Z3 SMT Counterexample Search (Prove by Unsatisfiability)

Prove universally quantified dynamical bounds across real numbers by asserting the **negation** of the target bound and requiring `z3.unsat`:

```python
import z3

def test_basin_steady_state_ball():
    gamma, eps, e_t, e_next = z3.Reals("gamma eps e_t e_next")
    s = z3.Solver()
    s.add(gamma > 0, gamma < 1, eps >= 0)
    s.add(e_t >= 0, e_t <= eps / (1 - gamma))
    s.add(e_next <= gamma * e_t + eps)
    # Negate conclusion: can one contraction step escape the steady-state ball?
    s.add(e_next > eps / (1 - gamma))
    assert s.check() == z3.unsat
```

If `s.check() == z3.sat`, `s.model()` immediately returns an explicit mathematical counterexample in milliseconds, saving hours of failed Lean proof attempts.
