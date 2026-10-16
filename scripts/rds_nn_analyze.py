"""Exact structural analyses of bounded dense/ReLU networks for automated network research.

Every number is computed in rational arithmetic over the restricted model format of
``rds_nn_verify`` (rational weights, real ReLU semantics). It describes that exported model,
not IEEE floating-point execution, training or generalization. Each result says what it is:

* ``PROVED_OVER_BOX``: interval propagation is sound, so a DEAD unit is zero and an ACTIVE unit
  is linear for every input in the box. ``UNPROVED`` units may still be stable.
* ``UPPER_BOUND``: a Lipschitz constant no smaller than the true one.
* ``EXACT``: linear regions along a segment, Jacobians and ablation deltas.

None of these is a scientific claim. ``--compass-out`` writes the scalar summary as a Compass
measurement (``{"outputs": ...}``), which a campaign records as INPUT_REPORTED.

    python scripts/rds_nn_analyze.py regions --model model.json --request request.json
"""
import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path

from rds_nn_verify import _forward_step, _interval_step, _validate
from rds_verify_types import bounded, digest, rational, require

SCHEMA = 'rds-nn-analysis-v1'
ACTIONS = ('bounds', 'lipschitz', 'regions', 'jacobian', 'ablation')
MAX_PIECES = 4096
MAX_POINTS = 256
MAX_BYTES = 2 * 1024 * 1024


def network(model, box=None):
    """Validate through the ``rds_nn_verify`` model grammar; returns (layers, input box)."""
    require(isinstance(model, dict) and isinstance(model.get('layers'), list)
            and type(model.get('input_dim')) is int and model['input_dim'] >= 1, 'Model requires input_dim and layers')
    width = model['input_dim']
    for layer in model['layers']:
        if isinstance(layer, dict) and layer.get('kind') == 'linear' and isinstance(layer.get('weight'), list):
            width = len(layer['weight'])
    spec = {'schema': 1, 'kind': 'network_bounds', 'model': model,
            'input_box': box if box is not None else [['0', '0']] * model['input_dim'],
            'output_bounds': [['0', '0']] * max(width, 1)}
    layers, region, _, _, _ = _validate(spec)
    return layers, region


def _vector(values, dimension, label):
    require(isinstance(values, list) and len(values) == dimension, f'{label} must have {dimension} entries')
    return [rational(v) for v in values]


def _relu_unit(layers, ref):
    require(isinstance(ref, list) and len(ref) == 2 and all(type(v) is int for v in ref), 'Units are [layer, unit]')
    layer, unit = ref
    require(0 <= layer < len(layers) and layers[layer]['kind'] == 'relu' and unit >= 0, f'{ref} is not a ReLU unit')
    return layer, unit


def activation_bounds(model, box):
    """Units proved DEAD (pre-activation <= 0) or ACTIVE (>= 0) over the whole box; the rest UNPROVED."""
    layers, region = network(model, box)
    report, totals = [], {'dead': 0, 'active': 0, 'unproved': 0}
    for index, layer in enumerate(layers):
        if layer['kind'] == 'relu':
            dead = [j for j, (_, hi) in enumerate(region) if hi <= 0]
            active = [j for j, (lo, hi) in enumerate(region) if lo >= 0 < hi]
            unproved = [j for j, (lo, hi) in enumerate(region) if lo < 0 < hi]
            report.append({'layer': index, 'units': len(region), 'dead': dead, 'active': active, 'unproved': unproved})
            for key, units in (('dead', dead), ('active', active), ('unproved', unproved)):
                totals[key] += len(units)
        region = _interval_step(layer, region)
    return {'relu_layers': report, 'totals': totals, 'output_box': [[str(lo), str(hi)] for lo, hi in region],
            'assurance': 'PROVED_OVER_BOX', 'summary': totals}


