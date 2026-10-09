"""Explicit independent finite oracles for the public owned consumer example."""
import hashlib
import json


def specifications():
    sources = []

    def source(path, value):
        text = json.dumps(value, indent=2, allow_nan=False)
        sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
        sources.append({'path': path, 'text': text, 'sha256': sha})
        return sha

    data_sha = source('sources/data.json', {'sample_ids': ['a', 'b']})
    evaluator_sha = source('sources/evaluator.json', {'metric': 'loss', 'benefit_sign': 'negative',
                                                     'gate': 'predicted gain < 0 AND p_better >= 0.5'})

    def points(name, values):
        path = 'sources/' + name + '.json'
        sha = source(path, {'values': values, 'sample_ids': ['a', 'b']})
        return {'values': values, 'sample_ids': ['a', 'b'],
                'identity': {'run_id': name, 'source_path': path, 'source_sha256': sha,
                             'data_sha256': data_sha, 'data_split': 'finite-development',
                             'evaluator_sha256': evaluator_sha, 'metric': 'loss',
                             'metric_definition': 'finite per-example loss', 'reduction': 'none',
                             'unit': 'dimensionless', 'direction': 'minimize'}}

    candidate, baseline, reference = points('candidate', [1, 3]), points('baseline', [3, 5]), points('reference', [1, 3])
    sampling = {'unit': 'finite example; dependence unknown', 'independent': False, 'pairing': 'matched_sample_ids'}
    common = {'identity_assurance': 'CALLER_METADATA', 'scientific_support': 'UNKNOWN'}
    paired_expected = {'operation': 'compare_paired_metrics', 'status': 'COMPARABLE', 'reason': None,
                       'identity': {'candidate': candidate['identity'], 'baseline': baseline['identity']},
                       'count': 2, 'sampling': sampling, 'mean_delta': -2.0, 'mean_improvement': 2.0,
                       'standard_error': None, 'uncertainty_status': 'UNKNOWN',
                       'uncertainty_reason': 'INDEPENDENCE_NOT_ESTABLISHED',
                       'missing_fields': [], 'conflicting_fields': [], **common}
    domain = {'domain': 'two declared finite numeric points', 'precision': 'Python integer arithmetic',
              'atol': 0, 'rtol': 0, 'independent_reference': True}
    residual_expected = {'operation': 'check_residuals', 'status': 'OBSERVED', 'reason': None,
                         'identity': {'candidate': candidate['identity'], 'reference': reference['identity']},
                         'domain': domain, 'count': 2, 'max_abs_residual': 0, 'violations': 0,
                         'within_tolerance': True, 'worst_pointer': '/values/0',
                         'missing_fields': [], 'conflicting_fields': [],
                         'assurance': 'FINITE_NUMERICAL_OBSERVATION', **common}
    upstream = source('sources/predictions.json', [[-1, -1, 0.9], [1, 1, 0.1]])
    rows = [{'pair_id': pair, 'group_id': 'A', 'gain': gain, 'predicted_gain': gain,
             'p_better': probability, 'stage': 'development', 'origin': 'natural', 'depth': 0,
             'source_sha256': upstream, 'parent_id': None, 'parent_sha256': None}
            for pair, gain, probability in [('a', -1, 0.9), ('b', 1, 0.1)]]
    document = {'rows': rows, 'parents': []}
    doc_sha = source('sources/decisions.json', document)
    raw = sources[-1]['text']
    identity = {'run_id': 'decisions', 'source_path': 'sources/decisions.json', 'source_sha256': doc_sha,
                'data_split': 'finite-development', 'evaluator_sha256': evaluator_sha}
    semantics = {'benefit_sign': 'negative', 'neutral_tolerance': 0, 'class_order': ['benefit', 'neutral', 'harm'],
                 'p_better_threshold': 0.5, 'protocol_id': 'finite-decision-v1', 'data_split': 'finite-development',
                 'evaluator_sha256': evaluator_sha, 'stage': 'development'}
    # Values below are specified by the two-row oracle, never by a candidate call.
    heads = {name: {'confusion': {'benefit': {'accepted': 1, 'rejected': 0},
                                 'neutral': {'accepted': 0, 'rejected': 0}, 'harm': {'accepted': 0, 'rejected': 1}},
                    'acceptance': {'benefit': {'accepted': 1, 'support': 1, 'rate': 1.0, 'status': 'OBSERVED'},
                                   'neutral': {'accepted': 0, 'support': 0, 'rate': None, 'status': 'UNKNOWN'},
                                   'harm': {'accepted': 0, 'support': 1, 'rate': 0.0, 'status': 'OBSERVED'}}}
             for name in ('regression', 'classifier', 'gate')}
    diagnostic_expected = {'operation': 'diagnose_decisions', 'status': 'OBSERVED', 'observation_status': 'OBSERVED',
        'support_status': 'OBSERVED', 'identity': identity, 'semantics': semantics,
        'classifier_mode': 'benefit_vs_rest_threshold', 'gate_rule': 'regression_benefit_and_p_better_at_least_threshold',
        'class_support': {'benefit': 1, 'neutral': 0, 'harm': 1}, 'heads': heads,
        'head_disagreements': {'regression_classifier': 0},
        'rejection_reasons': {'regression_only': 0, 'classifier_only': 0, 'both': 1, 'accepted': 1},
        'groups': {'status': 'OBSERVED', 'scope': 'VALID_ROWS_ONLY', 'group_count': 1,
                   'constant_classifier_mixed_truth_count': 0, 'entries': [
                       {'group_id': 'A', 'row_count': 2, 'classifier_constant': False,
                        'truth_mixed': True, 'truth_classes': ['benefit', 'harm']}], 'omitted_groups': 0},
        'depth_counts': {'0': 2}, 'origin_counts': {'natural': 2}, 'stage_counts': {'development': 2},
        'entries': [{'pointer': '/rows/' + str(i), 'pair_id': pair, 'group_id': 'A', 'status': 'OBSERVED',
                     'truth': truth, 'predictions': {name: i == 0 for name in ('regression', 'classifier', 'gate')},
                     'rejection_reason': 'accepted' if i == 0 else 'both', 'reasons': []}
                    for i, (pair, truth) in enumerate([('a', 'benefit'), ('b', 'harm')])],
        'row_count': 2, 'valid_row_count': 2, 'unknown_count': 0, 'omitted_rows': 0, 'reasons': [], **common}
    # Separate unfavorable original case: both heads accept the harmful row.
    unsafe_upstream = source('sources/unsafe-predictions.json', [[-1, -1, 0.9], [1, -1, 0.9]])
    unsafe_rows = [dict(row, predicted_gain=-1, p_better=0.9, source_sha256=unsafe_upstream) for row in rows]
    unsafe_sha = source('sources/unsafe-decisions.json', {'rows': unsafe_rows, 'parents': []})
    unsafe_raw = sources[-1]['text']
    unsafe_identity = dict(identity, run_id='unsafe-decisions', source_path='sources/unsafe-decisions.json',
                           source_sha256=unsafe_sha)
    requirements = {'head': 'gate', 'min_benefit_support': 1, 'min_harm_support': 1,
                    'min_benefit_acceptance': 1, 'max_harm_acceptance': 0}
    requirements_expected = {'operation': 'evaluate_decision_requirements', 'status': 'FAIL', 'met': False,
        'reason': 'REQUIREMENTS_NOT_MET', 'identity': unsafe_identity, 'requirements': requirements,
        'checks': {'min_benefit_support': {'status': 'PASS', 'observed': 1, 'required': 1},
                   'min_harm_support': {'status': 'PASS', 'observed': 1, 'required': 1},
                   'min_benefit_acceptance': {'status': 'PASS', 'observed': 1.0, 'required': 1},
                   'max_harm_acceptance': {'status': 'FAIL', 'observed': 1.0, 'required': 0}},
        'diagnostic_status': 'OBSERVED', 'diagnostic_reasons': [],
        'assurance': 'FINITE_SAMPLE_REQUIREMENTS', **common}
    specs = [
        {'name': 'paired', 'entry': 'compare_paired_metrics', 'args': [candidate, baseline, sampling],
         'expected': paired_expected, 'predicates': [{'field': 'status', 'op': 'eq', 'value': 'COMPARABLE'},
                                                   {'field': 'mean_improvement', 'op': 'eq', 'value': 2}]},
        {'name': 'residual', 'entry': 'check_residuals', 'args': [candidate, reference, domain],
         'expected': residual_expected, 'predicates': [{'field': 'status', 'op': 'eq', 'value': 'OBSERVED'},
                                                     {'field': 'within_tolerance', 'op': 'eq', 'value': True}]},
        {'name': 'diagnostic', 'entry': 'diagnose_decisions', 'args': [raw, identity, semantics],
         'expected': diagnostic_expected, 'predicates': [{'field': 'status', 'op': 'eq', 'value': 'OBSERVED'},
             {'field': 'heads/gate/acceptance/harm/rate', 'fact': 'harm_acceptance', 'op': 'eq', 'value': 0}]},
        {'name': 'requirements', 'entry': 'evaluate_decision_requirements',
         'args': [unsafe_raw, unsafe_identity, semantics, requirements], 'expected': requirements_expected,
         'predicates': [{'field': 'status', 'op': 'eq', 'value': 'FAIL'},
                        {'field': 'met', 'op': 'eq', 'value': False}]}]
    for spec in specs:
        spec['sources'] = sources
    return specs


if __name__ == '__main__':
    print(json.dumps(specifications(), indent=2, allow_nan=False))
