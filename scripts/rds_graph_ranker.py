"""Frozen, bounded neural preferences over an already admitted route frontier.

No training, truth promotion, execution, external model loading or new state.
Weights may come from an offline trainer; their quality is never self-certified.
"""
from copy import deepcopy
import math
import time

from rds_project import digest, require
from rds_advisor_search import evaluate_condition

FEATURES = ['action', 'true', 'false', 'unknown', 'observed']
MAX_NODES, MAX_EDGES, MAX_WIDTH, MAX_LAYERS = 512, 2048, 16, 2
ASSURANCE = 'NEURAL_PREFERENCE_NOT_TRUTH_OR_SCIENTIFIC_VALUE'


def _scalar(value):
    require(type(value) in (int, float) and abs(value) <= 1e6 and math.isfinite(value),
            'Graph ranker weights must be finite numeric scalars within +/-1e6')


def _vector(value, width):
    require(isinstance(value, list) and len(value) == width, 'Graph ranker vector dimension mismatch')
    for item in value:
        _scalar(item)


def _matrix(value, rows, columns):
    require(isinstance(value, list) and len(value) == rows, 'Graph ranker matrix dimension mismatch')
    for row in value:
        _vector(row, columns)


def validate(config):
    require(isinstance(config, dict) and set(config) == {'schema', 'mode', 'model'},
            'graph_ranker needs schema, mode and model only')
    require(type(config['schema']) is int and config['schema'] == 1, 'Graph ranker schema must be 1')
    require(config['mode'] in ('shadow', 'tie_break'), 'Graph ranker mode must be shadow or tie_break')
    model = config['model']
    require(isinstance(model, dict) and set(model) == {
        'features', 'origin', 'input_weights', 'input_bias', 'layers', 'readout', 'readout_bias'},
        'Graph ranker model fields mismatch')
    require(model['features'] == FEATURES, 'Graph ranker feature order mismatch')
    require(isinstance(model['origin'], str) and 1 <= len(model['origin']) <= 512,
            'Graph ranker needs a bounded origin locator; it is unverified metadata')
    require(isinstance(model['input_bias'], list) and 1 <= len(model['input_bias']) <= MAX_WIDTH,
            'Graph ranker hidden width must be 1..16')
    width = len(model['input_bias'])
    _vector(model['input_bias'], width)
    _matrix(model['input_weights'], width, len(FEATURES))
    require(isinstance(model['layers'], list) and 1 <= len(model['layers']) <= MAX_LAYERS,
            'Graph ranker needs 1..2 message-passing layers')
    for layer in model['layers']:
        require(isinstance(layer, dict) and set(layer) == {'self', 'message', 'bias'},
                'Graph ranker layer needs self, message and bias only')
        _matrix(layer['self'], width, width)
        _matrix(layer['message'], width, width)
        _vector(layer['bias'], width)
    _vector(model['readout'], width)
    _scalar(model['readout_bias'])
    return config


