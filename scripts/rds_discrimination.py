"""Bounded, declared rival predicates over original experiment measurements.

This checks executable discriminability in one frozen scope, not natural-language
equivalence, causality, hypothesis novelty or scientific truth.
"""
from copy import deepcopy
from fractions import Fraction
import math
import re

from rds_project import require, digest
from rds_artifacts import _pointer
from rds_advisor_search import evaluate_condition


def _number(value):
    return (type(value) is int and value.bit_length() <= 1024 or
            type(value) is float and math.isfinite(value))


def _predicate(value):
    require(isinstance(value, dict) and set(value) == {'op', 'value'}, 'Prediction requires op/value')
    op, expected = value['op'], value['value']
    require(isinstance(op, str) and op in {'eq', 'lt', 'lte', 'gt', 'gte'} and
            (_number(expected) or type(expected) is bool and op == 'eq'),
            'Use finite scalar predictions; booleans support eq only')
    return value


def _interval(pred):
    op, v = pred['op'], pred['value']
    return {'eq': (v, True, v, True), 'lt': (-math.inf, False, v, False),
            'lte': (-math.inf, False, v, True), 'gt': (v, False, math.inf, False),
            'gte': (v, True, math.inf, False)}[op]


def validate(value, contract=None, experiment=None):
    require(isinstance(value, dict) and set(value) ==
            {'schema', 'hypothesis_id', 'conditions', 'measurement', 'proposal', 'rival'}
            and type(value['schema']) is int and value['schema'] == 1,
            'New structure experiments require an executable discriminator schema 1')
    ident = value['hypothesis_id']
    require(isinstance(ident, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', ident),
            'Invalid discriminator hypothesis ID')
    conditions = value['conditions']
    require(isinstance(conditions, list) and 1 <= len(conditions) <= 16, 'Bind frozen experimental conditions')
    seen = set()
    for item in conditions:
        require(isinstance(item, dict) and set(item) == {'path', 'sha256'} and
                isinstance(item['path'], str) and item['path'] not in seen and
                isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']),
                'Invalid or duplicate condition binding')
        seen.add(item['path'])
    measure = value['measurement']
    require(isinstance(measure, dict) and set(measure) == {'name', 'path', 'pointer'} and
            all(isinstance(measure[k], str) and measure[k] for k in measure) and
            len(measure['name']) <= 128 and len(measure['pointer']) <= 2048,
            'Declare named original measurement and JSON pointer')
    # Validate the pointer spelling/depth independently of whether a value exists.
    segments = measure['pointer'].split('/')
    require(measure['pointer'].startswith('/') and len(segments) <= 33 and
            all(p[:1] in ('0', '1') for s in segments for p in s.split('~')[1:]), 'Invalid measurement pointer')
    a, b = _predicate(value['proposal']), _predicate(value['rival'])
    require((type(a['value']) is bool) == (type(b['value']) is bool), 'Rival prediction types differ')
    if type(a['value']) is bool:
        separate = a['value'] is not b['value']
    else:
        al, alc, au, auc = _interval(a)
        bl, blc, bu, buc = _interval(b)
        lower, upper = max(al, bl), min(au, bu)
        separate = lower > upper or lower == upper and not (
            (lower != al or alc) and (lower != bl or blc) and
            (upper != au or auc) and (upper != bu or buc))
    require(separate, 'DESIGN_DISCRIMINATOR: rival predictions overlap or are equivalent')
    if contract is not None:
        bindings = {r['path']: r for r in contract['bindings']}
        require(all(r['path'] in bindings and bindings[r['path']]['role'] in {'data', 'config'} and
                    bindings[r['path']]['sha256'] == r['sha256'] for r in conditions),
                'Discriminator conditions must match frozen data/config bindings')
        # A subset could hide a changed experimental condition behind the same hypothesis.
        required = {p for p, r in bindings.items() if r['role'] in {'data', 'config'}}
        require(seen == required, 'Bind every frozen data/config condition')
    if experiment is not None:
        require(measure['path'] == experiment['verdict_output'], 'Measurement must come from the independent verifier output')
    return deepcopy(value)


def hypothesis_key(value):
    """Explicit hypothesis identity in the same conditions and measurement scope.

    Output filenames change between attempts; they do not change the condition.
    IDs/name still are declared identity, not universal semantic alias detection.
    """
    pred = deepcopy(value['proposal'])
    if _number(pred['value']):
        fraction = Fraction(pred['value'])
        pred['value'] = [fraction.numerator, fraction.denominator]
    return digest({'hypothesis_id': value['hypothesis_id'],
                   'conditions': sorted(value['conditions'], key=lambda r: r['path']),
                   'measurement': {k: value['measurement'][k] for k in ('name', 'pointer')}, 'prediction': pred})


def observe(value, document, output_sha256):
    if value is None:
        return {'observation': 'UNKNOWN', 'reason': 'LEGACY_UNBOUND_DISCRIMINATION', 'measured': None}
    measure = value['measurement']
    try:
        actual = _pointer(document, measure['pointer'])
    except (KeyError, IndexError, TypeError, ValueError):
        return {'observation': 'UNKNOWN', 'reason': 'MEASUREMENT_MISSING', 'measured': None}
    boolean = type(value['proposal']['value']) is bool
    if (boolean and type(actual) is not bool) or (not boolean and not _number(actual)):
        return {'observation': 'UNKNOWN', 'reason': 'MEASUREMENT_TYPE_OR_VALUE_UNSUPPORTED', 'measured': None}
    source = {'path': measure['path'], 'sha256': output_sha256, 'locator': 'pointer:' + measure['pointer']}
    checks = {key: evaluate_condition({'fact': 'measurement', **value[key]},
              {'measurement': {'value': actual, 'source': source, 'reliable': True}}) for key in ('proposal', 'rival')}
    truths = (checks['proposal']['truth'], checks['rival']['truth'])
    observed = {('TRUE', 'FALSE'): 'SUPPORT', ('FALSE', 'TRUE'): 'REFUTE'}.get(truths, 'UNKNOWN')
    return {'observation': observed, 'reason': None if observed != 'UNKNOWN' else 'NEITHER_RIVAL_PREDICTION_MATCHES',
            'measured': {'name': measure['name'], 'value': actual, **source}, 'predicates': checks,
            'hypothesis_key': hypothesis_key(value), 'hypothesis_id': value['hypothesis_id'],
            'conditions': deepcopy(value['conditions'])}
