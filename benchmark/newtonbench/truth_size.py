"""Size every devset truth law, and ask what max_nodes would have to be.

The question this answers: `max_nodes` is a hard structural ceiling. An equation
whose smallest faithful expression exceeds it cannot be found at any budget, so the
search that runs anyway is pure cost. This script sizes each law and reports the
coverage curve over candidate ceilings.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import rds_compass_model as model
import rds_compass_declaration as declaration
import devset

# devset_run.py never declares `synthesis`, so every cell inherits this default.
DECLARED = declaration.DEFAULTS['synthesis']['max_nodes']


def translate(expression):
    """Rewrite devset's call spelling into the model dialect's operator nodes.

    devset writes `pow(a, b)` and `neg(a)`; the model's call whitelist has neither.
    Everything else passes through unchanged.
    """
    out = expression.replace('pow(', '_POW_(').replace('neg(', '_NEG_(')
    return out


def size_of(expression):
    try:
        node = model.parse(expression, None)
        return model.size(node), None
    except Exception as error:  # noqa: BLE001 - report every parse failure, never guess
        return None, '%s: %s' % (type(error).__name__, error)


def main():
    names = devset.list_equations()
    rows = []
    for name in names:
        plan = devset.load(name)
        truth = plan['truth']
        law = truth.get('law')
        expression = truth.get('expression')
        size, error = size_of(law) if law else (None, 'no law')
        declared = DECLARED
        rows.append({'equation': name, 'law': law, 'expression': expression,
                     'size': size, 'error': error, 'declared_max_nodes': declared})
    sized = [r for r in rows if r['size'] is not None]
    print('parsed %d/%d' % (len(sized), len(rows)))
    for r in rows:
        if r['size'] is None:
            print('  UNPARSED %-28s %s | %s' % (r['equation'], r['law'], r['error']))
    print()
    print('%-30s %5s %6s' % ('equation', 'size', 'max_n'))
    for r in sorted(sized, key=lambda x: -x['size']):
        mark = '' if r['size'] <= r['declared_max_nodes'] else '  <-- STRUCTURALLY UNREACHABLE'
        print('%-30s %5d %6d%s' % (r['equation'], r['size'], r['declared_max_nodes'], mark))
    print()
    print('coverage curve (fraction of laws an enumeration at that ceiling can express):')
    for ceiling in (7, 8, 10, 12, 16, 20):
        covered = [r for r in sized if r['size'] <= ceiling]
        print('  max_nodes=%-3d %2d/%d = %.1f%%' % (ceiling, len(covered), len(sized),
                                                    100.0 * len(covered) / len(sized)))
    declared = sorted({r['declared_max_nodes'] for r in rows})
    print()
    print('declared max_nodes values across the devset: %s' % declared)
    with open(str(pathlib.Path(__file__).resolve().parent / 'results' / 'truth-size.json'), 'w') as handle:
        json.dump(rows, handle, indent=1)
    print('wrote truth_size.json')


if __name__ == '__main__':
    main()
