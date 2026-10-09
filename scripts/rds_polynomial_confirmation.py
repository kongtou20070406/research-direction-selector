"""Finite exact QQ evaluation; no root, covering or optimality certificate.

Only frozen sparse quadratic data is interpreted. Candidate code is never
imported; rational strings are parsed with Fraction, never eval.
"""
from fractions import Fraction
import json
import re

MAX_BYTES = 1024 * 1024
MAX_INPUT_BITS = 1024
MAX_RESULT_BITS = 8192
MAX_TERMS = 16384
KIND = 'polynomial_rational_evaluation'


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _bytes(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                     allow_nan=False).encode('utf-8')
    _require(len(raw) <= MAX_BYTES, 'Polynomial JSON exceeds 1 MiB')


def _bounded(value, bits=MAX_RESULT_BITS):
    _require(abs(value.numerator).bit_length() <= bits and value.denominator.bit_length() <= bits,
             'Polynomial rational exceeds arithmetic bit bound')
    return value


def rational(value, *, bits=MAX_INPUT_BITS):
    _require(type(value) is str and len(value) <= 2 * (bits // 3 + 2) + 3 and
             re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:/[1-9][0-9]*)?', value) is not None,
             'Polynomial rational must be a canonical integer or reduced p/q string')
    result = _bounded(Fraction(value), bits)
    _require(str(result) == value, 'Polynomial rational is not canonical')
    return result


def _ids(values, name):
    _require(isinstance(values, list) and all(type(v) is str and 1 <= len(v) <= 128 for v in values)
             and len(set(values)) == len(values), 'Invalid or duplicate polynomial ' + name)


def _prepare(claim, data):
    _bytes(claim)
    _bytes(data)
    _require(isinstance(claim, dict) and set(claim) == {'schema', 'kind', 'quantities'} and
             type(claim['schema']) is int and claim['schema'] == 1 and claim['kind'] == KIND and
             claim['quantities'] in (['values'], ['values', 'sparse_jacobian']),
             'Invalid finite polynomial claim')
    _require(isinstance(data, dict) and set(data) == {'schema', 'variables', 'polynomials', 'points'} and
             type(data['schema']) is int and data['schema'] == 1, 'Invalid sparse polynomial data schema')
    variables = data['variables']
    _ids(variables, 'variables')
    _require(1 <= len(variables) <= 128, 'Polynomial variable bound exceeded')
    polys, points = data['polynomials'], data['points']
    _require(isinstance(polys, list) and 1 <= len(polys) <= 128 and
             isinstance(points, list) and 1 <= len(points) <= 8, 'Polynomial/point count bound exceeded')
    prepared, count = [], 0
    for poly in polys:
        _require(isinstance(poly, dict) and set(poly) == {'id', 'terms'} and
                 isinstance(poly['terms'], list), 'Invalid sparse polynomial')
        count += len(poly['terms'])
        _require(count <= MAX_TERMS, 'Polynomial sparse term bound exceeded')
        terms, seen = [], set()
        for term in poly['terms']:
            _require(isinstance(term, dict) and set(term) == {'coefficient', 'powers'} and
                     isinstance(term['powers'], list) and len(term['powers']) <= 2, 'Invalid sparse polynomial term')
            coefficient = rational(term['coefficient'])
            _require(coefficient != 0, 'Zero polynomial coefficient must be omitted')
            powers, previous, degree = [], -1, 0
            for pair in term['powers']:
                _require(isinstance(pair, list) and len(pair) == 2 and type(pair[0]) is int and
                         previous < pair[0] < len(variables) and type(pair[1]) is int and
                         1 <= pair[1] <= 2, 'Invalid, duplicate or unordered polynomial power')
                previous = pair[0]
                degree += pair[1]
                powers.append(tuple(pair))
            _require(degree <= 2 and tuple(powers) not in seen, 'Polynomial degree exceeds two or monomial is duplicated')
            seen.add(tuple(powers))
            terms.append((coefficient, tuple(powers)))
        prepared.append(terms)
    _ids([p['id'] for p in polys], 'IDs')
    coordinates = []
    for point in points:
        _require(isinstance(point, dict) and set(point) == {'id', 'coordinates'} and
                 isinstance(point['coordinates'], list) and len(point['coordinates']) == len(variables),
                 'Polynomial point does not cover all variables')
        coordinates.append([rational(v) for v in point['coordinates']])
    _ids([p['id'] for p in points], 'point IDs')
    return prepared, coordinates