def lipschitz_bound(model, box=None):
    """Upper bound on the l-inf operator norm of every Jacobian over the box (all inputs without one).

    Entrywise |J| <= |W_k| M ... M |W_1|, where M zeroes units proved DEAD over the box, so the
    largest row sum of that product bounds sup ||J||_inf, and therefore the l-inf Lipschitz
    constant on the convex box. The plain product of layer norms is reported for contrast.
    """
    layers, region = network(model, box)
    size = len(region)
    product = [[Fraction(int(i == j)) for j in range(size)] for i in range(size)]
    naive = Fraction(1)
    for layer in layers:
        if layer['kind'] == 'relu':
            if box is not None:
                product = [row if hi > 0 else [Fraction(0)] * size for row, (_, hi) in zip(product, region)]
        else:
            weights = [[abs(w) for w in row] for row in layer['weight']]
            product = [[bounded(sum(w * product[k][j] for k, w in enumerate(row) if w)) for j in range(size)]
                       for row in weights]
            naive = bounded(naive * max(sum(row) for row in weights))
        region = _interval_step(layer, region)
    bound = max(sum(row) for row in product)
    return {'lipschitz_upper': str(bound), 'layer_norm_product': str(naive), 'norm': 'LINF_TO_LINF',
            'scope': 'INPUT_BOX' if box is not None else 'ALL_INPUTS', 'assurance': 'UPPER_BOUND',
            'summary': {'lipschitz_upper': str(bound), 'layer_norm_product': str(naive)}}


def _affine(layer, offset, slope):
    rows = zip(layer['weight'], layer['bias'])
    return ([bounded(b + sum(w * o for w, o in zip(row, offset))) for row, b in rows],
            [bounded(sum(w * s for w, s in zip(row, slope))) for row in layer['weight']])


def linear_regions(model, start, end):
    """Exact pieces of x(t) = start + t (end - start), t in [0, 1], on which every ReLU pattern is fixed."""
    layers, region = network(model)
    a, b = _vector(start, len(region), 'start'), _vector(end, len(region), 'end')
    pieces = [(Fraction(0), Fraction(1), a, [y - x for x, y in zip(a, b)], ())]
    for layer in layers:
        following = []
        for t0, t1, offset, slope, pattern in pieces:
            if layer['kind'] == 'linear':
                following.append((t0, t1, *_affine(layer, offset, slope), pattern))
                continue
            cuts = sorted({-o / s for o, s in zip(offset, slope) if s and t0 < -o / s < t1})
            for u0, u1 in zip([t0] + cuts, cuts + [t1]):
                on = [o + s * (u0 + u1) / 2 > 0 for o, s in zip(offset, slope)]
                # Checked per piece, so a segment past the cap never materializes its descendants.
                require(len(following) < MAX_PIECES, f'The segment crosses more than {MAX_PIECES} linear pieces')
                following.append((u0, u1, [o if k else Fraction(0) for o, k in zip(offset, on)],
                                  [s if k else Fraction(0) for s, k in zip(slope, on)],
                                  pattern + (''.join('1' if k else '0' for k in on),)))
        pieces = following
    value = lambda piece, t: [str(o + s * t) for o, s in zip(piece[2], piece[3])]
    return {'regions': len(pieces), 'breakpoints': [str(p[0]) for p in pieces[1:]][:MAX_POINTS],
            'patterns': [list(p[4]) for p in pieces[:64]], 'outputs_at_start': value(pieces[0], Fraction(0)),
            'outputs_at_end': value(pieces[-1], Fraction(1)), 'assurance': 'EXACT',
            'summary': {'linear_regions': len(pieces)}}


def jacobian(model, point):
    """Exact Jacobian at a point; a pre-activation exactly at zero is reported, with ReLU'(0) = 0 used."""
    layers, region = network(model)
    x = _vector(point, len(region), 'point')
    rows, ties = [[Fraction(int(i == j)) for j in range(len(x))] for i in range(len(x))], []
    for index, layer in enumerate(layers):
        if layer['kind'] == 'relu':
            ties += [{'layer': index, 'unit': j} for j, v in enumerate(x) if v == 0]
            rows = [row if v > 0 else [Fraction(0)] * len(row) for row, v in zip(rows, x)]
        else:
            rows = [[bounded(sum(w * rows[k][j] for k, w in enumerate(weights) if w)) for j in range(len(point))]
                    for weights in layer['weight']]
        x = _forward_step(layer, x)
    norm = max(sum(abs(v) for v in row) for row in rows)
    return {'output': [str(v) for v in x], 'jacobian': [[str(v) for v in row] for row in rows],
            'status': 'NON_DIFFERENTIABLE' if ties else 'DIFFERENTIABLE', 'ties': ties[:64], 'linf_norm': str(norm),
            'assurance': 'EXACT', 'summary': {'jacobian_linf': str(norm), 'relu_ties': len(ties)}}


