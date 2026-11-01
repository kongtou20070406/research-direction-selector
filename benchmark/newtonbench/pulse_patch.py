"""Single-point pulse candidate generator for sparse exception synthesis."""
from fractions import Fraction
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

import rds_compass_model as model


def pulse_patch(rows, leader, observable='y', *, tolerances=None, variable=None):
    """Generate and rank single-point pulse hypotheses: ite((x == v), observed_v, leader).

    Args:
        rows: Observation rows with 'inputs' and 'outputs'.
        leader: Infix string or AST dict for the baseline/leader expression.
        observable: Target observable name (e.g. 'y').
        tolerances: Map of observable -> (abs, rel) tolerance tuple.
        variable: Specific input variable; inferred if single-input.

    Returns:
        List of candidate dicts sorted by counterexamples_count ascending.
    """
    if not rows:
        return []
    if variable is None:
        sample_inputs = rows[0]['inputs']
        if len(sample_inputs) != 1:
            raise ValueError(f"pulse_patch supports single-input domains only, got {list(sample_inputs)}")
        variable = next(iter(sample_inputs))

    tol = tolerances or {observable: (Fraction(0), Fraction(0))}
    leader_str = leader if isinstance(leader, str) else model.render(leader)

    candidates = []
    seen_points = set()

    for row in rows:
        in_val = row['inputs'].get(variable)
        out_val = row['outputs'].get(observable)
        if in_val is None or out_val is None:
            continue
        exact_in = model.exact(in_val)
        if exact_in.denominator != 1:
            continue  # Integer domain only
        int_v = int(exact_in)
        if int_v in seen_points:
            continue
        seen_points.add(int_v)

        exact_out = model.exact(out_val)
        expr = f"ite(({variable} == {int_v}), {model.text(exact_out)}, {leader_str})"
        hyp = model.compile_hypothesis({
            'id': f"pulse_{variable}_{int_v}",
            'predict': {observable: expr}
        }, [variable], [observable])

        rep = model.replay(hyp, rows, tol)
        count = len(rep.get('counterexamples', []))
        candidates.append({
            'expression': expr,
            'point': int_v,
            'observed': exact_out,
            'hypothesis': hyp,
            'replay': rep,
            'counterexamples_count': count,
            'status': rep.get('status')
        })

    candidates.sort(key=lambda c: (c['counterexamples_count'], c['point']))
    return candidates


generate_pulse_patches = pulse_patch
