# Exact network analyses (`rds_nn_analyze`)

`scripts/rds_nn_analyze.py` answers structural questions about a dense/ReLU network exactly. It uses the restricted model format of `rds_nn_verify` (`{"input_dim", "layers": [linear | relu]}`, rational weights, at most 64 units per layer and 16384 parameters). A PyTorch `Sequential` of `Linear`/`ReLU` layers can be converted with `rds_model_export.export_sequential`.

Every number describes that exported rational model over the reals. It does not describe IEEE floating-point execution, training or generalization, and `scientific_support` is always `UNKNOWN`.

| Action | Request fields | Result | Label |
|---|---|---|---|
| `bounds` | `box` | Per ReLU layer, units proved `dead` (pre-activation `<= 0`) or `active` (`>= 0`) for every input in the box; the rest are `unproved`, which does not mean unstable. | `PROVED_OVER_BOX` |
| `lipschitz` | `box` (optional) | An upper bound on the l∞→l∞ Lipschitz constant: the largest row sum of `abs(W_k) M ... M abs(W_1)`, where `M` zeroes units proved dead over the box. The plain product of layer norms is reported for contrast. | `UPPER_BOUND` |
| `regions` | `start`, `end` | The exact number of linear pieces of the network along the segment, the breakpoints `t` in `[0, 1]`, the activation patterns and the end outputs. | `EXACT` |
| `jacobian` | `point` | The exact Jacobian and its l∞ norm. A pre-activation exactly at zero is listed under `ties` with status `NON_DIFFERENTIABLE`; the reported matrix then uses `ReLU'(0) = 0`. | `EXACT` |
| `ablation` | `points`, `units` (`[[layer, unit], ...]`, ReLU layers only) | Exact output deltas when the listed units are forced to zero, with per-output maximum and mean absolute change. | `EXACT` |

```bash
python -B scripts/rds_nn_analyze.py regions --model model.json --request request.json --compass-out out.json
```

A malformed model or request exits 2 with `status: INVALID` on stderr. Nothing is guessed or repaired.

## Use in a Compass campaign

`--compass-out` writes the scalar summary as `{"outputs": {...}}`, the measurement format a Compass experiment emits. The summary holds:
- `dead`, `active`, `unproved` for `bounds`;
- `lipschitz_upper`, `layer_norm_product` for `lipschitz`;
- `linear_regions` for `regions`;
- `jacobian_linf`, `relu_ties` for `jacobian`;
- `max_abs_delta`, `max_mean_abs_delta` for `ablation`.

An experiment that builds one network per declared input can therefore let Compass identify a structural law, with each value receipted as `INPUT_REPORTED`. For example, linear regions against width, or the Lipschitz bound against depth. The Compass dialect now includes `log`, `exp` and rational powers, so such laws include scaling forms like `a * x ** (1/2)` or `log(x)`.

An experiment that trains a network per level of an input and reports train and test metrics can declare a Compass `trend`, which turns the receipted runs into overfitting and plateau conclusions (see [Fit trends](compass.md#fit-trends-train-and-test)).

These tools complement the certificates in [formal-verification.md](formal-verification.md). `rds_nn_verify` proves output bounds and margins, and `rds_scale_verify` proves scale equivariance. The analyses here measure structure: unit stability, sensitivity, region counts and ablation effects.
