"""Exact sparse quadratic QQ values and structural Jacobians.

Compile only the supplied polynomial structure. Each point uses a common
integer denominator; each output is reduced once. No point results are cached.
This finite arithmetic tool makes no root or disk-covering proof claim.
"""
from fractions import Fraction
from math import lcm


def _compile(data):
    """Scale each polynomial's coefficients to integers and differentiate."""
    n = len(data['variables'])
    compiled = []
    for polynomial in data['polynomials']:
        parsed = []
        denominator = 1
        seen = set()
        for term in polynomial['terms']:
            coefficient = Fraction(term['coefficient'])
            powers = tuple(tuple(pair) for pair in term['powers'])
            previous, degree, factors = -1, 0, []
            for index, exponent in powers:
                if (type(index) is not int or type(exponent) is not int
                        or not previous < index < n or not 1 <= exponent <= 2):
                    raise ValueError('Unsupported quadratic power')
                previous = index
                degree += exponent
                factors.extend([index] * exponent)
            if degree > 2 or powers in seen or not coefficient:
                raise ValueError('Unsupported quadratic term')
            seen.add(powers)
            denominator = lcm(denominator, coefficient.denominator)
            parsed.append((coefficient, tuple(factors)))
        terms, derivatives = [], {}
        for coefficient, factors in parsed:
            weight = coefficient.numerator * (denominator // coefficient.denominator)
            terms.append((weight, factors))
            # Two equal factors produce one structural derivative with weight 2.
            for variable in sorted(set(factors)):
                reduced = list(factors)
                reduced.remove(variable)
                derivatives.setdefault(variable, []).append(
                    (weight * factors.count(variable), tuple(reduced)))
        compiled.append((denominator, terms, sorted(derivatives.items())))
    return compiled


def evaluate(data):
    """Return all QQ values and all structural derivative entries per point."""
    if (data.get('schema') != 1 or not 1 <= len(data['variables']) <= 128
            or not 1 <= len(data['polynomials']) <= 128
            or not 1 <= len(data['points']) <= 8):
        raise ValueError('Unsupported finite quadratic data')
    compiled = _compile(data)
    values, jacobian = [], []
    for point in data['points']:
        coordinates = [Fraction(value) for value in point['coordinates']]
        if len(coordinates) != len(data['variables']):
            raise ValueError('Point dimension differs')
        denominator = lcm(*(value.denominator for value in coordinates))
        integers = [value.numerator * (denominator // value.denominator)
                    for value in coordinates]
        square = denominator * denominator
        row, derivative_row = [], []
        for pi, (coefficient_denominator, terms, derivatives) in enumerate(compiled):
            numerator = 0
            for weight, factors in terms:
                if not factors:
                    numerator += weight * square
                elif len(factors) == 1:
                    numerator += weight * integers[factors[0]] * denominator
                else:
                    numerator += weight * integers[factors[0]] * integers[factors[1]]
            row.append(str(Fraction(numerator, coefficient_denominator * square)))
            for variable, terms in derivatives:
                numerator = sum(weight * (integers[factors[0]] if factors else denominator)
                                for weight, factors in terms)
                derivative_row.append([pi, variable, str(Fraction(
                    numerator, coefficient_denominator * denominator))])
        values.append(row)
        jacobian.append(derivative_row)
    return {'values': values, 'jacobian': jacobian}
