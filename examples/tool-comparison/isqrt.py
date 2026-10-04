"""Same synthetic operation using the standard-library integer-root routine."""
from math import isqrt


def scaled_root(offset, precision):
    if precision < 1:
        raise ValueError('positive decimal scale required')
    scale = 10 ** precision + 1
    return isqrt(scale * scale + offset) - scale
