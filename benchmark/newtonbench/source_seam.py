"""Reproduce the preregistered three-arm source-seam probe.

Run from the repository root:
    python -B benchmark/newtonbench/source_seam.py benchmark/newtonbench/results/source-seam-20261021.json
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import devset  # noqa: E402
import devset_run  # noqa: E402
import rds_compass  # noqa: E402
from rds_project import ProjectStore  # noqa: E402

EQUATION = 'feynman-I.41.16'
BUDGET = 8


def source_origins_from_state(state):
    """Keep source provenance even when a later jump prefixes its own origin."""
    origins = sorted({str(h.get('origin')) for h in state['hypotheses'].values() if h.get('origin')})
    return [origin for origin in origins if ':SOURCE:' in origin]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new or replaceable JSON output path')
    parser.add_argument('--variant', action='append',
                        choices=('no-source', 'truth-source', 'wrong-source'),
                        help='run one variant; repeat to select several (default: all)')
    args = parser.parse_args()

    truth = devset.load(EQUATION)['truth']['law']
    needle = '2.99792458e8**2'
    if needle not in truth:
        raise SystemExit('pre-registered wrong-source transform no longer matches truth law')
    wrong = truth.replace(needle, '1')
    cases = (
        ('no-source', ()),
        ('truth-source', ({'id': 'preregistered-truth', 'laws': [truth]},)),
        ('wrong-source', ({'id': 'preregistered-wrong', 'laws': [wrong]},)),
    )
    selected = set(args.variant or ('no-source', 'truth-source', 'wrong-source'))
    rows = []
    failed = False
    for variant, sources in cases:
        if variant not in selected:
            continue
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='rds-source-seam-') as temporary:
            try:
                row = devset_run.run_one(EQUATION, BUDGET, Path(temporary), sources=sources)
                row.pop('campaign_root', None)
                state = rds_compass.fold(ProjectStore(Path(temporary) / EQUATION / f'B{BUDGET}'))
                row['hypothesis_origins'] = sorted({
                    str(h.get('origin')) for h in state['hypotheses'].values() if h.get('origin')
                })
                row['source_origins'] = source_origins_from_state(state)
            except Exception as exc:
                failed = True
                row = {'equation': EQUATION, 'B': BUDGET,
                       'error': f'{type(exc).__name__}: {exc}'}
        row['variant'] = variant
        row['wall_seconds'] = round(time.perf_counter() - started, 3)
        rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema': 1,
        'equation': EQUATION,
        'budget': BUDGET,
        'measurement': 'RDS execution with precomputed source expressions; external generation cost is UNKNOWN',
        'rows': rows,
    }
    args.output.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(args.output)
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