def _trace(graph, facts):
    """Predicates retain three-valued truth and provenance from the existing reader."""
    nodes, edges, actions = {}, set(), {}
    raw_nodes, raw_edges = graph.get('nodes'), graph.get('edges')
    require(isinstance(raw_nodes, list) and len(raw_nodes) <= 128 and
            isinstance(raw_edges, list) and len(raw_edges) <= 512, 'Graph ranker input graph limit exceeded')
    for node in raw_nodes:
        require(isinstance(node, dict), 'Graph ranker node must be an object')
        ident = node.get('id')
        require(isinstance(ident, str) and ident and ident not in nodes, 'Graph ranker node identity mismatch')
        config = node.get('executable', {})
        action = config.get('action') or {}
        nodes[ident] = {'id': ident, 'kind': 'rule', 'features': [int(bool(action)), 0, 0, 0, 0]}
        if action:
            aid = action.get('id')
            require(isinstance(aid, str) and aid not in actions, 'Graph ranker action identity mismatch')
            actions[aid] = ident
    # Only the relation that existing direction search traverses affects messages.
    for edge in raw_edges:
        if isinstance(edge, dict) and edge.get('relation') == 'prerequisite_for':
            require(edge.get('from') in nodes and edge.get('to') in nodes,
                    'Graph ranker prerequisite endpoint is missing')
            edges.add((edge['from'], edge['to'], 'prerequisite_for'))
    for node in raw_nodes:
        config = node.get('executable', {})
        for kind in ('preconditions', 'satisfied_when'):
            conditions = config.get(kind, [])
            require(isinstance(conditions, list), 'Graph ranker conditions must be a list')
            for condition in conditions:
                require(isinstance(condition, dict), 'Graph ranker condition must be an object')
                # Shared identical predicates become one node; on_false code is not a feature.
                predicate = {k: deepcopy(v) for k, v in condition.items() if k in ('fact', 'op', 'value')}
                report = evaluate_condition(predicate, facts)
                ident = 'predicate:' + digest(predicate)
                require(ident not in nodes or nodes[ident].get('kind') == 'predicate',
                        'Graph ranker predicate identity collides with a rule')
                truth = report['truth']
                nodes[ident] = {'id': ident, 'kind': 'predicate', 'predicate': predicate, 'evidence': report,
                               'features': [0, int(truth == 'TRUE'), int(truth == 'FALSE'),
                                            int(truth == 'UNKNOWN'), int(report['evidence_status'] in
                                            ('ARTIFACT_OBSERVED', 'PROGRAM_DERIVED'))]}
                edges.add((ident, node['id'], kind))
                require(len(nodes) <= MAX_NODES and len(edges) <= MAX_EDGES,
                        'Graph ranker feature graph limit exceeded')
    return {'feature_names': FEATURES, 'nodes': sorted(nodes.values(), key=lambda n: n['id']),
            'edges': [{'from': a, 'to': b, 'relation': r} for a, b, r in sorted(edges)],
            'action_nodes': dict(sorted(actions.items()))}


def _affine(weights, vector, bias):
    return [math.tanh(math.fsum(w * x for w, x in zip(row, vector)) + b)
            for row, b in zip(weights, bias)]


def rank(config, graph, facts, candidates, *, precedence=None):
    """Score, then optionally reorder this exact pool. Caller owns all admission gates."""
    started = time.perf_counter()
    report = {'schema': 1, 'status': 'ABSTAINED', 'mode': config.get('mode'),
              'assurance': ASSURANCE, 'training_status': 'UNVERIFIED',
              'research_policy_gain_measured': False, 'authorization': 'UNCHANGED',
              'model_sha256': None, 'scope': [r['candidate'] for r in candidates],
              'selection_applied': False, 'precedence': precedence, 'scores': []}
    ordered = list(candidates)
    try:
        validate(config)
        report['model_sha256'] = digest(config)
        trace = _trace(graph, facts)
        report.update(trace=trace, input_sha256=digest({'trace': trace, 'scope': report['scope'],
                                                      'precedence': precedence}))
        model = config['model']
        width = len(model['input_bias'])
        hidden = {n['id']: _affine(model['input_weights'], n['features'], model['input_bias'])
                  for n in trace['nodes']}
        parents = {n: set() for n in hidden}
        for edge in trace['edges']:
            parents[edge['to']].add(edge['from'])
        for layer in model['layers']:
            updated = {}
            for ident, values in hidden.items():
                neighbours = sorted(parents[ident])
                mean = [math.fsum(hidden[p][i] for p in neighbours) / len(neighbours)
                        if neighbours else 0.0 for i in range(width)]
                updated[ident] = [math.tanh(math.fsum(w * x for w, x in zip(row, values)) +
                                          math.fsum(w * x for w, x in zip(message, mean)) + b)
                                  for row, message, b in zip(layer['self'], layer['message'], layer['bias'])]
            hidden = updated
        scores = {}
        for candidate in report['scope']:
            require(candidate in trace['action_nodes'], 'Graph ranker candidate has no unique action node')
            values = hidden[trace['action_nodes'][candidate]]
            score = math.fsum(w * x for w, x in zip(model['readout'], values)) + model['readout_bias']
            require(math.isfinite(score), 'Graph ranker inference is nonfinite')
            scores[candidate] = score
            report['scores'].append({'candidate': candidate, 'score': score})
        report.update(status='SCORED', preferred=sorted(report['scope'], key=lambda c: -scores[c]))
        if config['mode'] == 'tie_break' and not precedence:
            # Stable sort preserves declaration order for equal neural scores.
            ordered.sort(key=lambda r: -scores[r['candidate']])
            report['selection_applied'] = [r['candidate'] for r in ordered] != report['scope']
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        report['reason'] = str(exc)
    report['inference_wall_seconds'] = time.perf_counter() - started
    report['cpu_seconds'] = None
    return ordered, report
