"""Synthetic exact-integer square-root workload at a fixed decimal scale."""


def scaled_root(offset, precision):
    if precision < 1:
        raise ValueError('positive decimal scale required')
    scale = 10 ** precision + 1
    n = scale * scale + offset
    if n < 0:
        raise ValueError('negative radicand')
    if n == 0:
        return -scale
    root = 1 << ((n.bit_length() + 1) // 2)
    while True:
        revised = (root + n // root) // 2
        if revised >= root:
            return root - scale
        root = revised
