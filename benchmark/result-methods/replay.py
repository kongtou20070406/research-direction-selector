"""Replay saved Digits outputs with stdlib only; never fit or retry a model.

Checks refer to the published finite development record. Requirements were
selected after that record was inspected, and are not unseen safety evidence.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_result_tools import (_document, compare_metrics, compare_paired_metrics,
                              check_residuals, diagnose_decisions, evaluate_decision_requirements)
from rds_bounded_io import read_regular_bytes

REQUIREMENTS = {'head': 'gate', 'min_benefit_support': 20, 'min_harm_support': 20,
                'min_benefit_acceptance': 0.8, 'max_harm_acceptance': 0.1}
FILES = ('summary.json', 'protocol.json', 'splits.json', 'versions.json', 'model-costs.json',
         'original-predictions.json', 'decision-rows.json', 'independent-oracle.json', 'tool-results.json',
         'baseline.json', 'candidate.json', 'scalar-reference.json', 'evaluator.txt', 'receipt-fields.json',
         'source-rds_result_tools.py.txt', 'source-benchmark-run.py.txt')
MAX_RECORD_BYTES = 2 * 1024 * 1024


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def equivalent(left, right):
    """Retain exact structure/counts; allow declared float64 replay tolerance."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equivalent(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(equivalent(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def replay(records):
    started = time.perf_counter()
    root = Path(records).resolve()
    raw = {name: read_regular_bytes(root / name, MAX_RECORD_BYTES, label='Replay record ' + name)
           for name in FILES}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
    data = {name: _document(value.decode('utf-8')) for name, value in raw.items() if name.endswith('.json')}
    summary, receipt, saved = (data[name] for name in ('summary.json', 'receipt-fields.json', 'tool-results.json'))
    require(summary['protocol_sha256'] == hashes['protocol.json'] and
            summary['original_predictions_sha256'] == hashes['original-predictions.json'], 'Original source hash mismatch')
    for path, copy in (('scripts/rds_result_tools.py', 'source-rds_result_tools.py.txt'),
                       ('benchmark/result-methods/run.py', 'source-benchmark-run.py.txt')):
        require(summary['source_sha256'][path] == hashes[copy], 'Executed source copy hash mismatch')
        require(any(b['path'] == path and b['sha256'] == hashes[copy] for b in receipt['bindings_before']) and
                receipt['bindings_before'] == receipt['bindings_after'], 'Original receipt source binding mismatch')
    require(any(a['kind'] == 'project_output' and a['sha256'] == hashes['summary.json'] for a in receipt['artifacts']),
            'Original receipt output hash mismatch')
    require(receipt['run_status'] == 'SUCCEEDED' and receipt['exit_code'] == 0 and not receipt['timeout'],
            'Original attempt did not succeed')
    require(summary['model_costs'] == data['model-costs.json'], 'Original fitting costs mismatch')
    protocol = data['protocol.json']
    require(protocol['gate'] == 'predicted_gain<0 AND p_better>=0.5' and protocol['neutral_tolerance'] == 0,
            'Unsupported original gate protocol')
    splits, original, document = (data[name] for name in ('splits.json', 'original-predictions.json', 'decision-rows.json'))
    groups = [splits[k] for k in ('train', 'head_fit', 'heldout')]
    ids = [v for group in groups for v in group]
    require(all(type(v) is int and 0 <= v < 1797 for v in ids) and len(ids) == len(set(ids)) == 1797,
            'Original split IDs overlap or omit dataset members')
    require(summary['samples'] == {k: len(splits[k]) for k in ('train', 'head_fit', 'heldout')} and
            original['sample_ids'] == splits['heldout'], 'Original heldout IDs/order mismatch')
    count = len(original['sample_ids'])
    require(1 <= count <= 2048 and all(len(v) == count for v in original.values()), 'Original column length mismatch')
    require(all(type(v) is int and 0 <= v < 10 for v in original['true_digits']), 'Original digit label invalid')
    losses = {}
    for name in ('baseline', 'candidate'):
        probabilities = original[name + '_probabilities']
        require(all(isinstance(p, list) and len(p) == 10 and all(finite(v) and 0 <= v <= 1 for v in p)
                    and math.isclose(sum(p), 1, abs_tol=1e-12) for p in probabilities), 'Original probabilities invalid')
        losses[name] = [-math.log(max(1e-15, min(1, p[label])))
                        for p, label in zip(probabilities, original['true_digits'])]
        require(all(finite(v) and abs(v - ref) <= 1e-12 + 1e-12 * abs(ref)
                    for v, ref in zip(original[name + '_loss'], losses[name])), 'Original loss/probability mismatch')
    require(all(finite(v) for v in original['predicted_gain']) and
            all(finite(v) and 0 <= v <= 1 for v in original['p_better']), 'Original head values invalid')
    require(len(document['rows']) == count and document['parents'] == [], 'Original decision rows mismatch')
    oracle = {head: {label: {'accepted': 0, 'support': 0} for label in ('benefit', 'harm', 'neutral')}
              for head in ('regression', 'classifier', 'gate')}
    disagreements = 0
    for i, row in enumerate(document['rows']):
        gain = original['candidate_loss'][i] - original['baseline_loss'][i]
        expected = {'pair_id': 'digit-' + str(original['sample_ids'][i]),
                    'group_id': 'true-digit-' + str(original['true_digits'][i]), 'gain': gain,
                    'predicted_gain': original['predicted_gain'][i], 'p_better': original['p_better'][i],
                    'stage': 'heldout', 'origin': 'natural', 'depth': 0, 'parent_id': None, 'parent_sha256': None,
                    'source_sha256': hashes['original-predictions.json']}
        require(row == expected, 'Original decision row/value/identity mismatch')
        label = 'benefit' if gain < 0 else 'harm' if gain > 0 else 'neutral'
        regression, classifier = original['predicted_gain'][i] < 0, original['p_better'][i] >= 0.5
        disagreements += regression != classifier
        for head, accepted in (('regression', regression), ('classifier', classifier), ('gate', regression and classifier)):
            oracle[head][label]['support'] += 1
            oracle[head][label]['accepted'] += int(accepted)
    require(oracle == data['independent-oracle.json'] == summary['oracle'], 'Independent oracle mismatch')
    # The original Windows writer hashed LF text before write_text produced CRLF.
    # Preserve that record, expose the mismatch, and rebind current calls to bytes.
    evaluator_sha = hashes['evaluator.txt']
    claimed_evaluator_sha = saved['decision_diagnostics']['identity']['evaluator_sha256']
    evaluator_text = raw['evaluator.txt'].decode('utf-8').replace('\r\n', '\n')
    expected_evaluator = 'Per-example loss=-log(clip(p[true digit],1e-15,1)); gain=candidate loss-baseline loss; benefit if gain<0.\n'
    require(evaluator_text == expected_evaluator and
            claimed_evaluator_sha in {evaluator_sha, hashlib.sha256(evaluator_text.encode('utf-8')).hexdigest()},
            'Original evaluator content/identity mismatch')

    def rebind(value):
        if isinstance(value, dict):
            return {k: evaluator_sha if k == 'evaluator_sha256' and v == claimed_evaluator_sha else rebind(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [rebind(v) for v in value]
        return value

    saved = rebind(saved)
    diagnostic = saved['decision_diagnostics']
    identity, semantics = diagnostic['identity'], diagnostic['semantics']
    require(identity['source_sha256'] == hashes['decision-rows.json'] and
            identity['source_path'] == 'decision-rows.json' and identity['data_split'] == 'heldout' and
            identity['evaluator_sha256'] == evaluator_sha, 'Diagnostic identity mismatch')
    expected_semantics = {'benefit_sign': 'negative', 'neutral_tolerance': 0, 'class_order': ['benefit', 'neutral', 'harm'],
                          'p_better_threshold': 0.5, 'protocol_id': hashes['protocol.json'], 'data_split': 'heldout',
                          'evaluator_sha256': evaluator_sha, 'stage': 'heldout'}
    require(semantics == expected_semantics, 'Diagnostic semantics mismatch')
    points = {}
    for name, key in (('baseline', 'baseline'), ('candidate', 'candidate'), ('scalar-reference', 'candidate')):
        point_identity = saved['residual_check']['identity']['reference'] if name == 'scalar-reference' else saved['paired_comparison']['identity'][name]
        source = data[name + '.json']
        require(point_identity['source_path'] == name + '.json' and point_identity['source_sha256'] == hashes[name + '.json'] and
                point_identity['data_sha256'] == hashes['splits.json'] and point_identity['evaluator_sha256'] == evaluator_sha,
                'Point source/data/evaluator identity mismatch')
        require(source['sample_ids'] == ['digit-' + str(v) for v in original['sample_ids']], 'Point IDs mismatch')
        expected_values = losses[key] if name == 'scalar-reference' else original[name + '_loss']
        require(equivalent(source['values'], expected_values), 'Point values disagree with original predictions')
        points[name] = {**source, 'identity': point_identity}
    current = {'paired_comparison': compare_paired_metrics(points['candidate'], points['baseline'], saved['paired_comparison']['sampling']),
               'decision_diagnostics': diagnose_decisions(raw['decision-rows.json'].decode('utf-8'), identity, semantics),
               'residual_check': check_residuals(points['candidate'], points['scalar-reference'], saved['residual_check']['domain'])}
    require(all(equivalent(current[k], saved[k]) for k in current), 'Saved result replay mismatch')
    scalar = saved['baseline_scalar_comparison']
    for name in ('baseline', 'candidate'):
        require(scalar[name]['identity'] == dict(points[name]['identity'], reduction='mean') and
                math.isclose(scalar[name]['value'], sum(losses[name]) / count, abs_tol=1e-12), 'Scalar mean/identity mismatch')
    current['baseline_scalar_comparison'] = compare_metrics(*[{'value': scalar[name]['value'], 'identity': scalar[name]['identity']}
                                                            for name in ('candidate', 'baseline')])
    require(equivalent(current['baseline_scalar_comparison'], scalar) and
            current['decision_diagnostics']['head_disagreements']['regression_classifier'] == disagreements,
            'Scalar or head disagreement replay mismatch')
    requirement_args = (raw['decision-rows.json'].decode('utf-8'), identity, semantics, REQUIREMENTS)
    requirement_result = evaluate_decision_requirements(*requirement_args)
    require(requirement_result['status'] == 'FAIL' and requirement_result['met'] is False and
            requirement_result['checks']['max_harm_acceptance']['status'] == 'FAIL', 'Unfavorable gate requirement lost')
    timings = []
    for _ in range(30):
        at = time.perf_counter()
        evaluate_decision_requirements(*requirement_args)
        timings.append((time.perf_counter() - at) * 1000)
    return {'status': 'PASS', 'scope': 'SAVED_OUTPUT_DEVELOPMENT_REPLAY', 'training_executions': 0,
            'records_sha256': hashes, 'current_source_sha256': {path: hashlib.sha256((REPO / path).read_bytes()).hexdigest()
                for path in ('scripts/rds_result_tools.py', 'benchmark/result-methods/replay.py')},
            'original_attempt_id': receipt['attempt_id'], 'original_receipt_sha256': receipt['sha256'],
            'original_worker_wall_seconds': receipt['resources']['wall_seconds']['measured'],
            'original_model_costs': summary['model_costs'], 'original_receipt_assurance': 'EXTRACTED_FIELDS_AND_ARTIFACT_HASHES',
            'evaluator_binding': {'original_claimed_sha256': claimed_evaluator_sha, 'actual_bytes_sha256': evaluator_sha,
                'original_status': 'MATCHED' if claimed_evaluator_sha == evaluator_sha else 'NEWLINE_HASH_MISMATCH',
                'current_status': 'REBOUND_TO_ACTUAL_BYTES', 'original_files_modified': False},
            'samples': count, 'independent_oracle': oracle, 'head_disagreements': disagreements,
            'replayed_results': current, 'decision_requirements': requirement_result,
            'requirement_selection': 'DEVELOPMENT_AFTER_ORIGINAL_OUTPUT_INSPECTION',
            'requirement_timings': {'repeats': 30, 'median_ms': statistics.median(timings),
                                   'min_ms': min(timings), 'max_ms': max(timings)},
            'incremental_wall_seconds': time.perf_counter() - started, 'scientific_gain': 'UNKNOWN',
            'model_quality_gain_from_rds': 'NOT_MEASURED', 'sqlite_contention': 'NOT_MEASURED'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    records = Path(args.records).resolve()
    require(not output.exists() and not output.is_relative_to(records), 'Choose a new output outside original records')
    result = replay(records)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as target:
        json.dump(result, target, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2, allow_nan=False))
