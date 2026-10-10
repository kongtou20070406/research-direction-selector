"""Bounded public Digits MLP task; evaluate finite method correctness on frozen outputs.

Fixed before execution: seed0, stratified60/20/20, two40-epoch classifiers,
two80-epoch decision heads, threshold0.5, no tuning/restarts, CPU one BLAS thread.
This compares analysis tools, not learning gains or autonomous research agents.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_result_tools import compare_metrics, compare_paired_metrics, check_residuals, diagnose_decisions


class WorkspacePreflightError(ValueError):
    """Rejected original evidence directory; this invocation owns no output files."""


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timed_operation(operation, check_deadline):
    check_deadline()
    at = time.perf_counter()
    operation()
    elapsed = time.perf_counter() - at
    check_deadline()
    return elapsed * 1000


def run(workspace):
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise WorkspacePreflightError('Workspace must be empty; preserve every original attempt')
    # Optional benchmark dependencies; never required for normal RDS use or CI.
    import numpy as np
    import scipy
    import sklearn
    from sklearn.datasets import load_digits
    from sklearn.model_selection import train_test_split
    from sklearn.neural_network import MLPClassifier, MLPRegressor
    from threadpoolctl import threadpool_limits

    started = time.perf_counter()
    def check_deadline():
        if time.perf_counter() - started >= 120:
            raise TimeoutError('Frozen 120-second benchmark allowance exhausted; preserve partial originals')

    protocol = {'dataset': 'scikit-learn load_digits: UCI handwritten digits test subset', 'seed': 0,
                'split': 'stratified 60% task-train / 20% head-fit / 20% heldout',
                'task_models': [[16, 8], [32, 16]], 'task_epochs': 40,
                'head_models': [[16, 8], [16, 8]], 'head_epochs': 80,
                'optimizer': 'Adam', 'batch_size': 64, 'learning_rate_init': 0.001,
                'numeric_protocol': 'float64; X/16; crossentropy clip[1e-15,1]',
                'gate': 'predicted_gain<0 AND p_better>=0.5', 'neutral_tolerance': 0,
                'threshold_selection': 'FROZEN_NO_TUNING', 'single_seed': True, 'blas_threads': 1,
                'wall_cap_seconds': 120, 'model_agent_arms': 'NOT_RUN'}
    protocol_sha = dump(root / 'protocol.json', protocol)
    versions = {'python': platform.python_version(), 'platform': platform.platform(),
                'numpy': np.__version__, 'scipy': scipy.__version__, 'sklearn': sklearn.__version__}
    dump(root / 'versions.json', versions)
    digits = load_digits()
    x, y = digits.data.astype(np.float64) / 16, digits.target
    all_ids = np.arange(len(y))
    train, other = train_test_split(all_ids, test_size=0.4, stratify=y, random_state=0)
    fit, heldout = train_test_split(other, test_size=0.5, stratify=y[other], random_state=0)
    data_sha = dump(root / 'splits.json', {'train': train.tolist(), 'head_fit': fit.tolist(), 'heldout': heldout.tolist()})
    np.savez(root / 'original-digits.npz', x=digits.data, y=y)
    dataset_sha = hashlib.sha256((root / 'original-digits.npz').read_bytes()).hexdigest()
    evaluator_text = 'Per-example loss=-log(clip(p[true digit],1e-15,1)); gain=candidate loss-baseline loss; benefit if gain<0.\n'
    (root / 'evaluator.txt').write_bytes(evaluator_text.encode('utf-8'))
    evaluator_sha = hashlib.sha256(evaluator_text.encode()).hexdigest()
    model_costs = []
    with threadpool_limits(limits=1):
        models = []
        for name, width in [('baseline', (16, 8)), ('candidate', (32, 16))]:
            model = MLPClassifier(hidden_layer_sizes=width, random_state=0, batch_size=64)
            at = time.perf_counter()
            for epoch in range(40):
                check_deadline()
                model.partial_fit(x[train], y[train], classes=np.arange(10))
            model_costs.append({'name': name, 'epochs': 40, 'wall_seconds': time.perf_counter() - at})
            dump(root / 'model-costs.json', model_costs)
            models.append(model)
            np.savez(root / (name + '-weights.npz'), **{f'weight{i}': a for i, a in enumerate(model.coefs_)},
                     **{f'bias{i}': a for i, a in enumerate(model.intercepts_)})
        probs = [m.predict_proba(x) for m in models]
        losses = [-np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1)) for p in probs]
        gains = losses[1] - losses[0]
        features = np.concatenate(probs, axis=1)  # available at prediction time; no true labels as features
        heads = [MLPRegressor(hidden_layer_sizes=(16, 8), random_state=0, batch_size=64),
                 MLPClassifier(hidden_layer_sizes=(16, 8), random_state=0, batch_size=64)]
        for head, name, target in zip(heads, ('gain-regressor', 'benefit-classifier'), (gains, gains < 0)):
            at = time.perf_counter()
            for epoch in range(80):
                check_deadline()
                if name == 'benefit-classifier':
                    head.partial_fit(features[fit], target[fit], classes=np.array([False, True]))
                else:
                    head.partial_fit(features[fit], target[fit])
            model_costs.append({'name': name, 'epochs': 80, 'wall_seconds': time.perf_counter() - at})
            dump(root / 'model-costs.json', model_costs)
            np.savez(root / (name + '-weights.npz'), **{f'weight{i}': a for i, a in enumerate(head.coefs_)},
                     **{f'bias{i}': a for i, a in enumerate(head.intercepts_)})
        predicted = heads[0].predict(features[heldout])
        benefit_index = int(np.flatnonzero(heads[1].classes_ == True)[0])
        p_better = heads[1].predict_proba(features[heldout])[:, benefit_index]
    raw_sha = dump(root / 'original-predictions.json', {'sample_ids': heldout.tolist(), 'true_digits': y[heldout].tolist(),
                  'baseline_probabilities': probs[0][heldout].tolist(), 'candidate_probabilities': probs[1][heldout].tolist(),
                  'baseline_loss': losses[0][heldout].tolist(), 'candidate_loss': losses[1][heldout].tolist(),
                  'predicted_gain': predicted.tolist(), 'p_better': p_better.tolist()})
    source_ids = ['digit-' + str(i) for i in heldout]
    points = []
    for index, name in enumerate(('baseline', 'candidate')):
        values = losses[index][heldout].tolist()
        source = {'values': values, 'sample_ids': source_ids}
        sha = dump(root / (name + '.json'), source)
        identity = {'run_id': name, 'source_path': name + '.json', 'source_sha256': sha,
                    'data_sha256': data_sha, 'data_split': 'heldout', 'evaluator_sha256': evaluator_sha,
                    'metric': 'crossentropy', 'metric_definition': evaluator_text.strip(),
                    'reduction': 'none', 'unit': 'nats', 'direction': 'minimize'}
        points.append({**source, 'identity': identity})
    document = {'rows': [{'pair_id': source_ids[j], 'group_id': 'true-digit-' + str(int(y[i])),
                         'gain': float(gains[i]), 'predicted_gain': float(predicted[j]), 'p_better': float(p_better[j]),
                         'stage': 'heldout', 'origin': 'natural', 'depth': 0,
                         'parent_id': None, 'parent_sha256': None, 'source_sha256': raw_sha}
                        for j, i in enumerate(heldout)], 'parents': []}
    diagnostic_sha = dump(root / 'decision-rows.json', document)
    diagnostic_raw = (root / 'decision-rows.json').read_text(encoding='utf-8')
    diagnostic_id = {'run_id': 'heldout-decision-heads', 'source_path': 'decision-rows.json',
                     'source_sha256': diagnostic_sha, 'data_split': 'heldout', 'evaluator_sha256': evaluator_sha}
    semantics = {'benefit_sign': 'negative', 'neutral_tolerance': 0, 'class_order': ['benefit', 'neutral', 'harm'],
                 'p_better_threshold': 0.5, 'protocol_id': protocol_sha, 'data_split': 'heldout',
                 'evaluator_sha256': evaluator_sha, 'stage': 'heldout'}
    sampling = {'unit': 'heldout example (writer correlation unknown)', 'independent': False,
                'pairing': 'matched_sample_ids'}
    # Independent NumPy oracle: no candidate tool contributes an expected count/value.
    truth = np.where(gains[heldout] < 0, 'benefit', np.where(gains[heldout] > 0, 'harm', 'neutral'))
    masks = {'regression': predicted < 0, 'classifier': p_better >= 0.5,
             'gate': (predicted < 0) & (p_better >= 0.5)}
    oracle = {name: {label: {'accepted': int(np.sum(mask & (truth == label))),
                           'support': int(np.sum(truth == label))} for label in ('benefit', 'harm', 'neutral')}
              for name, mask in masks.items()}
    dump(root / 'independent-oracle.json', oracle)
    scalar_ids = [dict(point['identity'], reduction='mean') for point in points]
    old = compare_metrics({'value': float(np.mean(losses[1][heldout])), 'identity': scalar_ids[1]},
                          {'value': float(np.mean(losses[0][heldout])), 'identity': scalar_ids[0]})
    paired = compare_paired_metrics(points[1], points[0], sampling)
    diagnostic = diagnose_decisions(diagnostic_raw, diagnostic_id, semantics)
    # Independent scalar math.log replay of every candidate loss, using original probabilities.
    reference = dict(points[1], values=[-math.log(max(1e-15, min(1, float(probs[1][i, y[i]])))) for i in heldout],
                     identity=dict(points[1]['identity'], run_id='scalar-math-reference',
                                   source_path='scalar-reference.json'))
    reference['identity']['source_sha256'] = dump(root / 'scalar-reference.json',
                                                 {'values': reference['values'], 'sample_ids': source_ids})
    residual = check_residuals(points[1], reference, {'domain': '360 frozen heldout probability/label pairs',
                              'precision': 'float64 numpy.log versus scalar math.log', 'atol': 1e-12,
                              'rtol': 1e-12, 'independent_reference': True})
    failures = []
    if paired['status'] != 'COMPARABLE' or abs(paired['mean_delta'] - float(np.mean(gains[heldout]))) > 1e-12:
        failures.append('paired loss difference mismatch')
    if paired['standard_error'] is not None:
        failures.append('unsupported independent uncertainty invented')
    if diagnostic['status'] != 'OBSERVED':
        failures.append('diagnostic unknown: ' + str(diagnostic.get('reasons')))
    for name in masks:
        for label in ('benefit', 'harm'):
            observed = diagnostic['heads'][name]['acceptance'][label]
            if any(observed[key] != oracle[name][label][key] for key in ('accepted', 'support')):
                failures.append(name + ' ' + label + ' oracle mismatch')
    if residual['within_tolerance'] is not True:
        failures.append('independent numerical residual mismatch')
    dump(root / 'tool-results.json', {'baseline_scalar_comparison': old, 'paired_comparison': paired,
                                    'decision_diagnostics': diagnostic, 'residual_check': residual})
    timings = {}
    workloads = {'existing_scalar': lambda: compare_metrics({'value': float(np.mean(losses[1][heldout])), 'identity': scalar_ids[1]},
                                                           {'value': float(np.mean(losses[0][heldout])), 'identity': scalar_ids[0]}),
                 'paired_360': lambda: compare_paired_metrics(points[1], points[0], sampling),
                 'diagnostic_360': lambda: diagnose_decisions(diagnostic_raw, diagnostic_id, semantics),
                 'residual_360': lambda: check_residuals(points[1], reference, residual['domain'])}
    for name, operation in workloads.items():
        measurements = []
        for repeat in range(30):
            measurements.append(_timed_operation(operation, check_deadline))
        timings[name] = {'repeats': 30, 'median_ms': statistics.median(measurements),
                         'min_ms': min(measurements), 'max_ms': max(measurements)}
    check_deadline()
    summary = {'status': 'PASS' if not failures else 'FAIL', 'failures': failures, 'versions': versions,
               'dataset_sha256': dataset_sha, 'protocol_sha256': protocol_sha, 'original_predictions_sha256': raw_sha,
               'source_sha256': {path: hashlib.sha256((REPO / path).read_bytes()).hexdigest()
                                  for path in ['scripts/rds_result_tools.py', 'benchmark/result-methods/run.py']},
               'samples': {'train': len(train), 'head_fit': len(fit), 'heldout': len(heldout)},
               'accuracy': {name: float(np.mean(probs[j][heldout].argmax(axis=1) == y[heldout]))
                            for j, name in enumerate(('baseline', 'candidate'))},
               'model_costs': model_costs, 'tool_timings': timings, 'oracle': oracle,
               'mean_loss_improvement': paired['mean_improvement'], 'residual_max': residual['max_abs_residual'],
               'total_wall_seconds': time.perf_counter() - started, 'cpu_seconds': 'NOT_MEASURED', 'gpu_used': False,
               'existing_scalar_scope': 'descriptive aggregate loss only; no head diagnosis',
               'analysis_speedup': 'NOT_COMPARABLE_DIFFERENT_OUTPUT_SCOPE',
               'model_quality_gain_from_rds': 'NOT_MEASURED', 'agent_comparison': 'NOT_RUN', 'scientific_gain': 'UNKNOWN'}
    check_deadline()
    dump(root / 'summary.json', summary)
    try:
        check_deadline()
    except TimeoutError as error:
        # This invocation owns the empty workspace. Settle its report as failed
        # if serialization/write/hash itself exhausted the original allowance.
        summary['status'] = 'FAIL'
        summary['failures'].append(str(error))
        summary['total_wall_seconds'] = time.perf_counter() - started
        try:
            dump(root / 'summary.json', summary)
        except OSError as artifact_error:
            error.add_note('Failed to settle summary artifact after allowance exhaustion: '
                           + repr(artifact_error))
        raise
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    try:
        result = run(args.workspace)
    except Exception as error:
        failure_path = Path(args.workspace) / 'failure.json'
        if not isinstance(error, WorkspacePreflightError) and failure_path.parent.is_dir() and not failure_path.exists():
            failure_path.write_text(json.dumps({'status': 'FAIL', 'error': type(error).__name__,
                                              'reason': str(error), 'automatic_retry': False}, indent=2), encoding='utf-8')
        raise
    print(json.dumps(result, indent=2, allow_nan=False))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
