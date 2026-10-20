"""Probe: is the I.6.20a grammar declaration why that equation never lands?

The declared operators are ['neg','sub','div','pow','exp','sqrt','pi'] but the
truth law `exp(-theta**2/2)/sqrt(2*pi)` needs `mul` (for `2*pi`) and never uses
`sub` (`-theta**2` is `neg(pow(theta, 2))`).  Operators are the synthesis
candidate pool, so a missing `mul` means the truth is outside the search space.

This probe runs the same equation twice -- as declared, then with `mul` added --
and reports the outcome of each.  It does not modify devset.py and it does not
write into results/.  Pre-registered expectation: if the declaration is the
cause, the fixed arm reaches GOAL_IDENTIFIED while the declared arm does not.

Usage: python -B benchmark/newtonbench/probe_i620.py <budget> <out.json>
"""

import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / 'scripts'))

import devset  # noqa: E402
import devset_run  # noqa: E402

NAME = 'feynman-I.6.20a'
FIXED_OPERATORS = ['neg', 'mul', 'div', 'pow', 'exp', 'sqrt', 'pi']


def with_operators(operators):
    """Wrap the registry entry so devset.load(name) returns a patched spec."""
    original = devset.REGISTRY[NAME]

    def patched():
        spec = dict(original())
        spec['grammar'] = {**spec['grammar'], 'operators': list(operators)}
        return spec

    return patched


def run(label, operators, budget, base):
    devset.REGISTRY[NAME] = with_operators(operators)
    started = time.monotonic()
    try:
        row = devset_run.run_one(NAME, budget, base / label)
        error = None
    except Exception as exc:  # a failed declaration must not hide the other arm
        row = {'status': None, 'claim_exact': None, 'experiments': None}
        error = f'{type(exc).__name__}: {exc}'
    elapsed = round(time.monotonic() - started, 1)
    recorded = dict(row)
    recorded.update({
        'arm': label,
        'operators': list(operators),
        'seconds': elapsed,
    })
    if error:
        recorded['error'] = error
    return recorded


def main(budget, out_path):
    base = Path(tempfile.mkdtemp(prefix='probe-i620-'))
    declared = devset.load(NAME)['grammar']['operators']
    result = {
        'schema': 1,
        'equation': NAME,
        'budget': budget,
        'probe': 'i620_grammar',
        'arms': [
            run('declared', declared, budget, base),
            run('fixed', FIXED_OPERATORS, budget, base),
        ],
        'campaign_root': str(base),
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(result, indent=1) + '\n', encoding='utf-8')
    for arm in result['arms']:
        print(json.dumps({k: arm.get(k) for k in
                          ('arm', 'status', 'claim_exact', 'experiments', 'seconds', 'error')}),
              flush=True)
    print('wrote ' + str(out_path))


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8,
         sys.argv[2] if len(sys.argv) > 2 else 'results/probe-i620.json')
