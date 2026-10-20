"""L2-a acceptance: run the preregistered checks for LOW_MARGINAL_EIG routing.

Usage: python -B benchmark/newtonbench/l2a_accept.py

Reads results/pareto-l2a.json and results/devset-l2a.json, compares against
results/pareto.json and results/devset-run.json (baselines), and prints
PASS/FAIL for each preregistered criterion. Exit 0 = all pass.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / 'benchmark' / 'newtonbench' / 'results'


def load(name):
    path = RESULTS / name
    if not path.exists():
        print(f'MISSING: {name}')
        return None
    return json.loads(path.read_text())


def check(label, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {label}  {detail}')
    return ok


def main():
    ok = True

    pareto_new = load('pareto-l2a.json')
    pareto_old = load('pareto.json')
    devset_new = load('devset-l2a.json')
    devset_old = load('devset-run.json')

    if pareto_new and pareto_old:
        old_runs = {(r['world'], r['budget']): r for r in pareto_old['runs']}
        new_runs = {(r['world'], r['budget']): r for r in pareto_new['runs']}

        # Criterion 1: regime-double B=24 and B=32 experiments must not increase
        for b in (24, 32):
            key = ('regime-double', b)
            if key in old_runs and key in new_runs:
                old_exp = old_runs[key]['experiments']
                new_exp = new_runs[key]['experiments']
                ok &= check(f'regime-double B={b} experiments not increased',
                            new_exp <= old_exp, f'{old_exp} -> {new_exp}')

        # Criterion 2: regime-double all budgets claim_exact 32/32
        for b in (12, 16, 24, 32):
            key = ('regime-double', b)
            if key in new_runs:
                r = new_runs[key]
                ok &= check(f'regime-double B={b} claim_exact 32/32',
                            r.get('claim_exact') == 32 and r.get('exact_match') == 32,
                            f"status={r.get('status')}")

    if devset_new and devset_old:
        old_cells = {(r['equation'], r['B']): r for r in devset_old['runs']}
        new_cells = {(r['equation'], r['B']): r for r in devset_new['runs']}

        # Criterion 3: claim_exact 12/15 maintained
        old_ok = sum(1 for r in devset_old['runs'] if r.get('claim_exact'))
        new_ok = sum(1 for r in devset_new['runs'] if r.get('claim_exact'))
        ok &= check(f'devset claim_exact >= baseline ({old_ok}/15)',
                    new_ok >= old_ok, f'{new_ok}/15')

        # Criterion 4: no cell flipped from True to False
        flips = [(eq, b) for (eq, b), ro in old_cells.items()
                 if ro.get('claim_exact') and not new_cells.get((eq, b), {}).get('claim_exact')]
        ok &= check('no cell flipped from claim_exact to not', not flips,
                    f'flips: {flips}' if flips else '')

        # Criterion 5: I.6.20a stays SEARCH_EXHAUSTED
        i620 = [r for r in devset_new['runs'] if 'I.6.20a' in r['equation']]
        for r in i620:
            ok &= check(f"I.6.20a B={r['B']} stays SEARCH_EXHAUSTED",
                        r.get('status') == 'SEARCH_EXHAUSTED',
                        f"status={r.get('status')}")

    print()
    print('OVERALL:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
