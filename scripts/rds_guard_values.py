"""Pure bounded number and negative-domain predicates; no IO or policy calls."""
from fractions import Fraction
import re

from rds_project_values import require

MAX_BYTES = 2 * 1024 * 1024


def exact(value):
    require(not isinstance(value, bool) and isinstance(value, (str, int, float)), 'Expected an exact finite number')
    text = str(value)
    require(len(text) <= 180 and re.fullmatch(r'-?\d{1,80}(?:/\d{1,80}|\.\d{0,80})?(?:[eE][+-]?\d{1,3})?', text), 'Expected a bounded integer, rational or decimal')
    try:
        result = Fraction(text)
    except ZeroDivisionError as exc:
        raise ValueError('Exact guard denominator must be nonzero') from exc
    require(result.numerator.bit_length() <= 4096 and result.denominator.bit_length() <= 4096, 'Number exceeds exact guard bounds')
    return result


def validate_domain(domain, candidate):
    require(isinstance(domain, dict) and set(domain) == {'parameters', 'justification'}, 'Domain needs parameters and an explicit universal-range justification')
    params = domain['parameters']
    require(isinstance(params, dict) and 1 <= len(params) <= 8, 'Domain needs 1–8 explicit parameter predicates')
    require(isinstance(domain['justification'], str) and 0 < len(domain['justification'].strip()) <= 1024, 'Explain why the witness applies throughout the declared domain')
    original = candidate.get('action', {}).get('parameters', {})
    require(isinstance(original, dict) and set(params) <= set(original), 'Domain parameters must exist in the rejected action')
    for name, bounds in params.items():
        require(isinstance(name, str) and isinstance(bounds, dict) and bounds and set(bounds) <= {'min', 'max', 'eq'}, 'Use explicit min/max/eq predicates')
        require(not ('eq' in bounds and len(bounds) != 1), 'Equality cannot be combined with interval bounds')
        values = {key: exact(value) for key, value in bounds.items()}
        require(not ('min' in values and 'max' in values) or values['min'] <= values['max'], 'Domain interval is reversed')
    require(in_domain(params, original), 'The recorded witness parameter must lie in its declared domain')
    return domain


def in_domain(predicates, parameters):
    if not isinstance(parameters, dict) or not set(predicates) <= set(parameters):
        return False
    try:
        for name, bounds in predicates.items():
            value = exact(parameters[name])
            if any((key == 'min' and value < exact(bound)) or (key == 'max' and value > exact(bound)) or
                   (key == 'eq' and value != exact(bound)) for key, bound in bounds.items()):
                return False
        return True
    except (ValueError, TypeError, ZeroDivisionError):
        return False
