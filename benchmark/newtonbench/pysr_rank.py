"""Rank PySR frontier candidates by prediction divergence on unmeasured inputs.

``rank_experiments`` ranks *experiment points*; it never ranks the laws themselves.
Under 1% noise the whole PySR frontier replays ``REFUTED``, every candidate carries
the same status, and ``compass tool rank`` has nothing left to sort. This module
scores the laws directly: over a lattice of inputs nobody has measured, a candidate's
per-point exclusion mass ``log2(n_live / n_same_class)`` is the information it alone
would buy if it were true. Replay status stays an evidence column, never the key.

Boundary: bits are summed per point, so they upper-bound joint information; they are
not the EIG of a measurement plan. Candidates tied on every unmeasured point means the
declared tolerance cannot separate them *here*, not that they are the same law.

    python -B benchmark/newtonbench/pysr_rank.py results.json measurements.json
"""
from itertools import product
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _directory in (ROOT / 'scripts', ROOT / 'benchmark' / 'newtonbench'):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))
import rds_compass_model as model

DEFAULT_RELATIVE = '2/100'   # replay band of pysr_rds_derived.py: abs 0, rel 2%
MAX_POINTS = 256


def _key(env):
    """Exact identity of one input point, order-free."""
    return model.canonical({name: model.text(value) for name, value in env.items()})


def unmeasured_grid(rows, *, limit=MAX_POINTS):
    """Deterministic min/mid/max lattice over the measured input box.

    Measured points are dropped, so every returned point is genuinely untested.
    A box that would explode past ``limit`` fails closed: pass ``points`` instead.
    """
    model.require(rows, 'Measurements are required to place the unmeasured lattice')
    names = sorted(rows[0]['inputs'])
    model.require(names, 'Measurements must declare at least one input')
    columns = {}
    for name in names:
        values = sorted({model.exact(row['inputs'][name]) for row in rows})
        columns[name] = sorted({values[0], values[-1], (values[0] + values[-1]) / 2})
    size = 1
    for name in names:
        size *= len(columns[name])
    model.require(size <= limit, 'Unmeasured lattice exceeds %d points; pass explicit points' % limit)
    measured = {_key(row['inputs']) for row in rows}
    points = []
    for combination in product(*(columns[name] for name in names)):
        env = dict(zip(names, combination))
        if _key(env) in measured:
            continue
        points.append({'id': 'g%d' % len(points),
                       'inputs': {name: model.text(value) for name, value in env.items()}})
    return points


def normalize_points(points):
    """Bind caller-supplied probe points to exact text and stable ids."""
    return [{'id': str(item.get('id', 'g%d' % index)),
             'inputs': {name: model.text(value) for name, value in item['inputs'].items()}}
            for index, item in enumerate(points)]


def compile_candidates(record, inputs, observable):
    """Compile every frontier entry into a bounded RDS rulebook.

    ``rds`` is preferred because the adapter already mapped derived names back to
    the original variables; ``equation`` is tried as-is and fails closed if the
    dialect rejects it. Rejections are excluded with a reason, never dropped.
    """
    compiled, excluded = [], []
    for index, entry in enumerate(record.get('results') or []):
        ident = str(entry.get('id') or 'c%d' % index)
        expression = entry.get('rds') or entry.get('equation')
        if not expression:
            excluded.append({'id': ident, 'reason': 'NO_EXPRESSION'})
            continue
        try:
            hypothesis = model.compile_hypothesis(
                {'id': ident, 'predict': {observable: expression}}, inputs, [observable])
        except (ValueError, KeyError, TypeError) as exc:
            excluded.append({'id': ident, 'reason': 'COMPILE_ERROR: ' + str(exc)[:120]})
            continue
        compiled.append({'id': ident, 'entry': entry, 'hypothesis': hypothesis,
                         'equation': entry.get('equation'), 'rds': entry.get('rds')})
    return compiled, excluded


def _evidence(item, rows, observable, band):
    """Replay status as an evidence column; recorded value wins, else replay here."""
    entry = item['entry']
    recorded = entry.get('replay')
    if recorded is not None:
        evidence = {'replay': recorded, 'replay_source': 'recorded'}
        if entry.get('counterexamples') is not None:
            evidence['counterexamples'] = entry['counterexamples']
        return evidence
    verdict = model.replay(item['hypothesis'], rows, {observable: band})
    return {'replay': verdict['status'], 'replay_source': 'computed',
            'counterexamples': len(verdict['counterexamples'])}


