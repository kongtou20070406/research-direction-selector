"""Measure the reach of external truth-source candidates on size > 7 equations.

Run from repository root:
    python -B benchmark/newtonbench/source_reach.py [output_path]

Preregistered protocol:
    benchmark/newtonbench/source-reach-protocol.md
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / 'scripts', ROOT / 'benchmark' / 'newtonbench'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import devset  # noqa: E402
import devset_run  # noqa: E402

EQUATIONS = (
    'feynman-I.41.16',
    'feynman-II.24.17',
    'feynman-III.10.19',
    'feynman-I.40.1',
    'feynman-I.10.7',
    'feynman-I.6.20a',
    'feynman-I.8.14',
    'feynman-I.18.4',
    'feynman-I.27.6',
    'feynman-II.34.11',
)

VARIANTS = ('no-source', 'truth-source')
BUDGET = 8
TIMEOUT_SECONDS = 900


def run_cell_worker(equation: str, variant: str) -> dict:
    """Execute one cell inside an isolated subprocess with independent temporary dir."""
    spec = devset.load(equation)
    if variant == 'no-source':
        sources = ()
    elif variant == 'truth-source':
        sources = ({'id': 'truth', 'laws': [spec['truth']['law']]},)
    else:
        raise ValueError(f'Unknown variant: {variant}')

    with tempfile.TemporaryDirectory(prefix=f'rds-reach-{equation}-{variant}-') as temporary:
        result = devset_run.run_one(equation, BUDGET, Path(temporary), sources=sources)
        return {
            'equation': equation,
            'variant': variant,
            'status': result.get('status'),
            'claim': result.get('claim'),
            'claim_exact': result.get('claim_exact'),
            'experiments': result.get('experiments'),
            'counterexamples': result.get('counterexamples'),
            'false_certification': result.get('false_certification'),
            'certification_check': result.get('certification_check'),
            'law': result.get('law'),
        }


def run_cell(equation: str, variant: str) -> dict:
    """Run worker cell in a child process guarded by a 900s timeout."""
    started = time.perf_counter()
    cmd = [
        sys.executable, '-B', str(Path(__file__).resolve()),
        '--run-cell', equation, variant,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=str(ROOT),
        )
        elapsed = time.perf_counter() - started
        if proc.returncode != 0:
            err_text = proc.stderr.strip()
            err_line = err_text.splitlines()[-1] if err_text else f'Worker exit code {proc.returncode}'
            return {
                'equation': equation,
                'variant': variant,
                'error': err_line,
                'wall_seconds': round(elapsed, 3),
            }
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                data = json.loads(line)
                data['wall_seconds'] = round(elapsed, 3)
                return data
        return {
            'equation': equation,
            'variant': variant,
            'error': f'No JSON emitted by worker. stdout: {proc.stdout[:200]}',
            'wall_seconds': round(elapsed, 3),
        }
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - started
        return {
            'equation': equation,
            'variant': variant,
            'error': 'TIMEOUT',
            'wall_seconds': round(elapsed, 3),
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            'equation': equation,
            'variant': variant,
            'error': f'{type(exc).__name__}: {exc}',
            'wall_seconds': round(elapsed, 3),
        }


def save_results(output_path: Path, rows: list) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema': 1,
        'budget': BUDGET,
        'equations': list(EQUATIONS),
        'measurement': 'RDS source-reach pre-registered probe (size > 7 equations, B=8); external source-generation cost is UNKNOWN',
        'rows': rows,
    }
    tmp_path = output_path.with_suffix('.tmp')
    tmp_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    tmp_path.replace(output_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=Path,
                        default=ROOT / 'benchmark' / 'newtonbench' / 'results' / 'source-reach-20261021.json',
                        help='destination JSON output path')
    parser.add_argument('--equation', action='append', choices=EQUATIONS,
                        help='run only selected equation(s); default: all 10')
    parser.add_argument('--variant', action='append', choices=VARIANTS,
                        help='run only selected variant(s); default: both')
    parser.add_argument('--run-cell', nargs=2, metavar=('EQUATION', 'VARIANT'),
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    # Internal worker branch: execute cell and print JSON to stdout
    if args.run_cell:
        eq, var = args.run_cell
        try:
            res = run_cell_worker(eq, var)
        except Exception as exc:
            res = {
                'equation': eq,
                'variant': var,
                'error': f'{type(exc).__name__}: {exc}',
            }
        print(json.dumps(res, ensure_ascii=False))
        return

    selected_equations = [eq for eq in EQUATIONS if (not args.equation or eq in args.equation)]
    selected_variants = [var for var in VARIANTS if (not args.variant or var in args.variant)]

    rows = []
    total_cells = len(selected_equations) * len(selected_variants)
    current_cell = 0

    print(f'Starting source reach probe: {len(selected_equations)} equations x {len(selected_variants)} variants (total {total_cells} cells)...', flush=True)

    for eq in selected_equations:
        for var in selected_variants:
            current_cell += 1
            print(f'[{current_cell}/{total_cells}] Running {eq} ({var})...', end=' ', flush=True)
            row = run_cell(eq, var)
            status_desc = row.get('status') or row.get('error')
            claim_desc = f"claim_exact={row.get('claim_exact')}" if 'claim_exact' in row else ''
            print(f'{status_desc} {claim_desc} in {row.get("wall_seconds")}s', flush=True)
            rows.append(row)
            save_results(args.output, rows)

    print(f'Done. Saved {len(rows)} rows to {args.output}', flush=True)


if __name__ == '__main__':
    main()