def ablation(model, points, units):
    """Exact output change at each point when the listed ReLU units are forced to zero."""
    layers, region = network(model)
    require(isinstance(points, list) and 1 <= len(points) <= MAX_POINTS, f'Ablation needs 1..{MAX_POINTS} points')
    require(isinstance(units, list) and units, 'Ablation needs at least one [layer, unit]')
    targets = {}
    for ref in units:
        layer, unit = _relu_unit(layers, ref)
        targets.setdefault(layer, set()).add(unit)
    deltas = []
    for point in points:
        base = ablated = _vector(point, len(region), 'point')
        for index, layer in enumerate(layers):
            base, ablated = _forward_step(layer, base), _forward_step(layer, ablated)
            if index in targets:
                require(max(targets[index]) < len(ablated), f'Unit outside ReLU layer {index}')
                ablated = [Fraction(0) if j in targets[index] else v for j, v in enumerate(ablated)]
        deltas.append([bounded(y - x) for x, y in zip(base, ablated)])
    largest = [max(abs(row[k]) for row in deltas) for k in range(len(deltas[0]))]
    mean = [sum(abs(row[k]) for row in deltas) / len(deltas) for k in range(len(deltas[0]))]
    return {'units': sorted([layer, unit] for layer, group in targets.items() for unit in group),
            'points': len(deltas), 'max_abs_delta': [str(v) for v in largest], 'mean_abs_delta': [str(v) for v in mean],
            'deltas': [[str(v) for v in row] for row in deltas[:64]], 'assurance': 'EXACT',
            'summary': {'max_abs_delta': str(max(largest)), 'max_mean_abs_delta': str(max(mean))}}


def analyze(action, model, request):
    require(action in ACTIONS, 'Unknown analysis: ' + str(action))
    require(isinstance(request, dict), 'Request must be an object')
    model = model.get('model', model) if isinstance(model, dict) and 'layers' not in model else model
    if action == 'bounds':
        result = activation_bounds(model, request.get('box'))
    elif action == 'lipschitz':
        result = lipschitz_bound(model, request.get('box'))
    elif action == 'regions':
        result = linear_regions(model, request.get('start'), request.get('end'))
    elif action == 'jacobian':
        result = jacobian(model, request.get('point'))
    else:
        result = ablation(model, request.get('points'), request.get('units'))
    return {'schema': SCHEMA, 'action': action, 'model_sha256': digest(model), **result,
            'semantics': 'rational_weights_and_real_dense_relu_arithmetic', 'scientific_support': 'UNKNOWN'}


def _read(path):
    raw = Path(path).read_bytes()
    require(len(raw) <= MAX_BYTES, f'{path} exceeds {MAX_BYTES} bytes')
    return json.loads(raw.decode('utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=ACTIONS)
    parser.add_argument('--model', required=True, help='Exact model JSON ({input_dim, layers}) or a verify spec')
    parser.add_argument('--request', help='JSON with box, start/end, point, or points and units')
    parser.add_argument('--compass-out', help='Also write {"outputs": summary} for a Compass campaign')
    args = parser.parse_args(argv)
    try:
        result = analyze(args.action, _read(args.model), _read(args.request) if args.request else {})
    except (ValueError, TypeError, KeyError, ArithmeticError, OSError) as exc:
        print(json.dumps({'schema': SCHEMA, 'status': 'INVALID', 'error': str(exc)[:500]}), file=sys.stderr)
        return 2
    if args.compass_out:
        Path(args.compass_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.compass_out).write_text(json.dumps({'outputs': result['summary']}, sort_keys=True), encoding='utf-8')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