def _entropy(sizes):
    total = sum(sizes)
    return -sum(size / total * math.log2(size / total) for size in sizes if size) if total else 0.0


def rank_candidates(record, rows, *, observable='y', tolerance=None, inputs=None, points=None):
    """Rank frontier candidates without requiring replay to pass.

    ``tolerance`` is an ``{'abs': ..., 'rel': ...}`` dict; the default reproduces the
    replay band of ``pysr_rds_derived.py``. Sorting key: ``discrimination_bits``
    (descending), then the summed per-point rival count, then the number of points
    the candidate could be judged on, then id (the PySR frontier order).
    Candidates whose partition matches a better-ranked one are merged, not ranked
    twice, exactly as ``rank_experiments`` does for experiment points.
    """
    rows = list(rows or [])
    if inputs is None:
        model.require(rows, 'Measurements or explicit inputs are required')
        inputs = sorted(rows[0]['inputs'])
    inputs = list(inputs)
    band = model.tolerance(tolerance if tolerance is not None else {'rel': DEFAULT_RELATIVE})
    compiled, excluded = compile_candidates(record, inputs, observable)
    probes = normalize_points(points) if points is not None else unmeasured_grid(rows)
    empty = {'ranked': [], 'ranked_total': 0, 'merged': [], 'excluded': excluded,
             'probes': [], 'selection_effect': 'ADVISORY', 'scientific_support': 'UNKNOWN'}
    if not compiled:
        return dict(empty, note='NO_CANDIDATES')
    if not probes:
        return dict(empty, note='NO_UNMEASURED_POINTS',
                    excluded=excluded + [{'id': item['id'], 'reason': 'NO_UNMEASURED_POINTS'}
                                         for item in compiled])

    hypotheses = [item['hypothesis'] for item in compiled]
    bits = {item['id']: 0.0 for item in compiled}
    rivals = {item['id']: 0 for item in compiled}
    judged = {item['id']: 0 for item in compiled}
    signature = {item['id']: [] for item in compiled}
    probe_summary = []
    for point in probes:
        partition, undetermined = model.outcome_partition(
            hypotheses, point['inputs'], {observable: band})
        placed = {}
        for row in partition:
            token = row['prediction'] if isinstance(row['prediction'], str) else model.canonical(row['prediction'])
            for ident in row['hypotheses']:
                placed[ident] = (token, len(row['hypotheses']))
        live = len(placed)
        for ident, (token, size) in placed.items():
            judged[ident] += 1
            signature[ident].append([point['id'], token])
            if live > 1:
                bits[ident] += math.log2(live / size)
                rivals[ident] += live - size
        for ident in undetermined:
            signature[ident].append([point['id'], None])
        probe_summary.append({'id': point['id'], 'inputs': point['inputs'], 'classes': len(partition),
                              'entropy_bits': round(_entropy([len(row['hypotheses']) for row in partition]), 9)})

    scored = []
    for item in compiled:
        ident = item['id']
        scored.append(dict(_evidence(item, rows, observable, band),
                           id=ident, equation=item['equation'], rds=item['rds'],
                           discrimination_bits=round(bits[ident], 9),
                           distinguishable_rivals=rivals[ident], comparable_points=judged[ident],
                           partition_sha256=model.digest(signature[ident])))
    scored.sort(key=lambda row: (-row['discrimination_bits'], -row['distinguishable_rivals'],
                                 -row['comparable_points'], row['id']))
    ranked, merged, partitions = [], [], {}
    for row in scored:
        if row['partition_sha256'] in partitions:
            merged.append({'id': row['id'], 'equivalent_to': partitions[row['partition_sha256']],
                           'reason': 'EQUIVALENT_PARTITION'})
            continue
        partitions[row['partition_sha256']] = row['id']
        ranked.append(row)
    return {'ranked': ranked, 'ranked_total': len(ranked), 'merged': merged, 'excluded': excluded,
            'probes': probe_summary, 'selection_effect': 'ADVISORY', 'scientific_support': 'UNKNOWN'}


if __name__ == '__main__':
    record = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    measurements = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    print(json.dumps(rank_candidates(record, measurements), indent=1))
