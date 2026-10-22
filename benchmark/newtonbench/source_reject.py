"""Measure whether a wrong law handed to the kernel can pass its judgement.

Protocol: `benchmark/newtonbench/source-reject-protocol.md`. The substitutions
below are transcribed from that document; nothing here chooses a law after
seeing a number.

    python -B benchmark/newtonbench/source_reject.py --verify-only
    python -B benchmark/newtonbench/source_reject.py results/source-reject-20261021.json
"""

import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(HERE))

import devset
import devset_run

BUDGET = 8

# Protocol section 1. Each entry is (text that must be present, replacement).
SUBSTITUTIONS = {
    'feynman-I.41.16': ('pi**2*2.99792458e8**2', 'pi*2.99792458e8**2'),
    'feynman-II.24.17': ('-pi**2/d**2', '+pi**2/d**2'),
    'feynman-I.10.7': ('m_0/sqrt(', 'm_0/('),
    'feynman-I.6.20a': ('/sqrt(2*pi)', '*sqrt(2*pi)'),
    'feynman-I.8.14': ('+(y2-y1)**2', '-(y2-y1)**2'),
    'feynman-II.34.11': ('/(2*m)', '/(2*m**2)'),
}

CARRIED = ('equation', 'variant', 'B', 'status', 'claim', 'law', 'counterexamples', 'claim_exact',
           'experiments', 'false_certification', 'certification_check', 'seconds', 'error')


def wrong_law(name):
    """Return (wrong, truth, refusal). refusal is None when the edit applied."""
    truth = devset.load(name)['truth']['law']
    old, new = SUBSTITUTIONS[name]
    if old not in truth:
        return None, truth, 'SUBSTITUTION_MISS'
    return truth.replace(old, new, 1), truth, None


def declared_counterexamples(expr, spec):
    try:
        return devset_run.counterexamples(expr, spec)
    except Exception as exc:                                    # noqa: BLE001 - report, never guess
        return f'{type(exc).__name__}: {exc}'


def verify():
    """Protocol section 1: a wrong law with no counterexample on the declared rows
    would not exercise the rejection path at all, so the cell is NOT_APPLICABLE."""
    rows = []
    for name in SUBSTITUTIONS:
        spec = devset.load(name)
        wrong, truth, refusal = wrong_law(name)
        row = {'equation': name, 'truth': truth, 'wrong': wrong, 'refusal': refusal,
               'declared_rows': len(spec['rows'])}
        if wrong is not None:
            row['wrong_counterexamples'] = declared_counterexamples(wrong, spec)
            row['truth_counterexamples'] = declared_counterexamples(truth, spec)
            row['verdict'] = ('APPLICABLE'
                              if isinstance(row['wrong_counterexamples'], int)
                              and row['wrong_counterexamples'] > 0
                              and row['truth_counterexamples'] == 0
                              else 'NOT_APPLICABLE')
        else:
            row['verdict'] = 'NOT_APPLICABLE'
        rows.append(row)
    return rows


def measure(name, variant, laws):
    spec = devset.load(name)
    wrong, truth, refusal = wrong_law(name)
    if refusal:
        return {'equation': name, 'variant': variant, 'refusal': refusal}
    base = Path(tempfile.mkdtemp(prefix='reject-' + variant + '-'))
    source = {'id': 'preregistered-wrong', 'laws': list(laws)}
    started = time.monotonic()
    try:
        row = devset_run.run_one(name, BUDGET, base, (source,))
    except Exception as exc:                                    # noqa: BLE001 - reported, not swallowed
        row = {'equation': name, 'B': BUDGET, 'error': f'{type(exc).__name__}: {exc}'}
    row['variant'] = variant
    row['wrong'] = wrong
    row['truth'] = truth
    row['declared_rows'] = len(spec['rows'])
    row['seconds'] = round(time.monotonic() - started, 1)
    return {key: row[key] for key in CARRIED if key in row}


def main(argv):
    if '--verify-only' in argv:
        rows = verify()
        print(json.dumps(rows, indent=1))
        return 0
    out = Path(argv[1]) if len(argv) > 1 else Path('source-reject.json')
    results = []
    for name in SUBSTITUTIONS:
        wrong, truth, refusal = wrong_law(name)
        if refusal:
            results.append({'equation': name, 'variant': 'wrong-only', 'refusal': refusal})
            results.append({'equation': name, 'variant': 'mixed', 'refusal': refusal})
            continue
        for variant, laws in (('wrong-only', (wrong,)), ('mixed', (wrong, truth))):
            row = measure(name, variant, laws)
            results.append(row)
            print(json.dumps(row), flush=True)
    out.write_text(json.dumps(results, indent=1))
    print(f'wrote {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
