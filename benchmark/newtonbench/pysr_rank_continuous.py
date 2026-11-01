"""Rank PySR frontier candidates by continuous prediction divergence.

``pysr_rank`` scores a candidate by partition entropy, so ``1`` vs ``10`` and ``1``
vs ``10**9`` both count as "a separate class": the magnitude of the disagreement is
thrown away and every lone candidate saturates at ``sum_p log2(n_live)``. This module
keeps the gap. For every ordered pair of candidates and every unmeasured input,

    d_p(a, b) = |pred_a - pred_b| / max(|pred_a|, |pred_b|, eps)

is a scale-free relative gap in ``[0, 2]`` (a sign flip reaches 2; it is not clipped,
because clipping would flatten a flip into a mere 100% error). A candidate's score is
the mean over its rivals of the mean gap over comparable points, so the score does not
grow with the size of the frontier and two fronts of different length stay comparable.

Replay stays an evidence column. Equivalence is still decided by the RDS partition at
the declared tolerance, not by this score, so a "small but real" gap never merges two
candidate laws into one.

Boundaries: the gap is evaluated at the point values, not through the interval
enclosure, so ``Inexact`` predictions enter at their midpoint. A frontier that cannot
be judged at any unmeasured point fails closed instead of returning a zero ranking.

    python -B benchmark/newtonbench/pysr_rank_continuous.py results.json measurements.json
"""
from fractions import Fraction
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _directory in (ROOT / 'scripts', ROOT / 'benchmark' / 'newtonbench'):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))
import rds_compass_model as model
import pysr_rank

EPSILON = Fraction(1, 10 ** 12)


def relative_gap(predicted, other):
    """Scale-free gap between two predictions; unclipped, so a sign flip gives 2."""
    scale = max(abs(predicted), abs(other), EPSILON)
    return abs(predicted - other) / scale


def _prediction(hypothesis, env, observable):
    values, status = model.predict(hypothesis, env)
    return values[observable] if status == 'PREDICTED' else None


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


def _partitions(hypotheses, probes, observable, band):
    """Order-free partition identity and per-point size summary, as in pysr_rank."""
    signature = {hypothesis['id']: [] for hypothesis in hypotheses}
    summary = []
    for point in probes:
        partition, undetermined = model.outcome_partition(
            hypotheses, point['inputs'], {observable: band})
        for row in partition:
            token = row['prediction'] if isinstance(row['prediction'], str) else model.canonical(row['prediction'])
            for ident in row['hypotheses']:
                signature[ident].append([point['id'], token])
        for ident in undetermined:
            signature[ident].append([point['id'], None])
        summary.append({'id': point['id'], 'inputs': point['inputs'], 'classes': len(partition)})
    return {ident: model.digest(vector) for ident, vector in signature.items()}, summary


def rank_candidates(record, rows, *, observable='y', tolerance=None, inputs=None, points=None):
    """Rank frontier candidates by mean relative prediction gap on unmeasured inputs.

    ``tolerance`` is an ``{'abs': ..., 'rel': ...}`` dict and only decides equivalence
    (which laws behave identically), never the score itself. Sorting key:
    ``divergence`` (descending), then the single worst point gap, then the number of
    comparable candidate-point pairs, then id (the PySR frontier order).
    """
    rows = list(rows or [])
    if inputs is None:
        model.require(rows, 'Measurements or explicit inputs are required')
        inputs = sorted(rows[0]['inputs'])
    inputs = list(inputs)
    band = model.tolerance(tolerance if tolerance is not None else {'rel': pysr_rank.DEFAULT_RELATIVE})
    compiled, excluded = pysr_rank.compile_candidates(record, inputs, observable)
    probes = pysr_rank.normalize_points(points) if points is not None else pysr_rank.unmeasured_grid(rows)
    empty = {'ranked': [], 'ranked_total': 0, 'merged': [], 'excluded': excluded,
             'probes': [], 'selection_effect': 'ADVISORY', 'scientific_support': 'UNKNOWN'}
    if not compiled:
        return dict(empty, note='NO_CANDIDATES')
    if not probes:
        return dict(empty, note='NO_UNMEASURED_POINTS',
                    excluded=excluded + [{'id': item['id'], 'reason': 'NO_UNMEASURED_POINTS'}
                                         for item in compiled])

    identifiers = [item['id'] for item in compiled]
    partitions, probe_summary = _partitions(
        [item['hypothesis'] for item in compiled], probes, observable, band)
    groups = {}
    for ident in identifiers:
        groups.setdefault(partitions[ident], []).append(ident)
    # Divergence is measured between equivalence classes, not between frontier copies:
    # a duplicated law must not dilute the survivor's score by adding zero-gap rivals.
    leader = {ident: min(members) for members in groups.values() for ident in members}
    predictions = []
    for point in probes:
        values = {}
        for item in compiled:
            value = _prediction(item['hypothesis'], point['inputs'], observable)
            if value is not None:
                values[item['id']] = value
        predictions.append(values)

    totals = {ident: Fraction(0) for ident in identifiers}
    peaks = {ident: Fraction(0) for ident in identifiers}
    rivals = {ident: 0 for ident in identifiers}
    leaders = sorted(set(leader.values()))
    for index, left in enumerate(leaders):
        for right in leaders[index + 1:]:
            gaps = [relative_gap(values[left], values[right])
                    for values in predictions if left in values and right in values]
            if not gaps:
                continue
            mean_gap = sum(gaps) / len(gaps)
            peak = max(gaps)
            for ident in (left, right):
                totals[ident] += mean_gap
                peaks[ident] = max(peaks[ident], peak)
                rivals[ident] += 1
    if not rivals or max(rivals.values()) == 0:
        return dict(empty, note='NO_COMPARABLE_PREDICTIONS',
                    excluded=excluded + [{'id': ident, 'reason': 'NO_COMPARABLE_PREDICTIONS'}
                                         for ident in identifiers])

    scored = []
    for item in compiled:
        ident = item['id']
        source = leader[ident]
        mean = totals[source] / rivals[source] if rivals[source] else Fraction(0)
        scored.append(dict(_evidence(item, rows, observable, band),
                           id=ident, equation=item['equation'], rds=item['rds'],
                           divergence=round(float(mean), 9),
                           worst_point_gap=round(float(peaks[source]), 9),
                           comparable_rivals=rivals[source],
                           partition_sha256=partitions[ident]))
    scored.sort(key=lambda row: (-row['divergence'], -row['worst_point_gap'],
                                 -row['comparable_rivals'], row['id']))
    ranked, merged, seen = [], [], {}
    for row in scored:
        if row['partition_sha256'] in seen:
            merged.append({'id': row['id'], 'equivalent_to': seen[row['partition_sha256']],
                           'reason': 'EQUIVALENT_PARTITION'})
            continue
        seen[row['partition_sha256']] = row['id']
        ranked.append(row)
    return {'ranked': ranked, 'ranked_total': len(ranked), 'merged': merged, 'excluded': excluded,
            'probes': probe_summary, 'selection_effect': 'ADVISORY', 'scientific_support': 'UNKNOWN'}


if __name__ == '__main__':
    record = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    measurements = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    print(json.dumps(rank_candidates(record, measurements), indent=1))
