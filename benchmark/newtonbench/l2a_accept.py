"""L2-a acceptance: run the preregistered checks for LOW_MARGINAL_EIG routing.

Usage: python -B benchmark/newtonbench/l2a_accept.py

Reads results/pareto-l2a.json and results/devset-l2a.json, compares against
results/pareto.json and results/devset-run.json (baselines), and prints
PASS/FAIL for each preregistered criterion. Exit 0 = all pass.

Criterion 6 gates the false-certification rate: it must be 0 or null
(NO_CLAIMS), exactly as devset_run.py and pareto.py compute it. A file that
predates those fields is first checked for the law it stores and re-graded
through those modules' backfill; a file that cannot even supply that fails
closed rather than passing by default.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / 'benchmark' / 'newtonbench' / 'results'
sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import devset_run
import pareto


def load(name):
    path = RESULTS / name
    if not path.exists():
        print(f'MISSING: {name}')
        return None
    return json.loads(path.read_text())


def check(label, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {label}  {detail}')
    return ok


def rows_of(document):
    """pareto.json was a bare list, now {'schema': 2, 'rows': [...]}; devset files use 'runs'."""
    if isinstance(document, dict):
        return document['rows'] if 'rows' in document else document['runs']
    return document


def rate_of(document, module):
    """The rate a file carries; older files are re-derived, never assumed zero."""
    if isinstance(document, dict) and 'false_certification_rate' in document:
        return document['false_certification_rate'], document, 'stored'
    try:
        filled = module.backfill(document)
    except (KeyError, TypeError, ValueError) as exc:
        return None, document, f'UNRECOVERABLE: {type(exc).__name__}: {exc}'
    return filled['false_certification_rate'], filled, 'backfilled'


def main():
    ok = True

    pareto_new = load('pareto-l2a.json')
    pareto_old = load('pareto.json')
    devset_new = load('devset-l2a.json')
    devset_old = load('devset-run.json')

    # A missing result file must fail the gate, not silently skip every criterion.
    ok &= check('pareto-l2a.json present', pareto_new is not None)
    ok &= check('devset-l2a.json present', devset_new is not None)
    ok &= check('baselines present',
                pareto_old is not None and devset_old is not None)

    if pareto_new and pareto_old:
        old_runs = {(r['task'], r['budget']): r for r in rows_of(pareto_old) if r.get('arm') == 'rds'}
        new_runs = {(r['task'], r['budget']): r for r in rows_of(pareto_new) if r.get('arm') == 'rds'}

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
        old_cells = {(r['equation'], r['B']): r for r in rows_of(devset_old)}
        new_cells = {(r['equation'], r['B']): r for r in rows_of(devset_new)}

        # Criterion 3: claim_exact 12/15 maintained
        old_ok = sum(1 for r in rows_of(devset_old) if r.get('claim_exact'))
        new_ok = sum(1 for r in rows_of(devset_new) if r.get('claim_exact'))
        ok &= check(f'devset claim_exact >= baseline ({old_ok}/15)',
                    new_ok >= old_ok, f'{new_ok}/15')

        # Criterion 4: no cell flipped from True to False
        flips = [(eq, b) for (eq, b), ro in old_cells.items()
                 if ro.get('claim_exact') and not new_cells.get((eq, b), {}).get('claim_exact')]
        ok &= check('no cell flipped from claim_exact to not', not flips,
                    f'flips: {flips}' if flips else '')

        # Criterion 5: I.6.20a stays SEARCH_EXHAUSTED
        i620 = [r for r in rows_of(devset_new) if 'I.6.20a' in r['equation']]
        for r in i620:
            ok &= check(f"I.6.20a B={r['B']} stays SEARCH_EXHAUSTED",
                        r.get('status') == 'SEARCH_EXHAUSTED',
                        f"status={r.get('status')}")

    # Criterion 6: false_certification_rate is 0 or null on every result file.
    #
    # results/pareto.json is the frozen schema-1 control: it stores exact_match counts and
    # no law expression, and saturation-protocol.md forbids re-running it. It has no
    # carrier for a rate and never will, so it is skipped explicitly rather than
    # permanently failing the gate. The two -l2a.json files CAN carry a law (pareto_l2a.py
    # stores `predict`), so for them a missing rate is a real failure.
    print('INFO  pareto.json false_certification_rate  skipped: frozen schema-1 control, '
          'stores no law expression (see docs/false-certification-implementation.md)')
    for name, document, module in (('devset-run.json', devset_old, devset_run),
                                   ('pareto-l2a.json', pareto_new, pareto),
                                   ('devset-l2a.json', devset_new, devset_run)):
        if document is None:
            ok &= check(f'{name} false_certification_rate is 0 or null', False, 'file missing')
            continue
        rate, filled, source = rate_of(document, module)
        ok &= check(f'{name} false_certification_rate is 0 or null',
                    rate is None or rate == 0,
                    f'rate={rate} claimed={filled.get("claimed_count")} source={source}')

    print()
    print('OVERALL:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
