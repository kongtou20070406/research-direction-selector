"""Size each truth law again, but through the derived pool a fixed pool would give.

The pool in `_fits` today only forms `left + right` and `left * right`. A law whose
irreducible intermediate is a difference or a quotient cannot be rewritten through it,
so its size never falls. This script asks what the ceiling would have to be if the pool
also carried subtraction and division.

Rewriting rule, deliberately as narrow as the real pool: a derived name stands for
`left <op> right` where both sides are declared variables, so only a two-leaf subtree
can be replaced, and only by one node. Greedy, largest subtree first.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / 'scripts'))
sys.path.insert(0, str(HERE.parent))

import rds_compass_model as model
import rds_compass_declaration as declaration
import devset

DECLARED = declaration.DEFAULTS['synthesis']['max_nodes']
BINARY = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/'}
TODAY = {'add', 'mul'}


def variables_of(node):
    found = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        if 'var' in current:
            found.add(current['var'])
        stack.extend(current.get('args') or [])
    return found


def rewrite(node, pool_ops, names, cache):
    """Replace every two-leaf subtree whose operator is in the pool; return the new size."""
    key = (model.render(node), tuple(sorted(pool_ops)))
    if key in cache:
        return cache[key]
    if 'var' in node or 'const' in node or 'pi' in node:
        cache[key] = 1
        return 1
    args = node.get('args') or []
    if len(args) != 2:
        # Unary operators keep their operand; nothing in the pool can absorb them.
        size = 1 + sum(rewrite(a, pool_ops, names, cache) for a in args)
        cache[key] = size
        return size
    left, right = args
    if node.get('op') in pool_ops:
        leaves = variables_of(left) | variables_of(right)
        if len(leaves) == 2 and model.size(left) == 1 and model.size(right) == 1:
            cache[key] = 1
            return 1
    size = 1 + rewrite(left, pool_ops, names, cache) + rewrite(right, pool_ops, names, cache)
    cache[key] = size
    return size


def main():
    rows = []
    for name in devset.list_equations():
        plan = devset.load(name)
        law = plan['truth'].get('law')
        if not law:
            continue
        try:
            node = model.parse(law, None)
        except Exception as error:  # noqa: BLE001
            rows.append({'equation': name, 'law': law, 'raw': None,
                         'error': '%s: %s' % (type(error).__name__, error)})
            continue
        names = sorted(variables_of(node))
        raw = model.size(node)
        today = rewrite(node, TODAY, names, {})
        with_sub = rewrite(node, set(BINARY), names, {})
        rows.append({'equation': name, 'law': law, 'variables': len(names),
                     'raw': raw, 'today_pool': today, 'with_sub_div': with_sub})
    sized = [r for r in rows if r.get('raw') is not None]
    print('%-30s %4s %6s %6s %6s' % ('equation', 'vars', 'raw', 'today', 'w/sub'))
    for r in sorted(sized, key=lambda x: -x['with_sub_div']):
        saved = r['raw'] - r['with_sub_div']
        note = '  saves %d' % saved if saved else ''
        print('%-30s %4d %6d %6d %6d%s'
              % (r['equation'], r['variables'], r['raw'], r['today_pool'], r['with_sub_div'], note))
    print()
    print('coverage over the laws, by ceiling:')
    for ceiling in (7, 8, 9, 10, 12, 16):
        today = sum(1 for r in sized if r['today_pool'] <= ceiling)
        better = sum(1 for r in sized if r['with_sub_div'] <= ceiling)
        print('  max_nodes=%-3d today %2d/%d  with sub/div %2d/%d'
              % (ceiling, today, len(sized), better, len(sized)))
    out = HERE.parent / 'results' / 'truth-size-derived.json'
    out.write_text(json.dumps(rows, indent=1), encoding='utf-8')
    print('wrote %s' % out.relative_to(HERE.parents[3]))


if __name__ == '__main__':
    main()