def _value(terms, point):
    value = Fraction(0)
    for coefficient, powers in terms:
        term = coefficient
        for index, exponent in powers:
            term = _bounded(term * _bounded(point[index] ** exponent))
        value = _bounded(value + term)
    return str(value)


def evaluate(claim, data):
    """Recompute every frozen point, polynomial and declared derivative exactly."""
    polys, points = _prepare(claim, data)
    result = {'point_ids': [p['id'] for p in data['points']],
              'polynomial_ids': [p['id'] for p in data['polynomials']],
              'values': [[_value(poly, point) for poly in polys] for point in points]}
    if 'sparse_jacobian' in claim['quantities']:
        derivatives = []
        for pi, terms in enumerate(polys):
            for vi in sorted({i for _, powers in terms for i, _ in powers}):
                derived = []
                for coefficient, powers in terms:
                    exponent = next((e for i, e in powers if i == vi), 0)
                    if exponent:
                        reduced = tuple((i, e - 1 if i == vi else e) for i, e in powers
                                        if i != vi or e > 1)
                        derived.append((_bounded(coefficient * exponent), reduced))
                derivatives.append((pi, vi, derived))
        result['jacobian'] = [[[pi, vi, _value(terms, point)] for pi, vi, terms in derivatives]
                              for point in points]
    _bytes(result)
    return result


def check_output(claim, data, payload, inputs_sha256):
    """A well-formed wrong exact value is FAIL; malformed/unsupported data raises."""
    _bytes(payload)
    expected = evaluate(claim, data)
    _require(isinstance(payload, dict) and set(payload) == set(expected) | {'schema', 'inputs_sha256'} and
             type(payload['schema']) is int and payload['schema'] == 1 and
             payload['inputs_sha256'] == inputs_sha256 and
             payload['point_ids'] == expected['point_ids'] and
             payload['polynomial_ids'] == expected['polynomial_ids'], 'Polynomial output identity or schema differs')
    values = payload['values']
    _require(isinstance(values, list) and len(values) == len(expected['values']), 'Polynomial output point coverage differs')
    mismatch = None
    for p, row in enumerate(values):
        _require(isinstance(row, list) and len(row) == len(expected['polynomial_ids']), 'Polynomial output equation coverage differs')
        for i, value in enumerate(row):
            rational(value, bits=MAX_RESULT_BITS)
            if value != expected['values'][p][i] and mismatch is None:
                mismatch = {'quantity': 'values', 'point_id': expected['point_ids'][p],
                            'polynomial_id': expected['polynomial_ids'][i],
                            'expected': expected['values'][p][i], 'actual': value}
    if 'jacobian' in expected:
        jacobian = payload['jacobian']
        _require(isinstance(jacobian, list) and len(jacobian) == len(expected['jacobian']),
                 'Polynomial Jacobian point coverage differs')
        for p, row in enumerate(jacobian):
            _require(isinstance(row, list) and len(row) == len(expected['jacobian'][p]),
                     'Polynomial Jacobian structural coverage differs')
            for item, wanted in zip(row, expected['jacobian'][p]):
                _require(isinstance(item, list) and len(item) == 3 and type(item[0]) is int and
                         type(item[1]) is int and item[:2] == wanted[:2],
                         'Polynomial Jacobian coordinates or ordering differ')
                rational(item[2], bits=MAX_RESULT_BITS)
                if item[2] != wanted[2] and mismatch is None:
                    mismatch = {'quantity': 'sparse_jacobian', 'point_id': expected['point_ids'][p],
                                'polynomial_id': expected['polynomial_ids'][item[0]],
                                'variable': data['variables'][item[1]], 'expected': wanted[2], 'actual': item[2]}
    return {'status': 'FAIL' if mismatch else 'PASS', 'counterexample': mismatch,
            'point_count': len(expected['point_ids']), 'polynomial_count': len(expected['polynomial_ids']),
            'variable_count': len(data['variables']),
            'jacobian_pairs': len(expected.get('jacobian', [[]])[0])}
